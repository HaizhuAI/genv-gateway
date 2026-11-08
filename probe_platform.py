import json, time, random, urllib.request, urllib.error, uuid

API = "https://api.geminigen.ai"
accs = json.load(open("accounts.json"))
cool = {}  # email -> next allowed ts

def multipart(fields, boundary=None):
    b = boundary or ("----x" + uuid.uuid4().hex)
    body = b""
    for k, v in fields:
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += f'--{b}--\r\n'.encode()
    return body, f'multipart/form-data; boundary={b}'

def probe(acc, headers=None, fields_extra=(), query="", ua="okhttp/4.12.0"):
    email = acc["email"]
    wait = cool.get(email, 0) - time.time()
    if wait > 0: time.sleep(wait)
    cool[email] = time.time() + 58
    url = API + "/mobile/v4/imagen/free" + query
    fields = [("prompt", "a red apple"), ("orientation", "square")] + list(fields_extra)
    body, ct = multipart(fields)
    h = {"Authorization": "Bearer " + acc["access_token"], "Content-Type": ct,
         "User-Agent": ua, "X-Forwarded-For": ".".join(str(random.randint(1,254)) for _ in range(4))}
    if headers: h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=120); return r.status, r.read()[:400].decode("utf-8","ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400].decode("utf-8","ignore")

TESTS = [
    ("hdr x-app-version",        {"x-app-version":"3.7.2"}, (), "", "okhttp/4.12.0"),
    ("hdr x-platform android",   {"x-platform":"android"}, (), "", "okhttp/4.12.0"),
    ("hdr all3 x-token dummy",   {"x-app-version":"3.7.2","x-timestamp":"1762200000","x-token":"d41d8cd98f00b204e9800998ecf8427e"}, (), "", "okhttp/4.12.0"),
    ("field platform=android",   None, [("platform","android")], "", "okhttp/4.12.0"),
    ("field device_type=android",None, [("device_type","android")], "", "okhttp/4.12.0"),
    ("field device=android",     None, [("device","android")], "", "okhttp/4.12.0"),
    ("field os=android",         None, [("os","android")], "", "okhttp/4.12.0"),
    ("field source=android",     None, [("source","android")], "", "okhttp/4.12.0"),
    ("field channel=gp",         None, [("channel","googleplay")], "", "okhttp/4.12.0"),
    ("query ?platform=android",  None, (), "?platform=android", "okhttp/4.12.0"),
    ("UA Dart/3.4",              None, (), "", "Dart/3.4 (dart:io)"),
    ("UA GenV app",              None, (), "", "GenV/3.7.2 (Android; com.ainate.ai.video.generate)"),
    ("hdr platform: ANDROID",    {"platform":"ANDROID"}, (), "", "okhttp/4.12.0"),
    ("hdr app: genv",            {"app":"genv","version":"3.7.2"}, (), "", "okhttp/4.12.0"),
    ("field from=android",       None, [("from","android")], "", "okhttp/4.12.0"),
    ("field model=Pixel7",       None, [("model","Pixel 7")], "", "okhttp/4.12.0"),
    ("hdr x-device android",     {"x-device":"android"}, (), "", "okhttp/4.12.0"),
    ("hdr x-os android",         {"x-os":"android"}, (), "", "okhttp/4.12.0"),
]

i = 0
for name, hdrs, fx, q, ua in TESTS:
    acc = min(accs, key=lambda a: cool.get(a["email"], 0))
    s, r = probe(acc, hdrs, fx, q, ua)
    i += 1
    print(f"[{i:02d}] {name:28s} acct={acc['email'][:18]:20s} -> {s} {r[:160]}")
