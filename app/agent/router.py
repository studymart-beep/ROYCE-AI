"""Model router: picks the provider order for a call by capability and recent health.

Providers stay defined in app/config.py. The router only decides ORDER and records
health. Unhealthy providers are demoted to the end of the order, never removed, so
behaviour matches the old gemini -> openrouter failover and a flaky provider still
gets tried last.

Health is in memory only (last error, last error time, last success time). A restart
clears it, which is fine: the next call re-learns it.

Security: error text is truncated and stored, keys are never read here.
"""
import threading, time

from ..config import PROVIDERS

# capability tags per provider. A provider is only picked for a capability it carries.
CAPABILITIES = {
    "gemini": {"chat", "json", "vision"},
    "openrouter": {"chat", "json", "vision"},
}
# seconds a provider stays demoted after its last error
COOLDOWN = 120

_LOCK = threading.Lock()
_HEALTH = {}  # pid -> {"last_error": str|None, "last_error_at": float|None, "last_ok_at": float|None}


def _now():
    return time.time()


def record_ok(pid, now=None):
    with _LOCK:
        h = _HEALTH.setdefault(pid, {"last_error": None, "last_error_at": None, "last_ok_at": None})
        h["last_ok_at"] = now if now is not None else _now()


def record_error(pid, error, now=None):
    with _LOCK:
        h = _HEALTH.setdefault(pid, {"last_error": None, "last_error_at": None, "last_ok_at": None})
        h["last_error"] = str(error)[:200]
        h["last_error_at"] = now if now is not None else _now()


def health(pid=None):
    with _LOCK:
        if pid is not None:
            return dict(_HEALTH.get(pid, {}))
        return {k: dict(v) for k, v in _HEALTH.items()}


def reset_health():
    with _LOCK:
        _HEALTH.clear()


def is_healthy(pid, now=None):
    h = health(pid)
    if not h.get("last_error_at"):
        return True
    t = now if now is not None else _now()
    if h.get("last_ok_at") and h["last_ok_at"] >= h["last_error_at"]:
        return True
    return (t - h["last_error_at"]) >= COOLDOWN


def is_configured(pid):
    import os
    return bool(os.environ.get(PROVIDERS[pid]["key_env"], ""))


def pick_order(capability="chat", preferred=None, configured=None, now=None):
    """Providers able to serve `capability`, preferred first, healthy before unhealthy.

    configured: optional callable pid -> bool (tests inject this; default checks env).
    """
    check = configured or is_configured
    names = [p for p in PROVIDERS if capability in CAPABILITIES.get(p, set())]
    if preferred in names:
        names = [preferred] + [p for p in names if p != preferred]
    usable = [p for p in names if check(p)]
    healthy = [p for p in usable if is_healthy(p, now)]
    sick = [p for p in usable if p not in healthy]
    return healthy + sick


def call(fn, capability="chat", preferred=None, configured=None, log=print):
    """Run fn(pid) against providers in router order. Returns the first success.

    Raises RuntimeError naming the last error when every provider failed or none is configured.
    """
    order = pick_order(capability, preferred, configured)
    last = None
    for pid in order:
        try:
            out = fn(pid)
            record_ok(pid)
            log(f"[llm] served by {pid} ({capability})")
            return out
        except Exception as e:  # any provider error is a failover trigger
            last = e
            record_error(pid, e)
            log(f"[llm] {pid} failed: {str(e)[:200]}")
    if last is None:
        raise RuntimeError("no llm provider configured")
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
