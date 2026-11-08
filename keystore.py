"""Gateway API-key store: per-key model scope + generation limit.

keys.json: [{"id","key","name","models":[ids]|"*","limit":int|null,"used":int,
             "revoked":bool,"created":ts}]

Master key = env GATEWAY_API_KEY (unrestricted). Sub-keys are checked by the
/v1 middleware; `limit` counts successful generation submits.
"""
import json, os, secrets, time

KEYS_FILE = os.environ.get("GENV_KEYS", "keys.json")
MASTER = os.environ.get("GATEWAY_API_KEY")


def _load():
    try:
        return json.load(open(KEYS_FILE))
    except OSError:
        return []


def _save(keys):
    json.dump(keys, open(KEYS_FILE, "w"), indent=1)


def list_keys():
    return _load()


def create_key(name="", models="*", limit=None):
    if isinstance(models, str) and models != "*":
        models = [m.strip() for m in models.split(",") if m.strip()]
    keys = _load()
    rec = {"id": "k" + secrets.token_hex(4),
           "key": "sgw-" + secrets.token_urlsafe(24),
           "name": name or "unnamed",
           "models": models,
           "limit": limit, "used": 0,
           "revoked": False, "created": time.time()}
    keys.append(rec)
    _save(keys)
    return rec


def revoke_key(kid):
    keys = _load()
    for k in keys:
        if k["id"] == kid:
            k["revoked"] = True
            _save(keys)
            return True
    return False


def delete_key(kid):
    keys = _load()
    new = [k for k in keys if k["id"] != kid]
    if len(new) != len(keys):
        _save(new)
        return True
    return False


def authenticate(token, model=None):
    """-> (ok, err). Master key bypasses everything."""
    if MASTER and token == MASTER:
        return True, None
    keys = _load()
    for k in keys:
        if k["key"] == token:
            if k["revoked"]:
                return False, "key revoked"
            if k["models"] != "*" and model and model not in k["models"]:
                return False, f"model '{model}' not allowed for this key"
            if k["limit"] is not None and k["used"] >= k["limit"]:
                return False, "key generation limit reached"
            return True, None
    return False, "invalid api key"


def charge(token):
    """Increment usage after a successful submit (not for master)."""
    if MASTER and token == MASTER:
        return
    keys = _load()
    for k in keys:
        if k["key"] == token:
            k["used"] += 1
            _save(keys)
            return
