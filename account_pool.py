"""Mass-create SnapGen accounts via mail.tm disposable mail + OTP.

Usage:
    python account_pool.py 5            # create 5 accounts -> accounts_pool.json
    python account_pool.py 5 --append   # append to existing pool
"""
import json, sys, time
from genv_account import mailtm_account, mailtm_token, mailtm_wait_otp, genv_signup

POOL_FILE = "accounts_pool.json"


def create_one(idx, platform=None):
    email, mpwd = mailtm_account()
    mtok = mailtm_token(email, mpwd)
    try:
        at, rt = genv_signup(email, otp_getter=lambda: mailtm_wait_otp(mtok, 90),
                             platform=platform)
    except AssertionError as e:
        print(f"[{idx}] signup failed: {e}", flush=True)
        return None
    rec = {"email": email, "password": "abcd1234", "mail_password": mpwd}
    if platform:
        rec["platform"] = platform
    print(f"[{idx}] created {email} ({platform or 'web'})", flush=True)
    return rec


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    append = "--append" in sys.argv
    pool = []
    if append:
        try:
            pool = json.load(open(POOL_FILE))
        except OSError:
            pass
    app = "--app" in sys.argv
    for i in range(n):
        rec = create_one(i, platform="GenV-APP" if app else None)
        if rec:
            pool.append(rec)
            json.dump(pool, open(POOL_FILE, "w"), indent=1)
        time.sleep(2)
    print("pool size:", len(pool))


if __name__ == "__main__":
    main()
