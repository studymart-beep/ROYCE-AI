"""Model router: picks the provider order for a call by capability and recent health.

Providers stay defined in app/config.py. The router decides which providers a call may use
and records health. A provider in active cooldown (after a transient error) or marked
invalid (auth rejected) is SKIPPED by call(): it is not tried until its cooldown expires,
so no repeat calls go to a provider known to be unhealthy. pick_order() still lists
unhealthy providers last (demoted) for callers that only want an ordering.

Error classes (classify_error):
  auth         HTTP 401/403: the key was rejected. Provider marked invalid and skipped.
  bad_request  HTTP 400/422: our request is wrong. Raised at once, no failover, no cooldown.
  transient    everything else (404, 429, 5xx, timeout, network, unknown): cooldown, failover.

Health is in memory only. A restart clears it, which is fine: the next call re-learns it.
Security: error text is truncated and stored, keys are never read or logged here.
"""
import threading, time, urllib.error

from ..config import PROVIDERS
from . import budget

# capability tags per provider. A provider is only picked for a capability it carries.
CAPABILITIES = {
    "gemini": {"chat", "json", "vision"},
    "openrouter": {"chat", "json", "vision"},
}
# seconds a provider stays skipped after a transient error
COOLDOWN = 120

_LOCK = threading.Lock()
_HEALTH = {}  # pid -> {"last_error", "last_error_at", "last_ok_at", "invalid"}


def _now():
    return time.time()


def _blank():
    return {"last_error": None, "last_error_at": None, "last_ok_at": None, "invalid": False}


def record_ok(pid, now=None):
    with _LOCK:
        h = _HEALTH.setdefault(pid, _blank())
        h["last_ok_at"] = now if now is not None else _now()
        h["invalid"] = False


def record_error(pid, error, now=None):
    with _LOCK:
        h = _HEALTH.setdefault(pid, _blank())
        h["last_error"] = str(error)[:200]
        h["last_error_at"] = now if now is not None else _now()


def record_invalid(pid, error, now=None):
    """Auth rejected: the provider is skipped until a later call or validation succeeds."""
    record_error(pid, error, now)
    with _LOCK:
        _HEALTH[pid]["invalid"] = True


def health(pid=None):
    with _LOCK:
        if pid is not None:
            return dict(_HEALTH.get(pid, {}))
        return {k: dict(v) for k, v in _HEALTH.items()}


def reset_health():
    with _LOCK:
        _HEALTH.clear()


def classify_error(exc):
    code = getattr(exc, "code", None) if isinstance(exc, urllib.error.HTTPError) else None
    if code in (401, 403):
        return "auth"
    if code in (400, 422):
        return "bad_request"
    return "transient"


def cooldown_remaining(pid, now=None):
    """Seconds until a transient-error cooldown ends (0 when none)."""
    h = health(pid)
    if not h.get("last_error_at") or h.get("invalid"):
        return 0.0
    if h.get("last_ok_at") and h["last_ok_at"] >= h["last_error_at"]:
        return 0.0
    t = now if now is not None else _now()
    return max(0.0, COOLDOWN - (t - h["last_error_at"]))


def is_healthy(pid, now=None):
    h = health(pid)
    if h.get("invalid"):
        return False
    if not h.get("last_error_at"):
        return True
    if h.get("last_ok_at") and h["last_ok_at"] >= h["last_error_at"]:
        return True
    return cooldown_remaining(pid, now) <= 0


def is_configured(pid):
    import os
    return bool(os.environ.get(PROVIDERS[pid]["key_env"], ""))


def _names(capability, preferred):
    names = [p for p in PROVIDERS if capability in CAPABILITIES.get(p, set())]
    if preferred in names:
        names = [preferred] + [p for p in names if p != preferred]
    return names


def pick_order(capability="chat", preferred=None, configured=None, now=None, skip_unhealthy=False):
    """Providers able to serve `capability`, preferred first, healthy before unhealthy.

    configured: optional callable pid -> bool (tests inject this; default checks env).
    skip_unhealthy=True drops unhealthy providers entirely (what call() uses).
    """
    check = configured or is_configured
    usable = [p for p in _names(capability, preferred) if check(p)]
    healthy = [p for p in usable if is_healthy(p, now)]
    if skip_unhealthy:
        return healthy
    sick = [p for p in usable if p not in healthy]
    return healthy + sick


def call(fn, capability="chat", preferred=None, configured=None, log=print, now=None, before_attempt=None):
    """Run fn(pid) against healthy providers in router order. Returns the first success.

    before_attempt() runs before every attempt (outside failover handling), so a budget
    check there can stop the whole call. Errors:
      - none configured           -> RuntimeError "no llm provider configured"
      - all cooling down/invalid  -> RuntimeError naming the wait time, never a silent empty call
      - every attempt failed      -> RuntimeError "all providers failed: <last error>"
    """
    check = configured or is_configured
    order = pick_order(capability, preferred, configured, now=now, skip_unhealthy=True)
    if not order:
        usable = [p for p in _names(capability, preferred) if check(p)]
        if not usable:
            raise RuntimeError("no llm provider configured")
        if all(health(p).get("invalid") for p in usable):
            raise RuntimeError("all llm providers rejected their api key: check GEMINI_API_KEY and OPENROUTER_API_KEY in .env")
        wait = min(cooldown_remaining(p, now) for p in usable if not health(p).get("invalid")) if any(
            not health(p).get("invalid") for p in usable) else 0
        raise RuntimeError(f"llm providers are cooling down after errors; retry in about {int(wait) + 1}s")
    last = None
    for pid in order:
        if before_attempt is not None:
            before_attempt()
        try:
            out = fn(pid)
            record_ok(pid, now)
            log(f"[llm] served by {pid} ({capability})")
            return out
        except budget.BudgetExceeded:
            raise
        except Exception as e:
            last = e
            kind = classify_error(e)
            if kind == "bad_request":
                log(f"[llm] {pid} rejected the request: {str(e)[:200]}")
                raise
            if kind == "auth":
                record_invalid(pid, e, now)
            else:
                record_error(pid, e, now)
            log(f"[llm] {pid} failed ({kind}): {str(e)[:200]}")
    raise RuntimeError(f"all providers failed: {last}")


def readiness():
    """Which providers are configured and their last health. No key values."""
    out = {}
    for pid in PROVIDERS:
        h = health(pid)
        out[pid] = {
            "configured": is_configured(pid),
            "healthy": is_healthy(pid),
            "last_error_at": h.get("last_error_at"),
        }
    return out
