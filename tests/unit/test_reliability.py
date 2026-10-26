import threading
import urllib.error
import pytest
from app.agent import router, budget


@pytest.fixture(autouse=True)
def one_model_cascade(monkeypatch):
    # these tests cover provider-level failover; pin gemini to one model so the cascade is a no-op here
    monkeypatch.setenv("GEMINI_MODELS", "gemini-a")
    monkeypatch.delenv("GEMINI_MODEL", raising=False)


@pytest.fixture(autouse=True)
def clean():
    router.reset_health()
    budget.reset()
    yield
    router.reset_health()
    budget.reset()


def both(p):
    return True


def http(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def noop(*a):
    pass


# ---------- A1: cooldown skip, failover, all-unavailable, recovery ----------

def test_cooling_provider_is_skipped_not_called(tmp_path):
    router.record_error("gemini", "503", now=1000.0)
    calls = []
    def fn(pid):
        calls.append(pid)
        return "ok"
    out = router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1010.0)
    assert out == "ok"
    assert calls == ["openrouter"]  # gemini never called while cooling down


def test_fallback_selected_when_primary_cooling(monkeypatch):
    router.record_error("gemini", "429", now=1000.0)
    assert router.pick_order("chat", "gemini", configured=both, now=1001.0, skip_unhealthy=True) == ["openrouter"]


def test_all_unavailable_gives_actionable_wait_error():
    router.record_error("gemini", "503", now=1000.0)
    router.record_error("openrouter", "timeout", now=1000.0)
    calls = []
    with pytest.raises(RuntimeError, match="cooling down after errors; retry in about"):
        router.call(lambda pid: calls.append(pid), capability="chat", configured=both, log=noop, now=1010.0)
    assert calls == []


def test_all_keys_rejected_is_reported_as_invalid_not_cooldown():
    router.record_invalid("gemini", http(401), now=1000.0)
    router.record_invalid("openrouter", http(403), now=1000.0)
    with pytest.raises(RuntimeError, match="rejected their api key"):
        router.call(lambda pid: "x", capability="chat", configured=both, log=noop, now=1010.0)


def test_cooldown_expiry_restores_provider():
    router.record_error("gemini", "503", now=1000.0)
    later = 1000.0 + router.COOLDOWN + 1
    assert router.is_healthy("gemini", now=later) is True
    calls = []
    router.call(lambda pid: calls.append(pid) or "ok", capability="chat", preferred="gemini",
                configured=both, log=noop, now=later)
    assert calls == ["gemini"]


def test_recovery_after_success_clears_cooldown():
    router.record_error("gemini", "503", now=1000.0)
    router.record_ok("gemini", now=1002.0)
    assert router.cooldown_remaining("gemini", now=1003.0) == 0.0
    assert router.is_healthy("gemini", now=1003.0) is True


def test_failed_attempt_triggers_cooldown_and_failover_once():
    calls = []
    def fn(pid):
        calls.append(pid)
        if pid == "gemini":
            raise RuntimeError("503 overloaded")
        return "from openrouter"
    assert router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0) == "from openrouter"
    assert calls == ["gemini", "openrouter"]
    calls.clear()
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1010.0)
    assert calls == ["openrouter"]  # no repeat call to gemini while it cools down


def test_bad_request_raises_without_failover_or_cooldown():
    calls = []
    def fn(pid):
        calls.append(pid)
        raise http(400)
    with pytest.raises(urllib.error.HTTPError):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert calls == ["gemini"]
    assert router.is_healthy("gemini", now=1001.0) is True


def test_auth_error_marks_invalid_and_skips_until_success():
    def fn(pid):
        if pid == "gemini":
            raise http(401)
        return "ok"
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0)
    assert router.health("gemini")["invalid"] is True
    assert router.is_healthy("gemini", now=10 ** 9) is False  # invalid is not time-limited
    router.record_ok("gemini", now=10 ** 9)
    assert router.is_healthy("gemini", now=10 ** 9) is True


# ---------- A2: shared budget ----------

def test_budget_blocks_at_cap_with_retry_timing():
    for i in range(budget.CAP):
        budget.consume(now=1000.0 + i, chat_id=7)
    with pytest.raises(budget.BudgetExceeded) as e:
        budget.consume(now=1100.0, chat_id=7)
    assert e.value.retry_after is not None and e.value.retry_after > 0
    assert "try again in about" in str(e.value)


def test_budget_window_slides():
    for i in range(budget.CAP):
        budget.consume(now=1000.0, chat_id=7)
    budget.consume(now=1000.0 + budget.WINDOW + 1, chat_id=7)  # old entries expired
    assert budget.used(7, now=1000.0 + budget.WINDOW + 1) == 1


def test_unbound_calls_are_charged_to_system_bucket():
    for _ in range(budget.CAP):
        budget.consume(now=1000.0)
    with pytest.raises(budget.BudgetExceeded):
        budget.consume(now=1000.0)  # background work cannot bypass the cap


def test_bound_chat_is_charged_by_bind():
    token = budget.bind(55)
    try:
        budget.consume(now=1000.0)
    finally:
        budget.unbind(token)
    assert budget.used(55, now=1000.0) == 1
    assert budget.used(budget.SYSTEM, now=1000.0) == 0


def test_router_charges_every_provider_attempt_including_failover():
    charges = []
    def fn(pid):
        if pid == "gemini":
            raise RuntimeError("503")
        return "ok"
    router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop, now=1000.0,
                before_attempt=lambda: charges.append(1))
    assert len(charges) == 2  # the failed attempt and the fallback both reserved a slot


def test_budget_exceeded_stops_router_without_failover():
    tried = []
    def fn(pid):
        tried.append(pid)
        return "ok"
    def refuse():
        raise budget.BudgetExceeded(60)
    with pytest.raises(budget.BudgetExceeded):
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=noop,
                    now=1000.0, before_attempt=refuse)
    assert tried == []


def test_concurrent_calls_never_exceed_cap():
    ok = []
    lock = threading.Lock()
    def worker():
        for _ in range(10):
            try:
                budget.consume(now=1000.0, chat_id=9)
                with lock:
                    ok.append(1)
            except budget.BudgetExceeded:
                pass
    threads = [threading.Thread(target=worker) for _ in range(12)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert len(ok) == budget.CAP
    assert budget.used(9, now=1000.0) == budget.CAP


def test_orchestrator_retry_loop_cannot_bypass_budget(monkeypatch):
    from app.agent import orchestrator as o
    ran = []
    monkeypatch.setitem(o.ENGINES, "script", (["product"], lambda i: ran.append(1) or {"a": 1}, "dict"))
    import time
    now = time.time()
    for _ in range(budget.CAP):
        budget.consume(now=now, chat_id=3)
    token = budget.bind(3)
    try:
        with pytest.raises(budget.BudgetExceeded):
            o.run_step({"engine": "script", "input": {"product": "x"}}, sleep=noop)
    finally:
        budget.unbind(token)
    assert ran == []
