"""Honest provider validation for /health.

Each provider gets one cheap, free live check: a GET on its model-list endpoint (no tokens
spent). Outcome states:
  not_configured  the key env var is missing or empty (no network call made)
  validated       the provider answered 2xx: the key works
  unavailable     inconclusive: 429 rate limit, timeout, 5xx, network error. NOT invalid.
  invalid         401/403: the provider rejected the key
Keys are sent only in the Authorization/x-goog header, never logged, and never returned.
Results are cached for CACHE_TTL seconds so /health cannot spam providers.
"""
import hashlib, os, socket, threading, time, urllib.error, urllib.request

from ..config import PROVIDERS
from . import router

CHECK_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}
TIMEOUT = 8
CACHE_TTL = 300

_LOCK = threading.Lock()
_CACHE = {}  # pid -> (time, result dict)

STATE_TEXT = {
    "not_configured": "not configured (key missing)",
    "validated": "validated (live check passed)",
    "unavailable": "unavailable (rate limited, timed out or unreachable; not proven invalid)",
    "invalid": "invalid (key rejected by the provider)",
}


def _request(pid, key):
    headers = {"User-Agent": "RoyceAI/1.0"}
    if pid == "gemini":
        headers["x-goog-api-key"] = key
    else:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(CHECK_URLS[pid], headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        r.read(256)
        return r.status


def classify_probe(pid, key, opener=None):
    """Run the live check. opener(pid, key) -> status code, or raises. Returns (state, detail)."""
    if not key:
        return "not_configured", STATE_TEXT["not_configured"]
    try:
        status = int((opener or _request)(pid, key))
        if 200 <= status < 300:
            return "validated", STATE_TEXT["validated"]
        if status in (401, 403):
            return "invalid", STATE_TEXT["invalid"]
        return "unavailable", f"unavailable (provider answered HTTP {status}; not proven invalid)"
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "invalid", STATE_TEXT["invalid"]
        if e.code == 429:
            return "unavailable", "unavailable (rate limited, HTTP 429; not proven invalid)"
        return "unavailable", f"unavailable (HTTP {e.code}; not proven invalid)"
    except (socket.timeout, TimeoutError):
        return "unavailable", STATE_TEXT["unavailable"]
    except urllib.error.URLError as e:
        if isinstance(getattr(e, "reason", None), (socket.timeout, TimeoutError)):
            return "unavailable", STATE_TEXT["unavailable"]
        return "unavailable", STATE_TEXT["unavailable"]
    except Exception:
        return "unavailable", STATE_TEXT["unavailable"]


def check(pid, now=None, opener=None, force=False):
    """Cached validation for one provider. Also updates router health (invalid skips it)."""
    t = now if now is not None else time.time()
    key = os.environ.get(PROVIDERS[pid]["key_env"], "")
    fp = hashlib.sha256(key.encode()).hexdigest() if key else ""  # cache by key identity, never store the key
    with _LOCK:
        hit = _CACHE.get(pid)
        if hit and not force and hit[2] == fp and t - hit[0] < CACHE_TTL:
            return dict(hit[1])
    state, detail = classify_probe(pid, key, opener)
    result = {"state": state, "detail": detail, "checked_at": t}
    if state == "invalid":
        router.record_invalid(pid, detail, t)
    elif state == "validated":
        router.record_ok(pid, t)
    with _LOCK:
        _CACHE[pid] = (t, result, fp)
    return dict(result)


def check_all(now=None, opener=None, force=False):
    return {pid: check(pid, now=now, opener=opener, force=force) for pid in PROVIDERS}


def reset():
    with _LOCK:
        _CACHE.clear()
