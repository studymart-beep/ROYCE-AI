import threading
import time
import pytest
from app.agent import budget, router, orchestrator


@pytest.fixture(autouse=True)
def clean():
    budget.reset()
    router.reset_health()
    yield
    budget.reset()
    router.reset_health()


def test_cap_is_30_per_hour():
    assert budget.CAP == 30 and budget.WINDOW == 3600


def test_foreground_and_background_share_one_chat_budget():
    now = time.time()
    tok = budget.bind(42)
    try:
        for _ in range(budget.CAP):
            budget.consume(now=now)
        with pytest.raises(budget.BudgetExceeded):
            budget.consume(now=now)
    finally:
        budget.unbind(tok)


def test_unbound_work_cannot_bypass_the_cap():
    now = time.time()
    for _ in range(budget.CAP):
        budget.consume(now=now)  # no chat bound: charged to the shared SYSTEM bucket
    with pytest.raises(budget.BudgetExceeded):
        budget.consume(now=now)


def test_retry_loop_consumes_per_attempt(monkeypatch):
    now = time.time()
    tok = budget.bind(5)
    try:
        def boom():
            raise RuntimeError("upstream down")
        calls = []
        def runner(inp):
            calls.append(1)
            boom()  # run_step reserves a slot before every attempt, retries included
        monkeypatch.setattr(orchestrator, "ENGINES", {"x": ([], runner, "text")})
        with pytest.raises(Exception):
            orchestrator.run_step({"engine": "x", "input": {}}, sleep=lambda s: None)
        assert len(calls) == orchestrator.MAX_RETRIES + 1
        assert budget.used(5, now=now) == orchestrator.MAX_RETRIES + 1
    finally:
        budget.unbind(tok)


def test_budget_exceeded_is_not_retried(monkeypatch):
    now = time.time()
    tok = budget.bind(6)
    try:
        for _ in range(budget.CAP):
            budget.consume(now=now)
        calls = []
        def runner(inp):
            calls.append(1)
            return "x"
        monkeypatch.setattr(orchestrator, "ENGINES", {"x": ([], runner, "text")})
        with pytest.raises(budget.BudgetExceeded):
            orchestrator.run_step({"engine": "x", "input": {}}, sleep=lambda s: None)
        assert calls == []  # the runner never ran: the reservation was refused first
    finally:
        budget.unbind(tok)


def test_exhausted_budget_gives_retry_timing():
    now = time.time()
    tok = budget.bind(7)
    try:
        for _ in range(budget.CAP):
            budget.consume(now=now - 1000)  # old entries: oldest expires soon
        with pytest.raises(budget.BudgetExceeded) as e:
            budget.consume(now=now)
        assert e.value.retry_after is not None and e.value.retry_after > 0
        assert "retry" in str(e.value).lower() or "hour" in str(e.value).lower()
    finally:
        budget.unbind(tok)


def test_concurrent_calls_cannot_exceed_cap():
    now = time.time()
    tok = budget.bind(9)
    allowed = []
    lock = threading.Lock()
    def worker():
        t = budget.bind(9)  # each thread binds the chat it works for (contextvars are per thread)
        try:
            budget.consume(now=now)
            with lock:
                allowed.append(1)
        except budget.BudgetExceeded:
            pass
        finally:
            budget.unbind(t)
    try:
        threads = [threading.Thread(target=worker) for _ in range(200)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(allowed) == budget.CAP
        assert budget.used(9, now=now) == budget.CAP
    finally:
        budget.unbind(tok)
