"""OneBot v11 reverse WebSocket transport (NapCat / Lagrange / go-cqhttp).
Legacy option — the primary QQ path is bot_qq.py (official AppID/Secret).
Point reverse-WS to ws://<host>:8400/onebot. Config in botconf.json:
{"enabled":true,"access_token":"","require_at_in_group":true}
"""
import asyncio, json, os, time, uuid
from fastapi import WebSocketDisconnect

import botcore

CONF_FILE = os.environ.get("GENV_BOTCONF", "botconf.json")
_DEFAULT = {"enabled": True, "access_token": "", "require_at_in_group": True}
SESSIONS = {}  # self_id -> {sess, connected_at, last_event, app}


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
    now = time.time()
    return {"enabled": get_config().get("enabled", True),
            "clients": [{"self_id": sid, "app": s.get("app"),
                         "connected_at": s["connected_at"],
                         "last_event": s.get("last_event"),
                         "age": int(now - s["connected_at"])}
                        for sid, s in SESSIONS.items()]}


async def test_send(target_type, target_id, message):
    if not SESSIONS:
        return False, "no bot client connected"
    sid, s = next(iter(SESSIONS.items()))
    p = {"message": message}
    if target_type == "group":
        p.update(message_type="group", group_id=int(target_id))
    else:
        p.update(message_type="private", user_id=int(target_id))
    try:
        await s["sess"].api("send_msg", p)
        return True, f"sent via bot {sid}"
    except Exception as e:
        return False, str(e)


def _text_of(msg):
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        return "".join(s.get("data", {}).get("text", "") for s in msg
                       if s.get("type") == "text").strip()
    return ""


def _at_ids(msg):
    if isinstance(msg, list):
        return {str(s.get("data", {}).get("qq")) for s in msg if s.get("type") == "at"}
    return set()


class Session:
    def __init__(self, ws):
        self.ws = ws
        self._send_lock = asyncio.Lock()

    async def api(self, action, params):
        async with self._send_lock:
            await self.ws.send_json({"action": action, "params": params,
                                     "echo": str(uuid.uuid4())[:8]})

    async def say(self, ev, text):
        p = {"message": text}
        if ev.get("message_type") == "group":
            p.update(message_type="group", group_id=ev.get("group_id"))
        else:
            p.update(message_type="private", user_id=ev.get("user_id"))
        await self.api("send_msg", p)


async def dispatch(sess, ev, provider, JOBS, cfg):
    if ev.get("post_type") != "message":
        return
    is_group = ev.get("message_type") == "group"
    if is_group and cfg.get("require_at_in_group", True) and \
            str(ev.get("self_id", "")) not in _at_ids(ev.get("message")):
        return
    text = _text_of(ev.get("message"))
    if not text:
        return
    await botcore.run_command(text, provider, JOBS,
                              lambda t: sess.say(ev, t))


async def serve(ws, provider, JOBS, video_obj=None, save_jobs=None):
    await ws.accept()
    cfg = get_config()
    token = cfg.get("access_token") or os.environ.get("ONEBOT_TOKEN") or ""
    if token:
        auth = ws.headers.get("authorization", "").replace("Bearer ", "").strip()
        at = ws.query_params.get("access_token", "")
        if token not in (auth, at):
            await ws.close(code=4401)
            return
    if not cfg.get("enabled", True):
        await ws.close(code=4403)
        return
    sess = Session(ws)
    sid = None
    try:
        while True:
            data = await ws.receive_json()
            ev_sid = str(data.get("self_id") or "")
            if ev_sid:
                sid = ev_sid
                rec = SESSIONS.setdefault(sid, {"sess": sess,
                                                "connected_at": time.time(),
                                                "app": None})
                rec["sess"] = sess
                rec["last_event"] = time.time()
            await dispatch(sess, data, provider, JOBS, get_config())
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        if sid and SESSIONS.get(sid, {}).get("sess") is sess:
            SESSIONS.pop(sid, None)
