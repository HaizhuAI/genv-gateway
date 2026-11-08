import json, time, os, sys
from genv_account import mailtm_account, mailtm_token, mailtm_wait_otp, genv_signup

DB = "accounts.json"
accs = json.load(open(DB)) if os.path.exists(DB) else []
n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
for i in range(n):
    try:
        email, mpwd = mailtm_account()
        mtok = mailtm_token(email, mpwd)
        at, rt = genv_signup(email, otp_getter=lambda: mailtm_wait_otp(mtok, 90))
        accs.append({"email": email, "mail_pwd": mpwd, "access_token": at, "refresh_token": rt, "created": time.time()})
        json.dump(accs, open(DB, "w"), indent=1)
        print(f"[{len(accs)}] {email} OK")
    except Exception as e:
        print("FAIL:", repr(e)[:200])
        time.sleep(5)
print("total:", len(accs))
