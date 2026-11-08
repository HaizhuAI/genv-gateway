import json, uuid, urllib.request, urllib.error, time, random

API = "https://api.geminigen.ai"
accs = json.load(open("accounts.json"))
cool = {}

def probe(acc, headers=None, ua="okhttp/4.12.0"):
    email = acc["email"]
    wait = cool.get(email, 0) - time.time()
    if wait > 0: time.sleep(wait)
    cool[email] = time.time() + 58
    b = "----x" + uuid.uuid4().hex
    body = b""
    for k,v in [("prompt","a red apple"),("orientation","square")]:
        body += f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    body += f'--{b}--\r\n'.encode()
    h = {"Authorization":"Bearer "+acc["access_token"],"Content-Type":f"multipart/form-data; boundary={b}","User-Agent":ua}
    if headers: h.update(headers)
    req = urllib.request.Request(API+"/mobile/v4/imagen/free", data=body, headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=90); return r.status, r.read()[:300].decode("utf8","ignore")
    except urllib.error.HTTPError as e: return e.code, e.read()[:300].decode("utf8","ignore")

def run(tests):
    for name, hdrs, ua in tests:
        acc = min(accs, key=lambda a: cool.get(a["email"], 0))
        s, r = probe(acc, hdrs, ua)
        print(f"{name:34s} -> {s} {r[:150]}", flush=True)
