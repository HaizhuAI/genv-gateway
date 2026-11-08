"""Official QQ bot (QQ开放平台) gateway client — AppID + AppSecret only.

Flow: getAppAccessToken -> /gateway -> wss -> identify -> events.
Handles GROUP_AT_MESSAGE_CREATE (群@), C2C_MESSAGE_CREATE (私聊),
AT_MESSAGE_CREATE (频道@), DIRECT_MESSAGE_CREATE (频道私信).

Config qqconf.json: {"enabled":true,"appid":"","appsecret":"","sandbox":false}
Manage via WebUI or PUT /admin/bot/config. No per-QQ/group config needed —
the bot answers whoever @s/messages it.
"""
import asyncio, json, os, time
import httpx, websockets

import botcore

CONF_FILE = os.environ.get("GENV_QQCONF", "qqconf.json")
API = "https://api.sgroup.qq.com"
API_SANDBOX = "https://sandbox.api.sgroup.qq.com"
TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
INTENTS = (1 << 30) | (1 << 25) | (1 << 12)  # PUBLIC_GUILD + GROUP/C2C + DM

_DEFAULT = {"enabled": True, "appid": "", "appsecret": "", "sandbox": False}
_state = {"connected": False, "bot": None, "session_id": None,
          "last_event": None, "error": None, "reconnects": 0}


def get_config():
    try:
        return {**_DEFAULT, **json.load(open(CONF_FILE))}
    except OSError:
        return dict(_DEFAULT)


def set_config(**kw):
    c = get_config()
    for k in _DEFAULT:
        if k in kw and kw[k] is not None:
            c[k] = kw[k]
    json.dump(c, open(CONF_FILE, "w"), indent=1)
    return c


def status():
    return {"enabled": get_config().get("enabled", True),
            "appid_set": bool(get_config().get("appid")),
            "sandbox": get_config().get("sandbox", False),
            **_state}


async def test_credentials():
    """Verify AppID+Secret -> token -> gateway url. No message sent."""
    cfg = get_config()
    if not cfg.get("appid") or not cfg.get("appsecret"):
        return False, "appid/appsecret 未配置"
    try:
        tok, _ = await _token(cfg)
        api = API_SANDBOX if cfg.get("sandbox") else API
        async with httpx.AsyncClient(timeout=15) as h:
            r = await h.get(api + "/gateway",
                            headers={"Authorization": f"QQBot {tok}"})
            if r.status_code == 200:
                return True, f"凭证有效，gateway: {r.json().get('url', '?')[:60]}"
            return False, f"gateway {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, str(e)[:300]


async def _token(cfg):
    async with httpx.AsyncClient(timeout=15) as h:
        r = await h.post(TOKEN_URL, json={"appId": cfg["appid"],
                                        "clientSecret": cfg["appsecret"]})
        d = r.json()
        if "access_token" not in d:
            raise RuntimeError(f"token: {r.text[:200]}")
        return d["access_token"], int(d.get("expires_in", 7200))


class QQBot:
    def __init__(self, provider, JOBS):
        self.provider, self.JOBS = provider, JOBS
        self.tok, self.tok_at = None, 0.0
        self.msg_seq = 0

    async def token(self, cfg):
        if not self.tok or time.time() > self.tok_at:
            self.tok, ttl = await _token(cfg)
            self.tok_at = time.time() + ttl * 0.8
        return self.tok

    async def reply(self, api, ev, text):
        """Reply to whatever event `ev` was (group/c2c/channel/dm)."""
        self.msg_seq += 1
        t, d = ev["t"], ev["d"]
        body = {"content": text, "msg_type": 0,
                "msg_id": d.get("id"), "msg_seq": self.msg_seq}
        if t == "GROUP_AT_MESSAGE_CREATE":
            url = f"{api}/v2/groups/{d['group_openid']}/messages"
        elif t == "C2C_MESSAGE_CREATE":
            url = f"{api}/v2/users/{d['author']['user_openid']}/messages"
        elif t == "AT_MESSAGE_CREATE":
            url = f"{api}/channels/{d['channel_id']}/messages"
            body = {"content": text, "msg_id": d.get("id")}
        elif t == "DIRECT_MESSAGE_CREATE":
            url = f"{api}/dms/{d['guild_id']}/messages"
            body = {"content": text, "msg_id": d.get("id")}
        else:
            return
        try:
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.post(url, json=body, headers={
                    "Authorization": f"QQBot {self.tok}"})
                if r.status_code not in (200, 201, 202):
                    print(f"[qqbot] reply {r.status_code}: {r.text[:200]}", flush=True)
        except Exception as e:
            print("[qqbot] reply err:", e, flush=True)

    async def on_event(self, ws, ev, api):
        t = ev.get("t")
        if t == "READY":
            d = ev.get("d") or {}
            _state.update(connected=True, session_id=d.get("session_id"),
                          bot=d.get("user"), error=None)
            print(f"[qqbot] READY as {d.get('user')}", flush=True)
            return
        if t not in ("GROUP_AT_MESSAGE_CREATE", "C2C_MESSAGE_CREATE",
                     "AT_MESSAGE_CREATE", "DIRECT_MESSAGE_CREATE"):
            return
        _state["last_event"] = time.time()
        content = (ev["d"].get("content") or "").strip()
        if not content:
            return

        async def say(text):
            await self.reply(api, ev, text[:1900])

        asyncio.create_task(botcore.run_command(content, self.provider,
                                                self.JOBS, say))


async def run(provider, JOBS):
    """Supervisor loop: reconnects on drop, picks up config changes."""
    bot = QQBot(provider, JOBS)
    seq, session_id = None, None
    while True:
        cfg = get_config()
        if not cfg.get("enabled") or not cfg.get("appid") or not cfg.get("appsecret"):
            _state["connected"] = False
            await asyncio.sleep(10)
            continue
        api = API_SANDBOX if cfg.get("sandbox") else API
        try:
            tok = await bot.token(cfg)
            async with httpx.AsyncClient(timeout=15) as h:
                r = await h.get(api + "/gateway",
                                headers={"Authorization": f"QQBot {tok}"})
                url = r.json()["url"]
            async with websockets.connect(url, ping_interval=None,
                                          max_size=8 * 1024 * 1024) as ws:
                hello = json.loads(await ws.recv())
                hb = hello["d"]["heartbeat_interval"] / 1000
                if session_id and seq:
                    await ws.send(json.dumps(
                        {"op": 6, "d": {"token": f"QQBot {tok}",
                                        "session_id": session_id, "seq": seq}}))
                else:
                    await ws.send(json.dumps(
                        {"op": 2, "d": {"token": f"QQBot {tok}",
                                        "intents": INTENTS, "shard": [0, 1],
                                        "properties": {}}}))
                last_hb = time.time()

                async def heartbeat():
                    while True:
                        await asyncio.sleep(max(5, hb * 0.9))
                        await ws.send(json.dumps({"op": 1, "d": seq}))

                hb_task = asyncio.create_task(heartbeat())
                try:
                    async for raw in ws:
                        try:
                            pkt = json.loads(raw)
                        except Exception:
                            continue
                        if pkt.get("s"):
                            seq = pkt["s"]
                        op = pkt.get("op")
                        if op == 0:
                            if pkt.get("t") == "READY":
                                session_id = (pkt.get("d") or {}).get("session_id")
                            asyncio.create_task(bot.on_event(ws, pkt, api))
                        elif op == 9:      # invalid session
                            session_id, seq = None, None
                            break
                        elif op == 7:      # reconnect requested
                            break
                        elif op == 11:
                            pass
                finally:
                    hb_task.cancel()
            _state.update(connected=False, reconnects=_state["reconnects"] + 1)
            await asyncio.sleep(5)
        except Exception as e:
            _state.update(connected=False, error=str(e)[:300],
                          reconnects=_state["reconnects"] + 1)
            session_id, seq = None, None
            await asyncio.sleep(15)
