"""Account usage ledger + reserve-pool ("honor pool") policy.

A fresh GenV-APP account yields ~FREE_GENS_PER_ACCOUNT ad-free 0-credit
generations; afterwards every submit hits AD_REWARD_REQUIRED. The server
does not expose the remaining allowance, so we track submits ourselves.

state.json:
{
  "accounts": {"<email>": {"gens_used": int, "ad_gated": bool}},
  "policy": {"min_usable": int, "refill_batch": int, "auto": bool}
}
"""
import json, os, threading, time

STATE_FILE = os.environ.get("GENV_STATE", "gateway_state.json")
FREE_GENS_PER_ACCOUNT = int(os.environ.get("GENV_FREE_GENS", "5"))

_lock = threading.Lock()
_state = {"accounts": {}, "policy": {
    "min_usable": int(os.environ.get("GENV_MIN_USABLE", "3")),
    "refill_batch": int(os.environ.get("GENV_REFILL_BATCH", "3")),
    "auto": True,
}}


def _load():
    global _state
    try:
        d = json.load(open(STATE_FILE))
        _state["accounts"].update(d.get("accounts") or {})
        _state["policy"].update(d.get("policy") or {})
    except OSError:
        pass


def _save():
    try:
        json.dump(_state, open(STATE_FILE, "w"), indent=1)
    except OSError:
        pass


def bump(email, ad_gated=False):
    """Record one successful submit on <email> (or flag it ad-gated)."""
    if not email:
        return
    with _lock:
        a = _state["accounts"].setdefault(email, {"gens_used": 0, "ad_gated": False})
        if ad_gated:
            a["ad_gated"] = True
        else:
            a["gens_used"] += 1
        _save()


def mark_gated(email):
    bump(email, ad_gated=True)


def usable_app_accounts(app_accounts):
    """Pool entries that are (probably) still under the ad-free allowance."""
    out = []
    for a in app_accounts:
        rec = _state["accounts"].get(a["email"], {})
        if not rec.get("ad_gated") and rec.get("gens_used", 0) < FREE_GENS_PER_ACCOUNT:
            out.append({**a, "gens_used": rec.get("gens_used", 0)})
    return out


def honor_accounts(app_accounts):
    """The honor pool = accounts auto-provisioned for internal channels
    (QQ bot + sub API keys). Manually seeded accounts are 'general'."""
    return [a for a in app_accounts if a.get("honor")]


def account_stats(app_accounts):
    out = []
    for a in app_accounts:
        rec = _state["accounts"].get(a["email"], {})
        used = rec.get("gens_used", 0)
        gated = rec.get("ad_gated") or used >= FREE_GENS_PER_ACCOUNT
        out.append({"email": a["email"], "gens_used": used,
                    "free_left": max(0, FREE_GENS_PER_ACCOUNT - used),
                    "ad_gated": gated, "platform": a.get("platform"),
                    "honor": bool(a.get("honor")),
                    "created": a.get("created")})
    return out


def policy():
    return dict(_state["policy"])


def set_policy(**kw):
    with _lock:
        _state["policy"].update({k: v for k, v in kw.items() if v is not None})
        _save()
    return policy()


def needs_refill(app_accounts):
    """Threshold check against the HONOR pool only: if the honor pool has no
    usable accounts below min_usable -> refill (auto-provision marks honor)."""
    p = _state["policy"]
    honor = honor_accounts(app_accounts)
    base = honor if honor or p["min_usable"] else app_accounts
    usable = len(usable_app_accounts(base))
    return p["auto"] and usable < p["min_usable"], usable, p


_load()
