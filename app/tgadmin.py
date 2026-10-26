"""Verified group admin check for the Royce bot.

A user counts as admin only when Telegram itself says so (getChatMember returns status
creator or administrator). Nothing a user claims in chat is trusted. Results are cached for
10 minutes per (chat, user). If the lookup fails, the answer is no and nothing is cached.
"""
import time

CACHE_TTL = 600
ADMIN_STATUSES = ("creator", "administrator")
_cache = {}


def is_admin(chat_id, user_id, api_fn, now=None):
    if user_id is None or chat_id is None:
        return False
    now = float(now if now is not None else time.time())
    key = (chat_id, user_id)
    hit = _cache.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    try:
        r = api_fn("getChatMember", chat_id=chat_id, user_id=user_id)
    except Exception as e:
        print(f"[bot] admin check failed (treated as not admin): {e}", flush=True)
        return False
    if not isinstance(r, dict) or not r.get("ok"):
        return False
    status = (r.get("result") or {}).get("status")
    ok = status in ADMIN_STATUSES
    _cache[key] = (now, ok)
    return ok


def clear_cache():
    _cache.clear()
