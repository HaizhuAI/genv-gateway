import json, uuid, urllib.request, urllib.error, time, random

API = "https://api.geminigen.ai"
def post_json(path, body, tok=None):
    h = {"Content-Type":"application/json","User-Agent":"okhttp/4.12.0"}
    if tok: h["Authorization"]="Bearer "+tok
    req = urllib.request.Request(API+path, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        r = urllib.request.urlopen(req, timeout=30); return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read() or b"{}")
        except Exception: return e.code, {}

def login(acc):
    s, r = post_json("/mobile/v1/login", {"username": acc["email"], "password": "PASSWORD"})
    if s == 200:
        acc["access_token"], acc["refresh_token"] = r["access_token"], r["refresh_token"]
    return s, r

def gen(acc, headers=None, ua="okhttp/4.12.0"):
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
