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

Model cascade: a provider may carry several models (gemini: GEMINI_MODELS, see app/config.py).
Health is then kept per (provider, model): a retryable failure cools only that model, the next
model in the cascade is tried, and the next provider (openrouter) is used only once every model
of the provider has failed in this call ("exhausted"). Auth errors apply to the whole provider.

Health is in memory only. A restart clears it, which is fine: the next call re-learns it.
Security: error text is truncated and stored, keys are never read or logged here.
"""
import threading, time, urllib.error

from ..config import PROVIDERS, gemini_models
from . import budget

# capability tags per provider. A provider is only picked for a capability it carries.
CAPABILITIES = {
    "gemini": {"chat", "json", "vision"},
    "openrouter": {"chat", "json", "vision"},
}
# seconds a provider stays skipped after a transient error
COOLDOWN = 120

_LOCK = threading.Lock()
_HEALTH = {}  # key -> {"last_error", "last_error_at", "last_ok_at", "invalid"}; key = pid or (pid, model)
_TLS = threading.local()  # active (pid, model) for the attempt running on this thread
_SERVED = None  # (pid, model) that answered the most recent successful call(), process-wide


def _key(pid, model=None):
    return pid if model is None else (pid, model)


def models_for(pid):
    """Cascade model names for a provider, or [] for providers with a single fixed model."""
    if pid == "gemini":
        return gemini_models()
    return []


def active_model(pid):
    """Model the router is calling for pid right now (cascade entry), else the provider default."""
    cur = getattr(_TLS, "active", None)
    if cur and cur[0] == pid and cur[1]:
        return cur[1]
    return PROVIDERS[pid]["model"]


def last_served():
    """(provider, model) that answered the most recent successful call(), or None."""
    with _LOCK:
        return _SERVED


def _now():
    return time.time()


def _blank():
    return {"last_error": None, "last_error_at": None, "last_ok_at": None, "invalid": False}


def record_ok(pid, now=None, model=None):
    with _LOCK:
        h = _HEALTH.setdefault(_key(pid, model), _blank())
        h["last_ok_at"] = now if now is not None else _now()
        h["invalid"] = False


def record_error(pid, error, now=None, model=None):
    with _LOCK:
        h = _HEALTH.setdefault(_key(pid, model), _blank())
        h["last_error"] = str(error)[:200]
        h["last_error_at"] = now if now is not None else _now()


def record_invalid(pid, error, now=None):
    """Auth rejected: the provider is skipped until a later call or validation succeeds."""
    record_error(pid, error, now)
    with _LOCK:
        _HEALTH[pid]["invalid"] = True


def health(pid=None, model=None):
    with _LOCK:
        if pid is not None:
            return dict(_HEALTH.get(_key(pid, model), {}))
        return {k: dict(v) for k, v in _HEALTH.items()}


def reset_health():
    global _SERVED
    with _LOCK:
        _HEALTH.clear()
        _SERVED = None


def classify_error(exc):
    code = getattr(exc, "code", None) if isinstance(exc, urllib.error.HTTPError) else None
    if code in (401, 403):
        return "auth"
    if code in (400, 422):
        return "bad_request"
    return "transient"


def cooldown_remaining(pid, now=None, model=None):
    """Seconds until a transient-error cooldown ends (0 when none)."""
    h = health(pid, model)
    if not h.get("last_error_at") or h.get("invalid"):
        return 0.0
    if h.get("last_ok_at") and h["last_ok_at"] >= h["last_error_at"]:
        return 0.0
    t = now if now is not None else _now()
    return max(0.0, COOLDOWN - (t - h["last_error_at"]))


def is_healthy(pid, now=None, model=None):
    h = health(pid, model)
    if h.get("invalid"):
        return False
    if not h.get("last_error_at"):
        return True
    if h.get("last_ok_at") and h["last_ok_at"] >= h["last_error_at"]:
        return True
    return cooldown_remaining(pid, now, model) <= 0


def _available_models(pid, now=None):
    """Cascade entries of pid that may be tried now. Provider-level invalid/cooldown blocks all of them."""
    models = models_for(pid)
    if not models:
        return [None] if is_healthy(pid, now) else []
    if not is_healthy(pid, now):
        return []
    return [m for m in models if is_healthy(pid, now, m)]


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
    healthy = [p for p in usable if _available_models(p, now)]
    if skip_unhealthy:
        return healthy
    sick = [p for p in usable if p not in healthy]
    return healthy + sick


def call(fn, capability="chat", preferred=None, configured=None, log=print, now=None, before_attempt=None):
    """Run fn(pid) against healthy providers in router order. Returns the first success.

    For a provider with a model cascade (gemini), each cascade model is tried in order, skipping
    models in cooldown. A retryable failure moves to the next model; only when every model failed
    does the next provider get its turn ("gemini exhausted"). fn reads the model being tried via
    active_model(pid). last_served() then reports the (provider, model) that answered.

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
        waits = []
        for p in usable:
            if health(p).get("invalid"):
                continue
            ms = models_for(p) or [None]
            waits.append(min(cooldown_remaining(p, now, m) for m in ms))
        wait = min(waits) if waits else 0
        raise RuntimeError(f"llm providers are cooling down after errors; retry in about {int(wait) + 1}s")
    last = None
    for pid in order:
        all_models = models_for(pid)
        tried = 0
        for model in _available_models(pid, now):
            if before_attempt is not None:
                before_attempt()
            _TLS.active = (pid, model)
            tried += 1
            try:
                out = fn(pid)
                record_ok(pid, now, model)
                global _SERVED
                with _LOCK:
                    _SERVED = (pid, model)
                label = f"{pid}/{model}" if model else pid
                log(f"[llm] served by {label} ({capability})")
                return out
            except budget.BudgetExceeded:
                raise
            except Exception as e:
                last = e
                kind = classify_error(e)
                label = f"{pid}/{model}" if model else pid
                if kind == "bad_request":
                    log(f"[llm] {label} rejected the request: {str(e)[:200]}")
                    raise
                if kind == "auth":
                    record_invalid(pid, e, now)
                    log(f"[llm] {label} failed ({kind}): {str(e)[:200]}")
                    break  # the key is shared by every model of this provider
                record_error(pid, e, now, model)
                log(f"[llm] {label} failed ({kind}): {str(e)[:200]}")
            finally:
                _TLS.active = None
        if all_models and tried:
            log(f"[llm] gemini cascade exhausted after {tried} model(s); trying next provider")
    raise RuntimeError(f"all providers failed: {last}")


def readiness(now=None):
    """Which providers are configured and their last health. No key values."""
    out = {}
    for pid in PROVIDERS:
        h = health(pid)
        out[pid] = {
            "configured": is_configured(pid),
            "healthy": is_healthy(pid),
            "last_error_at": h.get("last_error_at"),
        }
        if models_for(pid):
            out[pid]["models"] = model_states(pid, now)
    return out


def model_states(pid, now=None):
    """Per cascade model for pid: real health from router state (no fake rows). Order = cascade order."""
    rows = []
    for m in models_for(pid):
        h = health(pid, m)
        rows.append({
            "model": m,
            "healthy": is_healthy(pid, now, m),
            "cooldown_s": int(cooldown_remaining(pid, now, m)),
            "last_ok_at": h.get("last_ok_at"),
            "last_error_at": h.get("last_error_at"),
            "last_error": h.get("last_error"),
            "last_used": last_served() == (pid, m),
            "tried": bool(h.get("last_ok_at") or h.get("last_error_at")),
        })
    return rows
