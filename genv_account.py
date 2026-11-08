import json, random, string, time, urllib.request

API = "https://api.geminigen.ai"
MT = "https://api.mail.tm"

UA = "okhttp/4.12.0"

def _req(url, data=None, headers=None, method=None):
    h = {"Content-Type": "application/json", "User-Agent": UA}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(data).encode() if data is not None else None, headers=h, method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: body = json.loads(e.read() or b"{}")
        except Exception: body = {"raw": e.read()[:500].decode("utf-8","ignore")}
        return e.code, body

def xff():
    return ".".join(str(random.randint(1, 254)) for _ in range(4))

def mailtm_account(retries=4):
    for _ in range(retries):
        try:
            dom = _req(MT + "/domains")[1]["hydra:member"][0]["domain"]
            addr = "".join(random.choices(string.ascii_lowercase + string.digits, k=12)) + "@" + dom
            pwd = "Pz!" + "".join(random.choices(string.ascii_letters + string.digits, k=12))
            s, r = _req(MT + "/accounts", {"address": addr, "password": pwd})
            if s in (200, 201):
                return addr, pwd
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError("mail.tm account creation failed")

def mailtm_token(addr, pwd):
    s, r = _req(MT + "/token", {"address": addr, "password": pwd})
    return r["token"]

def mailtm_wait_otp(tok, timeout=60, since=None):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s, r = _req(MT + "/messages", headers={"Authorization": "Bearer " + tok})
        for m in r.get("hydra:member", []):
            s2, full = _req(MT + "/messages/" + m["id"], headers={"Authorization": "Bearer " + tok})
            txt = full.get("text", "") + json.dumps(full)
            for dig in (4, 6):
                import re
                mm = re.search(r"\b(\d{%d})\b" % dig, txt)
                if mm: return mm.group(1)
        time.sleep(3)
    return None

def genv_signup(email, password="abcd1234", otp_getter=None, name="User", platform=None):
    """otp_getter: callable returning the otp code (called after request-otp).
    platform="GenV-APP" registers an APP-credential-store account (required for
    the mobile /mobile/v4/* channel)."""
    s, r = _req(API + "/mobile/v1/request-otp", {"email": email, "type": "sign_up"}, {"X-Forwarded-For": xff()})
    assert s == 200, ("request-otp", s, r)
    otp = otp_getter() if otp_getter else None
    body = {"email": email, "full_name": name, "password": password, "otp": otp}
    if platform:
        body["platform"] = platform
    s, r = _req(API + "/mobile/v1/signup", body, {"X-Forwarded-For": xff()})
    assert s == 200, ("signup", s, r)
    return r["access_token"], r["refresh_token"]

def genv_login(email, password="abcd1234", platform=None):
    body = {"username": email, "password": password}
    if platform:
        body["platform"] = platform
        body["remember_me"] = False
    s, r = _req(API + "/mobile/v1/login", body, {"X-Forwarded-For": xff()})
    assert s == 200, ("login", s, r)
    return r.get("access_token"), r.get("refresh_token")

if __name__ == "__main__":
    email, mpwd = mailtm_account()
    print("mailbox:", email)
    mtok = mailtm_token(email, mpwd)
    at, rt = genv_signup(email, otp_getter=lambda: mailtm_wait_otp(mtok))
    print("access_token:", at[:40], "...")
    print("refresh:", rt)
