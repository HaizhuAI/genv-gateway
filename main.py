"""OpenAI-compatible gateway -> SnapGen upstream (api.snapgen.ai/api).

Endpoints:
  GET  /v1/models
  POST /v1/videos               {model, prompt, size?, seconds?}         (OpenAI video API)
  GET  /v1/videos/{id}
  GET  /v1/videos/{id}/content  -> redirect to generated file
  POST /v1/images/generations   {model, prompt, size?, n?}               (OpenAI images API)
  POST /v1/chat/completions     shim: last user message -> media URL text
  GET  /v1/quota                upstream quota dump (extension)

Extensions (non-standard, optional):
  request field `turnstile_token` or header `X-Turnstile-Token`  — required for non-vela models
  field `resolution`, `duration`, `aspect_ratio`/`orientation`, `num_result`
  header `X-Wait: <seconds>` — server-side poll until done/timeout before responding
  field `image`/`images`/`file_urls` — reference images (URLs)
"""
import os, time, uuid, asyncio, json
from fastapi import FastAPI, Request, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from provider import SnapGenProvider, UpstreamError, STATUS_DONE, STATUS_FAILED
import models as M
import keystore
import usage
import bot_onebot
import bot_qq

app = FastAPI(title="snapgen-openai-gateway")
provider = SnapGenProvider()

JOBS = {}  # job_id -> {"uuid","type","model","created","attempts","history"}
JOBS_FILE = "gateway_jobs.json"

RESUBMIT_MAX = int(os.environ.get("GENV_RESUBMIT", "3"))
MASTER_KEY = os.environ.get("GATEWAY_API_KEY")  # admin/master key

REFILL_TASK = None


def _err(status, msg, typ="server_error", code=None):
    return JSONResponse(status_code=status,
                        content={"error": {"message": msg, "type": typ, "code": code}})


def _any_keys():
    return bool(MASTER_KEY or keystore.list_keys())


def _cookie_admin(request):
    return bool(MASTER_KEY) and request.cookies.get("gw_admin") == MASTER_KEY


@app.middleware("http")
async def auth_mw(request, call_next):
    """Bearer-key auth for /v1/* (model scope checked inside handlers).
    WebUI cookie login (gw_admin=master) also counts as master.
    Open when no keys configured at all."""
    if request.url.path.startswith("/v1") and _any_keys() and not _cookie_admin(request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        request.state.gw_token = token
        # validate existence/revocation only; model scope checked per-handler
        ok = bool(token) and (token == MASTER_KEY or any(
            k["key"] == token and not k["revoked"] for k in keystore.list_keys()))
        if not ok:
            return _err(401, "invalid api key", "authentication_error", "invalid_api_key")
    return await call_next(request)


def _check_model(request, mid):
    """Per-model scope + limit check for the caller's key."""
    if _cookie_admin(request):
        return None
    token = getattr(request.state, "gw_token", None) or (
        request.headers.get("authorization", "").removeprefix("Bearer ").strip())
    ok, why = keystore.authenticate(token, model=mid)
    if not ok:
        return _err(403, why, "permission_error", "forbidden")
    return None


def _charge(request):
    token = getattr(request.state, "gw_token", None)
    if token:
        keystore.charge(token)


def _is_admin(request):
    if _cookie_admin(request):
        return True
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    return bool(MASTER_KEY) and token == MASTER_KEY or not MASTER_KEY


def _save_jobs():
    try:
        json.dump(JOBS, open(JOBS_FILE, "w"), default=str)
    except OSError:
        pass


def _load_jobs():
    try:
        JOBS.update(json.load(open(JOBS_FILE)))
    except OSError:
        pass


_load_jobs()


# ---------------- models ----------------
@app.get("/v1/models")
async def list_models():
    return {"object": "list", "data": M.list_models()}


@app.get("/v1/quota")
async def quota():
    try:
        return await provider.quota()
    except UpstreamError as e:
        return _err(e.status, e.message, code=e.code)


# ---------------- helpers ----------------
def _turnstile(body: dict, hdr: str | None):
    return body.get("turnstile_token") or hdr


def _result_media(hist: dict):
    vids = hist.get("generated_video") or []
    imgs = hist.get("generated_image") or []
    if vids:
        v = vids[0]
        return "video", v.get("video_url") or v.get("file_download_url")
    if imgs:
        i = imgs[0]
        return "image", i.get("image_url") or i.get("file_download_url")
    return hist.get("media_type"), None


def _video_object(job_id, hist=None):
    j = JOBS[job_id]
    h = hist or j.get("history") or {}
    st = h.get("status")
    out = {
        "id": job_id, "object": "video", "model": j["model"],
        "created_at": int(j["created"]),
        "status": {None: "queued", -1: "queued", 0: "queued", 1: "in_progress",
                   2: "completed", 3: "failed"}.get(st, "in_progress"),
        "progress": h.get("status_percentage") or 0,
        "upstream_uuid": j.get("uuid"),
    }
    if st == STATUS_DONE:
        _, url = _result_media(h)
        out["url"] = url
        out["expires_at"] = h.get("expired_at")
    if st == STATUS_FAILED:
        out["error"] = {"code": h.get("error_code"), "message": h.get("error_message")}
    return out


async def _submit_job(spec, fields):
    if spec.get("channel") == "mobile":
        return await provider.mobile_submit(spec["endpoint"], fields)
    return await provider.submit(spec["endpoint"], fields)


async def _history(job):
    if job.get("channel") == "mobile":
        return await provider.mobile_history(job["uuid"])
    return await provider.history(job["uuid"])


async def _poll_until_done(job_id, timeout):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            hist = await _history(JOBS[job_id])
        except UpstreamError:
            await asyncio.sleep(5)
            continue
        JOBS[job_id]["history"] = hist
        if hist.get("status") == STATUS_DONE:
            return hist
        if hist.get("status") == STATUS_FAILED:
            # transient congestion -> resubmit same job
            if hist.get("error_code") == "HIGH_TRAFFIC_ERROR" and JOBS[job_id]["attempts"] < RESUBMIT_MAX:
                JOBS[job_id]["attempts"] += 1
                await asyncio.sleep(15)
                try:
                    d = await _submit_job({"channel": JOBS[job_id].get("channel"),
                                           "endpoint": JOBS[job_id]["endpoint"]},
                                          JOBS[job_id]["fields"])
                    JOBS[job_id]["uuid"] = d["uuid"]
                    JOBS[job_id]["history"] = d
                    continue
                except UpstreamError:
                    pass
            return hist
        await asyncio.sleep(10)
    return JOBS[job_id].get("history")


# ---------------- videos (OpenAI video API) ----------------
@app.post("/v1/videos")
async def create_video(request: Request, x_turnstile_token: str | None = Header(default=None),
                       x_wait: str | None = Header(default=None)):
    body = await request.json()
    mid = body.get("model", "muse-video")
    spec = M.VIDEO_MODELS.get(mid)
    if not spec:
        return _err(400, f"unknown video model '{mid}'", "invalid_request_error", "model_not_found")
    deny = _check_model(request, mid)
    if deny:
        return deny
    if spec.get("turnstile") and not _turnstile(body, x_turnstile_token):
        return _err(400, f"model '{mid}' requires turnstile_token (field or X-Turnstile-Token header)",
                  "invalid_request_error", "turnstile_required")
    prompt = body.get("prompt")
    if not prompt:
        return _err(400, "prompt is required", "invalid_request_error", "missing_prompt")

    seconds = int(body.get("seconds") or body.get("duration") or spec["durations"][0])
    if seconds not in spec["durations"]:
        seconds = min(spec["durations"], key=lambda d: abs(d - seconds))
    orient = M.orientation_for(body.get("size") or body.get("orientation") or body.get("aspect_ratio"),
                               spec["orientations"])
    if spec.get("ratio_literal"):
        orient = M.ratio_for(body.get("size") or body.get("aspect_ratio") or orient)
    ofield = spec.get("orientation_field", "orientation")
    resolution = body.get("resolution")
    if spec.get("channel") == "mobile" and resolution is None:
        resolution = sorted(spec["resolutions"])[0] if spec.get("resolutions") else ""
    fields = dict(spec.get("extra") or {})
    fields.update({
        "prompt": prompt,
        "model": spec["model"],
        "duration": str(seconds),
        ofield: orient,
        "mode": spec.get("mode") or body.get("mode"),
        "resolution": resolution,
        "service_mode": body.get("service_mode") or (spec.get("extra") or {}).get("service_mode"),
        "turnstile_token": _turnstile(body, x_turnstile_token),
        spec.get("image_field", "files"): body.get("files"),
        "file_urls": body.get("file_urls") or body.get("images"),
    })
    try:
        d = await _submit_job(spec, fields)
    except UpstreamError as e:
        return _err(e.status if e.status < 500 else 502, e.message, code=e.code)
    _charge(request)
    job_id = "video_" + uuid.uuid4().hex[:24]
    JOBS[job_id] = {"uuid": d.get("uuid"), "type": "video", "model": mid,
                    "channel": spec.get("channel", "web"),
                    "endpoint": spec["endpoint"], "fields": fields,
                    "created": time.time(), "attempts": 1, "history": d}
    _save_jobs()
    if x_wait:
        await _poll_until_done(job_id, min(int(x_wait), 600))
        _save_jobs()
    return _video_object(job_id)


@app.get("/v1/videos/{job_id}")
async def get_video(job_id: str):
    if job_id not in JOBS:
        return _err(404, "video not found", "invalid_request_error", "not_found")
    try:
        hist = await _history(JOBS[job_id])
        JOBS[job_id]["history"] = hist
        _save_jobs()
    except UpstreamError as e:
        return _err(e.status, e.message, code=e.code)
    return _video_object(job_id, hist)


@app.get("/v1/videos/{job_id}/content")
async def video_content(job_id: str):
    if job_id not in JOBS:
        return _err(404, "video not found", "invalid_request_error", "not_found")
    hist = await _history(JOBS[job_id])
    JOBS[job_id]["history"] = hist
    if hist.get("status") != STATUS_DONE:
        return _err(409, f"video not ready (status={hist.get('status')})", "invalid_request_error", "not_ready")
    _, url = _result_media(hist)
    if not url:
        return _err(404, "no media url", "invalid_request_error", "not_found")
    return RedirectResponse(url)


# ---------------- images (OpenAI images API) ----------------
@app.post("/v1/images/generations")
async def create_image(request: Request, x_turnstile_token: str | None = Header(default=None),
                       x_wait: str | None = Header(default=None)):
    body = await request.json()
    mid = body.get("model", "nano-banana-2")
    spec = M.IMAGE_MODELS.get(mid)
    if not spec:
        return _err(400, f"unknown image model '{mid}'", "invalid_request_error", "model_not_found")
    deny = _check_model(request, mid)
    if deny:
        return deny
    if spec.get("turnstile") and not _turnstile(body, x_turnstile_token):
        return _err(400, f"model '{mid}' requires turnstile_token (field or X-Turnstile-Token header)",
                  "invalid_request_error", "turnstile_required")
    prompt = body.get("prompt")
    if not prompt:
        return _err(400, "prompt is required", "invalid_request_error", "missing_prompt")

    fields = dict(spec.get("extra") or {})
    fields["prompt"] = prompt
    if spec.get("ratio_literal"):
        fields[spec["orientation_field"]] = M.ratio_for(body.get("size") or body.get("aspect_ratio"))
    else:
        fields[spec["orientation_field"]] = M.orientation_for(body.get("size") or body.get("aspect_ratio"), None)
    if spec.get("model"):
        fields["model"] = spec["model"]
    fields["resolution"] = body.get("resolution") or (
        sorted(spec["resolutions"])[0] if spec.get("resolutions") else None)
    fields["output_format"] = body.get("output_format")
    fields["turnstile_token"] = _turnstile(body, x_turnstile_token)
    fields["files"] = body.get("files")
    fields["file_urls"] = body.get("file_urls") or body.get("images")
    fields["num_result"] = body.get("num_result")

    try:
        d = await _submit_job(spec, fields)
    except UpstreamError as e:
        return _err(e.status if e.status < 500 else 502, e.message, code=e.code)
    _charge(request)
    job_id = "img_" + uuid.uuid4().hex[:24]
    JOBS[job_id] = {"uuid": d.get("uuid"), "type": "image", "model": mid,
                    "channel": spec.get("channel", "web"),
                    "endpoint": spec["endpoint"], "fields": fields,
                    "created": time.time(), "attempts": 1, "history": d}
    _save_jobs()
    hist = await _poll_until_done(job_id, min(int(x_wait or 300), 600))
    _save_jobs()
    if not hist or hist.get("status") != STATUS_DONE:
        st = (hist or {}).get("status")
        return _err(502, f"image generation did not complete (status={st}, err={(hist or {}).get('error_code')})",
                  code=(hist or {}).get("error_code"))
    imgs = hist.get("generated_image") or []
    data = [{"url": i.get("image_url") or i.get("file_download_url")} for i in imgs]
    if body.get("response_format") == "b64_json" and hist.get("base64_images"):
        data = [{"b64_json": hist["base64_images"]}]
    return {"created": int(time.time()), "data": data}


async def _gen_payload(mid, prompt, body, ts_token):
    """Shared submit+poll path used by chat shim. Returns result dict."""
    if mid in M.IMAGE_MODELS:
        spec = M.IMAGE_MODELS[mid]
        fields = dict(spec.get("extra") or {})
        fields["prompt"] = prompt
        fields[spec["orientation_field"]] = M.orientation_for(body.get("size"), None)
        if spec.get("model"):
            fields["model"] = spec["model"]
        fields["turnstile_token"] = ts_token or body.get("turnstile_token")
        try:
            d = await _submit_job(spec, fields)
        except UpstreamError as e:
            return {"error": {"code": e.code, "message": e.message}}
        job_id = "img_" + uuid.uuid4().hex[:24]
        JOBS[job_id] = {"uuid": d.get("uuid"), "type": "image", "model": mid,
                        "channel": spec.get("channel", "web"),
                        "endpoint": spec["endpoint"], "fields": fields,
                        "created": time.time(), "attempts": 1, "history": d}
    else:
        spec = M.VIDEO_MODELS.get(mid, M.VIDEO_MODELS["muse-video"])
        fields = dict(spec.get("extra") or {})
        fields.update({"prompt": prompt, "model": spec["model"],
                  "duration": str(spec["durations"][0]),
                  spec.get("orientation_field", "orientation"): "square",
                  "resolution": "" if spec.get("channel") == "mobile" else None,
                  "mode": spec.get("mode"),
                  "turnstile_token": ts_token or body.get("turnstile_token")})
        try:
            d = await _submit_job(spec, fields)
        except UpstreamError as e:
            return {"error": {"code": e.code, "message": e.message}}
        job_id = "video_" + uuid.uuid4().hex[:24]
        JOBS[job_id] = {"uuid": d.get("uuid"), "type": "video", "model": mid,
                        "channel": spec.get("channel", "web"),
                        "endpoint": spec["endpoint"], "fields": fields,
                        "created": time.time(), "attempts": 1, "history": d}
    hist = await _poll_until_done(job_id, 300)
    JOBS[job_id]["history"] = hist or JOBS[job_id]["history"]
    _save_jobs()
    obj = _video_object(job_id)
    if mid in M.IMAGE_MODELS and (hist or {}).get("status") == STATUS_DONE:
        imgs = hist.get("generated_image") or []
        obj["data"] = [{"url": i.get("image_url") or i.get("file_download_url")} for i in imgs]
    return obj


# ---------------- chat shim ----------------
@app.post("/v1/chat/completions")
async def chat_completions(request: Request, x_turnstile_token: str | None = Header(default=None)):
    body = await request.json()
    msgs = body.get("messages") or []
    prompt = ""
    for m in reversed(msgs):
        if m.get("role") == "user":
            c = m.get("content")
            prompt = c if isinstance(c, str) else " ".join(
                p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text")
            break
    if not prompt:
        return _err(400, "no user message", "invalid_request_error", "missing_prompt")
    mid = body.get("model", "muse-video")
    deny = _check_model(request, mid)
    if deny:
        return deny
    payload = await _gen_payload(mid, prompt, body, x_turnstile_token)
    if "error" not in payload:
        _charge(request)
    content = payload.get("url") or payload.get("data", [{}])[0].get("url") \
        or f"job {payload.get('id', '?')} status={payload.get('status', payload.get('error', {}).get('code'))} — poll GET /v1/videos/{payload.get('id')}"
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex[:20], "object": "chat.completion",
        "created": int(time.time()), "model": mid,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "x_job": payload,
    }


@app.get("/v1/jobs")
async def list_jobs(limit: int = 50):
    items = sorted(JOBS.items(), key=lambda kv: -kv[1]["created"])[:limit]
    return {"data": [_video_object(jid) for jid, _ in items]}


# ---------------- admin: pool / keys / policy ----------------
@app.get("/admin/state")
async def admin_state(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    stats = usage.account_stats(provider.app_accounts)
    quota = {}
    try:
        quota = await provider.mobile_quota()
    except UpstreamError:
        pass
    return {
        "accounts_total": len(provider.app_accounts),
        "accounts_usable": len(usage.usable_app_accounts(provider.app_accounts)),
        "accounts": stats,
        "policy": usage.policy(),
        "keys": [{**k, "key": k["key"][:8] + "…"} for k in keystore.list_keys()],
        "quota": quota,
        "jobs": len(JOBS),
        "free_gens_per_account": usage.FREE_GENS_PER_ACCOUNT,
        "master_key_set": bool(MASTER_KEY),
    }


@app.post("/admin/pool/refill")
async def admin_refill(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    body = await request.json() if request.headers.get("content-type") else {}
    n = int(body.get("n") or 3)
    made = []
    for _ in range(min(n, 10)):
        try:
            rec = await provider.provision_app_account()
            made.append(rec["email"])
        except Exception as e:
            return {"created": made, "stopped": str(e)[:200]}
    return {"created": made, "pool": len(provider.app_accounts),
            "usable": len(usage.usable_app_accounts(provider.app_accounts))}


@app.get("/admin/pool/policy")
async def admin_policy_get(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    return usage.policy()


@app.put("/admin/pool/policy")
async def admin_policy_put(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    body = await request.json()
    return usage.set_policy(min_usable=body.get("min_usable"),
                            refill_batch=body.get("refill_batch"),
                            auto=body.get("auto"))


# ---------------- admin: api keys ----------------
@app.get("/admin/keys")
async def keys_list(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    return {"data": [{**k, "key": k["key"]} for k in keystore.list_keys()]}


@app.post("/admin/keys")
async def keys_create(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    body = await request.json() if request.headers.get("content-type") else {}
    rec = keystore.create_key(name=body.get("name"),
                              models=body.get("models", "*"),
                              limit=body.get("limit"))
    return rec


@app.delete("/admin/keys/{kid}")
async def keys_delete(kid: str, request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    if keystore.delete_key(kid):
        return {"deleted": kid}
    return _err(404, "key not found", "invalid_request_error", "not_found")


# ---------------- web ui (admin-password gated) ----------------
_LOGIN_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>GenV Gateway</title>
<style>body{background:#0b0e14;color:#e6eaf2;font:14px -apple-system,sans-serif;display:flex;
min-height:100vh;align-items:center;justify-content:center;margin:0}
.c{background:#141925;border:1px solid #232a3a;border-radius:12px;padding:28px;width:320px}
input{width:100%;box-sizing:border-box;background:#0d1120;border:1px solid #232a3a;color:#e6eaf2;
border-radius:8px;padding:10px;margin:8px 0 14px}
button{width:100%;background:#6c5ce7;color:#fff;border:0;border-radius:8px;padding:10px;cursor:pointer}
.e{color:#ff5470;font-size:12px;min-height:16px}</style></head><body><div class="c">
<h3>GenV Gateway</h3><form onsubmit="go();return false">
<input id="p" type="password" placeholder="管理密码" autofocus>
<div class="e" id="e"></div><button>进入</button></form>
<script>async function go(){try{const r=await fetch('/admin/login',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({password:p.value})});
if(r.ok){location='/'}else{e.textContent='密码错误'}}catch(x){e.textContent='网络错误'}}</script>
</div></body></html>"""


def _ui_ok(request: Request):
    """WebUI access: open when no master key configured, else cookie must match."""
    if not MASTER_KEY:
        return True
    return request.cookies.get("gw_admin") == MASTER_KEY


@app.post("/admin/login")
async def admin_login(request: Request):
    body = await request.json()
    if not MASTER_KEY or body.get("password") == MASTER_KEY:
        r = JSONResponse({"ok": True})
        if MASTER_KEY:
            r.set_cookie("gw_admin", MASTER_KEY, httponly=True, samesite="lax")
        return r
    return _err(401, "wrong password", "authentication_error", "invalid_api_key")


@app.get("/")
async def root(request: Request):
    if not _ui_ok(request):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_LOGIN_HTML)
    return FileResponse(os.path.join(os.path.dirname(__file__), "webui.html"))


@app.get("/ui")
async def webui(request: Request):
    if not _ui_ok(request):
        from fastapi.responses import HTMLResponse
        return HTMLResponse(_LOGIN_HTML)
    return FileResponse(os.path.join(os.path.dirname(__file__), "webui.html"))


# ---------------- admin: QQ bot (official AppID/Secret) ----------------
@app.get("/admin/bot/config")
async def bot_cfg_get(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    c = bot_qq.get_config()
    return {**c, "appsecret": "•••" if c.get("appsecret") else ""}


@app.put("/admin/bot/config")
async def bot_cfg_put(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    body = await request.json()
    if body.get("appsecret") == "•••":
        body.pop("appsecret")  # keep existing
    return bot_qq.set_config(**body)


@app.get("/admin/bot/status")
async def bot_status(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    return {"qq": bot_qq.status(), "onebot": bot_onebot.status()}


@app.post("/admin/bot/test")
async def bot_test(request: Request):
    if not _is_admin(request):
        return _err(403, "admin key required", "permission_error", "forbidden")
    ok, msg = await bot_qq.test_credentials()
    return {"ok": ok, "message": msg}


# ---------------- QQ bot (OneBot v11 reverse ws) ----------------
@app.websocket("/onebot")
async def onebot_ws(ws: WebSocket):
    await bot_onebot.serve(ws, provider, JOBS, _video_object, _save_jobs)


# ---------------- background: reserve-pool auto refill ----------------
async def _refill_loop():
    while True:
        await asyncio.sleep(60)
        need, usable, pol = usage.needs_refill(provider.app_accounts)
        if not need:
            continue
        want = min(pol["refill_batch"], pol["min_usable"] - usable)
        for _ in range(want):
            try:
                rec = await provider.provision_app_account()
                print(f"[pool] auto-provisioned {rec['email']} "
                      f"(usable was {usable})", flush=True)
            except Exception as e:
                print("[pool] provision failed:", e, flush=True)
                break


@app.on_event("startup")
async def _startup():
    global REFILL_TASK
    REFILL_TASK = asyncio.create_task(_refill_loop())
    asyncio.create_task(bot_qq.run(provider, JOBS))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8400")))
