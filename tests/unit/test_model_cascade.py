import urllib.error
import pytest
from app import config
from app.agent import router, budget

MODELS = ["gemini-a", "gemini-b", "gemini-c"]


@pytest.fixture(autouse=True)
def env(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", ",".join(MODELS))
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    router.reset_health()
    budget.reset()
    yield
    router.reset_health()
    budget.reset()


def both(p):
    return True


def noop(*a):
    pass


def http(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def recorder(fail=(), fail_exc=None):
    """fn(pid) that records (pid, model) and raises for models in fail."""
    seen = []

    def fn(pid):
        m = router.active_model(pid)
        seen.append((pid, m))
        if (pid, m) in fail or m in fail:
            raise (fail_exc or RuntimeError("503 overloaded"))
        return f"ok from {pid}/{m}"
    return fn, seen


# ---- env parsing ----

def test_env_list_is_parsed_in_order_trimmed_and_deduped(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", " gemini-x , ,gemini-y,gemini-x ,, gemini-z ")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.gemini_models() == ["gemini-x", "gemini-y", "gemini-z"]


def test_default_cascade_when_env_unset(monkeypatch):
    monkeypatch.delenv("GEMINI_MODELS", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.gemini_models() == ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.5-pro"]


def test_single_model_env_becomes_first_entry(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-b")
    assert config.gemini_models() == ["gemini-b", "gemini-a", "gemini-c"]


def test_single_model_env_already_listed_is_not_duplicated(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "gemini-c")
    assert config.gemini_models() == ["gemini-c", "gemini-a", "gemini-b"]


def test_blank_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GEMINI_MODELS", " , ")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    assert config.gemini_models()[0] == "gemini-2.5-flash"


def test_parse_model_list_handles_none_and_empty():
    assert config.parse_model_list(None) == []
    assert config.parse_model_list("") == []


# ---- cascade order ----

def test_cascade_tries_models_in_order_and_stops_on_first_success():
    fn, seen = recorder(fail={"gemini-a"})
    out = router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert out == "ok from gemini/gemini-b"
    assert seen == [("gemini", "gemini-a"), ("gemini", "gemini-b")]


def test_first_model_success_never_touches_others():
    fn, seen = recorder()
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert seen == [("gemini", "gemini-a")]
    assert router.last_served() == ("gemini", "gemini-a")


def test_last_served_reports_the_answering_model():
    fn, _ = recorder(fail={"gemini-a", "gemini-b"})
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert router.last_served() == ("gemini", "gemini-c")


def test_active_model_is_provider_default_outside_a_call(monkeypatch):
    assert router.active_model("gemini") == config.PROVIDERS["gemini"]["model"]


# ---- per-model cooldown ----

def test_dead_model_is_skipped_while_others_still_tried():
    fn, seen = recorder(fail={"gemini-a"})
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    seen.clear()
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1010.0)
    assert seen == [("gemini", "gemini-b")]  # gemini-a is cooling, never called


def test_cooldown_is_per_model_not_per_provider():
    router.record_error("gemini", "503", now=1000.0, model="gemini-a")
    assert router.is_healthy("gemini", now=1001.0, model="gemini-a") is False
    assert router.is_healthy("gemini", now=1001.0, model="gemini-b") is True
    assert router.is_healthy("gemini", now=1001.0) is True  # provider level still open


def test_cooled_model_recovers_after_cooldown():
    router.record_error("gemini", "503", now=1000.0, model="gemini-a")
    assert router.is_healthy("gemini", now=1000.0 + router.COOLDOWN + 1, model="gemini-a") is True


def test_pick_order_keeps_gemini_while_a_model_is_still_available():
    for m in MODELS[:-1]:
        router.record_error("gemini", "503", now=1000.0, model=m)
    order = router.pick_order("chat", "gemini", configured=both, now=1001.0, skip_unhealthy=True)
    assert order == ["gemini", "openrouter"]  # gemini-c still usable


def test_all_models_cooling_removes_gemini_from_pick_order():
    for m in MODELS:
        router.record_error("gemini", "503", now=1000.0, model=m)
    order = router.pick_order("chat", "gemini", configured=both, now=1001.0, skip_unhealthy=True)
    assert order == ["openrouter"]


def test_cooldown_skip_calls_only_healthy_models_on_later_call():
    fn, seen = recorder(fail={"gemini-a", "gemini-b"})
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    seen.clear()
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1010.0)
    assert seen == [("gemini", "gemini-c")]


# ---- exhaustion then failover ----

def test_exhaustion_after_all_models_then_openrouter():
    seen = []

    def fn(pid):
        seen.append((pid, router.active_model(pid)))
        if pid == "gemini":
            raise RuntimeError("503")
        return "from openrouter"
    out = router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert out == "from openrouter"
    assert seen == [("gemini", "gemini-a"), ("gemini", "gemini-b"), ("gemini", "gemini-c"), ("openrouter", config.PROVIDERS["openrouter"]["model"])]


def test_exhaustion_is_logged_once_per_provider():
    logs = []

    def fn(pid):
        if pid == "gemini":
            raise RuntimeError("503")
        return "ok"
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=logs.append, now=1000.0)
    assert sum("cascade exhausted after 3 model(s)" in x for x in logs) == 1


def test_timeout_and_network_errors_cascade():
    seen = []

    def fn(pid):
        seen.append(router.active_model(pid))
        if len(seen) == 1:
            raise TimeoutError("timed out")
        if len(seen) == 2:
            raise urllib.error.URLError("connection reset")
        return "ok"
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert seen == ["gemini-a", "gemini-b", "gemini-c"]


def test_all_gemini_and_openrouter_fail_raises_with_last_error():
    def fn(pid):
        raise RuntimeError(f"{pid} down")
    with pytest.raises(RuntimeError, match="all providers failed"):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)


def test_exhausted_gemini_reports_cooldown_when_called_again_soon():
    def fn(pid):
        raise RuntimeError("503")
    with pytest.raises(RuntimeError):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    calls = []
    with pytest.raises(RuntimeError, match="cooling down"):
        router.call(lambda pid: calls.append(pid), capability="chat", configured=lambda p: p == "gemini",
                    log=noop, now=1001.0)
    assert calls == []


def test_budget_charged_per_model_attempt():
    charges = []
    def fn(pid):
        if pid == "gemini" and router.active_model(pid) != "gemini-c":
            raise RuntimeError("503")
        return "ok"
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0,
                before_attempt=lambda: charges.append(1))
    assert len(charges) == 3  # gemini-a, gemini-b (failed), gemini-c (served)


# ---- non-retryable short-circuit ----

def test_bad_request_stops_the_cascade_and_never_fails_over():
    seen = []

    def fn(pid):
        seen.append((pid, router.active_model(pid)))
        raise http(400)
    with pytest.raises(urllib.error.HTTPError):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert seen == [("gemini", "gemini-a")]
    assert router.health("gemini", "gemini-a").get("last_error_at") is None  # no cooldown either


def test_auth_error_skips_remaining_models_and_marks_provider_invalid():
    seen = []

    def fn(pid):
        seen.append((pid, router.active_model(pid)))
        if pid == "gemini":
            raise http(403)
        return "from openrouter"
    out = router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert out == "from openrouter"
    assert seen == [("gemini", "gemini-a"), ("openrouter", config.PROVIDERS["openrouter"]["model"])]
    assert router.health("gemini")["invalid"] is True


def test_budget_exceeded_stops_cascade_immediately():
    seen = []

    def fn(pid):
        seen.append(router.active_model(pid))
        return "ok"

    def refuse():
        raise budget.BudgetExceeded(60)
    with pytest.raises(budget.BudgetExceeded):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0,
                    before_attempt=refuse)
    assert seen == []


# ---- readiness / UI rows ----

def test_model_states_lists_real_cascade_in_order_with_health():
    fn, _ = recorder(fail={"gemini-a"})
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    rows = router.model_states("gemini", now=1001.0)
    assert [r["model"] for r in rows] == MODELS
    assert rows[0]["healthy"] is False and rows[0]["cooldown_s"] > 0 and rows[0]["tried"] is True
    assert rows[1]["healthy"] is True and rows[1]["last_used"] is True
    assert rows[2]["tried"] is False and rows[2]["last_used"] is False


def test_readiness_has_models_only_for_gemini():
    state = router.readiness(now=1001.0)
    assert [r["model"] for r in state["gemini"]["models"]] == MODELS
    assert "models" not in state["openrouter"]


def test_model_error_text_is_truncated():
    router.record_error("gemini", "x" * 1000, now=1.0, model="gemini-a")
    assert len(router.health("gemini", "gemini-a")["last_error"]) <= 200
