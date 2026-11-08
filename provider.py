"""SnapGen/GenV upstream provider client.

Request chain (reverse-engineered from snapgen.ai Nuxt bundle + live APK traffic):

WEB channel:
  POST https://api.geminigen.ai/mobile/v1/login {username,password} -> access_token
  Every web API call:  https://api.snapgen.ai/api<path>
      headers: Authorization: Bearer <token>, x-guard-id: <guard_token(path,method,sid)>
  Poll:                GET  /api/history/{uuid}         -> status 2=done 3=failed

MOBILE channel (captured from the real app):
  login:  POST /mobile/v1/login {username,password,platform:"GenV-APP"}
          — the platform field selects the APP credential store; accounts must be
          created with platform="GenV-APP" at signup or login fails.
  calls:  https://api.geminigen.ai/mobile/...  headers:
          Authorization: Bearer <jwt>, x-app-version: genv-android/3.7.2,
          User-Agent: Dart/3.12 (dart:io)
          x-token/x-timestamp seen on /mobile/v1/* are NOT verified server-side;
          /mobile/v4/* needs none of them.
  Poll:   GET /mobile/v1/history/{uuid}         -> same status codes
  Quota:  GET /mobile/v1/user/quota/models      -> per-model remaining counts
"""
import json, os, random, string, time
import httpx
import usage
from guard import guard_token, gen_stable_id, sync_time

AUTH_API = os.environ.get("GENV_AUTH_API", "https://api.geminigen.ai")
WEB_API = os.environ.get("GENV_WEB_API", "https://api.snapgen.ai/api")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
MOBILE_UA = "Dart/3.12 (dart:io)"
APP_VERSION = "genv-android/3.7.2"
APP_PLATFORM = "GenV-APP"

STATUS_DONE, STATUS_FAILED = 2, 3


class UpstreamError(Exception):
    def __init__(self, status, code, message):
        super().__init__(f"[{status}] {code}: {message}")
        self.status, self.code, self.message = status, code, message


class SnapGenProvider:
    def __init__(self, email=None, password=None, token=None, sid_file=".stable_id",
                 pool_file="accounts_pool.json"):
        self.accounts = []          # [{"email","password"}] rotation pool
        self._acc_idx = 0
        pf = pool_file or os.environ.get("GENV_POOL", "accounts_pool.json")
        try:
            self.accounts = [a for a in json.load(open(pf)) if a.get("email")]
        except OSError:
            pass
        e = email or os.environ.get("GENV_EMAIL")
        p = password or os.environ.get("GENV_PASSWORD")
        if e:
            self.accounts.insert(0, {"email": e, "password": p})
        web_pool = [a for a in self.accounts if a.get("platform") != APP_PLATFORM] or self.accounts
        self.email = web_pool[0]["email"] if web_pool else None
        self.password = web_pool[0]["password"] if web_pool else None
        self._token = token or os.environ.get("GENV_TOKEN")
        self._refresh = os.environ.get("GENV_REFRESH_TOKEN")
        # app-platform sub-pool: accounts created with platform="GenV-APP"
        self.app_accounts = [a for a in self.accounts if a.get("platform") == APP_PLATFORM]
        self._app_idx = 0
        self._app_token = None
        self.sid_file = sid_file
        self.sid = self._load_sid()
        self.client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers={"User-Agent": UA})
        self.mclient = httpx.AsyncClient(timeout=httpx.Timeout(120.0),
                                         headers={"User-Agent": MOBILE_UA})
        sync_time("https://api.snapgen.ai")

    def rotate_account(self):
        pool = [a for a in self.accounts if a.get("platform") != APP_PLATFORM] or self.accounts
        if len(pool) < 2:
            return False
        self._acc_idx = (self._acc_idx + 1) % len(pool)
        self.email = pool[self._acc_idx]["email"]
        self.password = pool[self._acc_idx]["password"]
        self._token = self._refresh = None
        return True

    def _load_sid(self):
        try:
            sid = open(self.sid_file).read().strip()
            if len(sid) == 22:
                return sid
        except OSError:
            pass
        sid = gen_stable_id()
        try:
            open(self.sid_file, "w").write(sid)
        except OSError:
            pass
        return sid

    async def login(self):
        if self._token:
            return self._token
        r = await self.client.post(f"{AUTH_API}/mobile/v1/login",
                                   json={"username": self.email, "password": self.password})
        if r.status_code != 200:
            raise UpstreamError(r.status_code, "LOGIN_FAILED", r.text[:300])
        d = r.json()
        self._token, self._refresh = d["access_token"], d.get("refresh_token")
        return self._token

    def _guard(self, path, method):
        return guard_token("/api" + path, method, self.sid)

    async def request(self, method, path, *, multipart=None, json_body=None, retry_auth=True):
        """Signed web-API request. path e.g. '/video-gen/vela'."""
        token = await self.login()
        headers = {"Authorization": "Bearer " + token, "x-guard-id": self._guard(path, method)}
        if multipart is not None:
            headers["Content-Type"] = f"multipart/form-data; boundary={multipart[0]}"
            data = multipart[1]
        else:
            data = None
        r = await self.client.request(method, WEB_API + path, content=data,
                                      json=json_body, headers=headers)
        expired = r.status_code == 401
        if not expired and r.status_code in (400, 403):
            try:
                expired = r.json().get("detail", {}).get("error_code") == "TOKEN_EXPIRED"
            except Exception:
                pass
        if expired and retry_auth:
            old = self._token
            self._token = None
            if self.email and self.password:
                await self.login()
            elif self._refresh:
                rr = await self.client.post(f"{AUTH_API}/mobile/v1/refresh-token",
                                            json={"refresh_token": self._refresh})
                if rr.status_code == 200:
                    d = rr.json()
                    self._token, self._refresh = d["access_token"], d.get("refresh_token", self._refresh)
            if not self._token:
                self._token = old  # surface the original 401 rather than a worse state
            return await self.request(method, path, multipart=multipart, json_body=json_body, retry_auth=False)
        return r

    @staticmethod
    def _raise(r):
        try:
            d = r.json().get("detail", {})
            code = d.get("error_code", "UPSTREAM_ERROR") if isinstance(d, dict) else "UPSTREAM_ERROR"
            msg = d.get("error_message", r.text[:300]) if isinstance(d, dict) else r.text[:300]
        except Exception:
            code, msg = "UPSTREAM_ERROR", r.text[:300]
        raise UpstreamError(r.status_code, code, msg)

    async def submit(self, path, fields: dict, rotate_on=("NOT_ENOUGH_AND_LOCK_CREDIT",
                                                         "NOT_ENOUGH_CREDIT")):
        """fields -> multipart. Returns upstream history dict ({uuid,status,...}).
        Rotates to the next pooled account when credits/quota run out."""
        for attempt in range(len(self.accounts) or 1):
            try:
                return await self._submit_once(path, fields)
            except UpstreamError as e:
                if e.code in rotate_on and self.rotate_account():
                    continue
                raise

    async def _submit_once(self, path, fields):
        boundary = "----gw" + "".join(random.choices(string.hexdigits, k=24))
        body = b""
        for k, v in fields.items():
            if v is None:
                continue
            if isinstance(v, (list, tuple)):
                for item in v:
                    body += self._part(boundary, k, item)
            else:
                body += self._part(boundary, k, v)
        body += f"--{boundary}--\r\n".encode()
        r = await self.request("POST", path, multipart=(boundary, body))
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    @staticmethod
    def _part(boundary, name, value):
        if isinstance(value, tuple):  # (filename, bytes, mime)
            fn, data, mime = value
            return (f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{fn}"\r\n'
                    f'Content-Type: {mime}\r\n\r\n').encode() + data + b"\r\n"
        return f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()

    async def history(self, uuid_):
        r = await self.request("GET", f"/history/{uuid_}")
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    async def quota(self):
        """web quota summary + mobile model quotas (best effort)."""
        out = {}
        r = await self.request("GET", "/user/quota/image-gen")
        if r.status_code == 200:
            out["web_image_gen"] = r.json()
        try:
            out["mobile_models"] = await self.mobile_quota()
        except UpstreamError:
            pass
        return out

    # ---------------- mobile channel (api.geminigen.ai) ----------------
    async def app_login(self):
        """Login into the APP credential store (platform="GenV-APP").
        Rotates across pool entries flagged platform=="GenV-APP"."""
        if self._app_token:
            return self._app_token
        pool = self.app_accounts or self.accounts
        if not pool:
            raise UpstreamError(0, "NO_APP_ACCOUNT",
                                "no app-platform account in pool; create one with platform='GenV-APP'")
        last = None
        for _ in range(len(pool)):
            a = pool[self._app_idx % len(pool)]
            r = await self.mclient.post(
                f"{AUTH_API}/mobile/v1/login",
                json={"username": a["email"], "password": a["password"],
                      "platform": APP_PLATFORM, "remember_me": False})
            if r.status_code == 200:
                self._app_token = r.json()["access_token"]
                return self._app_token
            last = (r.status_code, r.text[:200])
            self._app_idx += 1
        raise UpstreamError(last[0] if last else 401, "APP_LOGIN_FAILED",
                            last[1] if last else "all app accounts failed")

    def _rotate_app(self):
        pool = self.app_accounts or self.accounts
        if len(pool) < 2:
            return False
        # prefer usable accounts, honor pool first (bot/api-key reserve)
        usable_h = {a["email"] for a in usage.usable_app_accounts(pool) if a.get("honor")}
        usable_g = {a["email"] for a in usage.usable_app_accounts(pool)} - usable_h
        for want in (usable_h, usable_g | usable_h):
            for step in range(1, len(pool) + 1):
                idx = (self._app_idx + step) % len(pool)
                if pool[idx]["email"] in want:
                    self._app_idx = idx
                    self._app_token = None
                    return True
        # all gated: still rotate (caller may provision next)
        self._app_idx = (self._app_idx + 1) % len(pool)
        self._app_token = None
        return True

    async def mobile_request(self, method, path, *, multipart=None, json_body=None,
                             retry_auth=True):
        token = await self.app_login()
        headers = {"authorization": "Bearer " + token,
                   "x-app-version": APP_VERSION,
                   "accept": "application/json"}
        data = None
        if multipart is not None:
            headers["Content-Type"] = f"multipart/form-data; boundary={multipart[0]}"
            data = multipart[1]
        r = await self.mclient.request(method, AUTH_API + path, content=data,
                                       json=json_body, headers=headers)
        expired = r.status_code == 401
        if not expired and r.status_code in (400, 403):
            try:
                expired = r.json().get("detail", {}).get("error_code") == "TOKEN_EXPIRED"
            except Exception:
                pass
        if expired and retry_auth:
            self._app_token = None
            return await self.mobile_request(method, path, multipart=multipart,
                                             json_body=json_body, retry_auth=False)
        return r

    async def provision_app_account(self, password="abcd1234"):
        """Create a fresh GenV-APP account (mail.tm + OTP) and add it to the
        app pool. Each fresh account gets ~5 ad-free 0-credit generations."""
        import asyncio
        from genv_account import mailtm_account, mailtm_token, mailtm_wait_otp, genv_signup
        def _mk():
            email, mpwd = mailtm_account()
            mtok = mailtm_token(email, mpwd)
            at, rt = genv_signup(email, password=password,
                                 otp_getter=lambda: mailtm_wait_otp(mtok, 90),
                                 platform=APP_PLATFORM)
            return email
        email = await asyncio.to_thread(_mk)
        rec = {"email": email, "password": password, "platform": APP_PLATFORM,
               "honor": True, "created": time.time()}
        self.accounts.append(rec)
        self.app_accounts.append(rec)
        try:
            pf = os.environ.get("GENV_POOL", "accounts_pool.json")
            pool = json.load(open(pf))
            pool.append(rec)
            json.dump(pool, open(pf, "w"), indent=1)
        except OSError:
            pass
        return rec

    def _app_email(self):
        pool = self.app_accounts or self.accounts
        return pool[self._app_idx % len(pool)]["email"] if pool else None

    async def mobile_submit(self, path, fields: dict,
                            rotate_on=("NOT_ENOUGH_AND_LOCK_CREDIT", "NOT_ENOUGH_CREDIT",
                                       "AD_REWARD_REQUIRED"),
                            auto_provision=True):
        attempts = max(1, len(self.app_accounts or self.accounts))
        last_err = None
        for _ in range(attempts + 2):  # +2 retries via freshly provisioned accounts
            pool = self.app_accounts or self.accounts
            cur = self._app_email()
            boundary = "----gw" + "".join(random.choices(string.hexdigits, k=24))
            body = b""
            for k, v in fields.items():
                if v is None:
                    continue
                if isinstance(v, list):
                    for item in v:
                        body += self._part(boundary, k, item)
                else:
                    body += self._part(boundary, k, v)
            body += f"--{boundary}--\r\n".encode()
            try:
                r = await self.mobile_request("POST", path, multipart=(boundary, body))
            except UpstreamError as e:
                if e.code == "NO_APP_ACCOUNT" and auto_provision:
                    await self.provision_app_account()
                    self._app_idx = len(self.app_accounts) - 1
                    continue
                raise
            if r.status_code in (200, 201, 202):
                usage.bump(cur)
                return r.json()
            try:
                self._raise(r)
            except UpstreamError as e:
                last_err = e
                if e.code == "AD_REWARD_REQUIRED":
                    usage.mark_gated(cur)
                if e.code in rotate_on:
                    if not self._rotate_app() and auto_provision and e.code == "AD_REWARD_REQUIRED":
                        await self.provision_app_account()
                        self._app_idx = len(self.app_accounts or self.accounts) - 1
                        self._app_token = None
                    continue
                if e.code.startswith("MAX_PROCESSING") and _ > 0:
                    import asyncio
                    await asyncio.sleep(30)
                    continue
                raise
        raise last_err or UpstreamError(0, "SUBMIT_EXHAUSTED", "all accounts exhausted")

    async def mobile_history(self, uuid_):
        r = await self.mobile_request("GET", f"/mobile/v1/history/{uuid_}")
        if r.status_code != 200:
            self._raise(r)
        return r.json()

    async def mobile_quota(self):
        r = await self.mobile_request("GET", "/mobile/v1/user/quota/models")
        if r.status_code != 200:
            self._raise(r)
        return r.json()
