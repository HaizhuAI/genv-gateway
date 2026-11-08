import hashlib, base64, time, random, string

SECRET_SALT = "&vTQm0&u"
SECRET_KEY = "45NPBH$&"
BUCKET_MS = 60000

def _sha(s): return hashlib.sha256(s.encode()).hexdigest()
def _b64url(b): return base64.urlsafe_b64encode(bytes(b)).decode().rstrip("=")
def _ml(s):
    t = 0
    for ch in s:
        t = ((t << 5) - t + ord(ch)) & 0xFFFFFFFF
        if t & 0x80000000: t -= 0x100000000
    return format(abs(t), "08x")

# stable id: deterministic derivation per "device" (server only checks format)
def stable_id(rand_hex, ua="okhttp/4.12.0", screen="1080x2400", salt=SECRET_SALT):
    s = f"{rand_hex}.{_ml(ua)}.{_ml(screen)}"
    i = _sha(f"{salt}:{s}")
    return _b64url(i.encode())[:22]

def gen_stable_id():
    return stable_id("".join(random.choices("0123456789abcdef", k=32)))

_time_offset = 0
def sync_time(base="https://api.geminigen.ai"):
    import urllib.request
    global _time_offset
    try:
        req = urllib.request.Request(base + "/health", headers={"User-Agent": "okhttp/4.12.0"})
        t0 = time.time()
        with urllib.request.urlopen(req, timeout=15) as r:
            date = r.headers.get("Date")
        t1 = time.time()
        if date:
            import email.utils
            _time_offset = email.utils.parsedate_to_datetime(date).timestamp() - (t0 + t1) / 2
    except Exception:
        pass

def guard_token(path, method, sid, secret_key=SECRET_KEY, domfp=None, bucket=None):
    """path: the signed path string; method: http method; sid: 22-char stable id; domfp: 64-hex"""
    i = secret_key
    u = _sha(f"{i}:{sid}")[:32]
    if bucket is None:
        bucket = int((time.time() + _time_offset) // (BUCKET_MS / 1000))
    l = _sha(f"{path}:{method.upper()}:{u}:{bucket}:{i}")
    fp = domfp or ("0" * 64)
    raw = bytes([1]) + bytes.fromhex(u) + bucket.to_bytes(4, "big") + bytes.fromhex(l) + bytes.fromhex(fp)
    return _b64url(raw)
