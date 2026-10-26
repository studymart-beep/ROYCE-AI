import subprocess, sys, threading, time, urllib.error
import pytest
from fastapi.testclient import TestClient

from app import main, bot_runner
from app import telegram_bot as tb


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    # never touch the real token, .env or state files; restore globals after each test
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:test-not-real")
    monkeypatch.setattr(tb, "TOKEN", None)
    monkeypatch.setattr(tb, "API", None)
    monkeypatch.setattr(tb, "BASE", str(tmp_path))
    monkeypatch.setattr(bot_runner, "_thread", None)
    bot_runner._stop.clear()
    yield
    bot_runner.stop_bot_thread(timeout=5)


def _unreachable(*a, **k):
    raise urllib.error.URLError("telegram unreachable (test)")


def test_api_healthz_ok_when_telegram_unreachable(monkeypatch):
    monkeypatch.setattr(tb.urllib.request, "urlopen", _unreachable)
    monkeypatch.setattr(bot_runner, "RETRY_BACKOFF", (0.01,))
    with TestClient(main.app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json() == {"ok": True}
        time.sleep(0.05)
        assert c.get("/healthz").status_code == 200
    assert not bot_runner.running()


def test_api_healthz_ok_when_token_rejected(monkeypatch):
    def rejected(*a, **k):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    monkeypatch.setattr(tb.urllib.request, "urlopen", rejected)
    monkeypatch.setattr(bot_runner, "RETRY_BACKOFF", (0.01,))
    with TestClient(main.app) as c:
        assert c.get("/healthz").status_code == 200


def test_lifespan_starts_exactly_one_poll_thread_even_when_started_twice(monkeypatch):
    calls = []
    def fake_setup():
        return None
    def fake_poll(stop_event):
        calls.append(threading.current_thread().name)
        stop_event.wait(5)
    monkeypatch.setattr(tb, "_setup_bot", fake_setup)
    monkeypatch.setattr(tb, "poll_forever", fake_poll)
    first = bot_runner.start_bot_thread()
    second = bot_runner.start_bot_thread()  # duplicate startup (reload/second worker in same process)
    assert first is not None and first is second
    time.sleep(0.1)
    assert len(calls) == 1
    assert sum(1 for t in threading.enumerate() if t.name == "royce-telegram-poll") == 1
    bot_runner.stop_bot_thread(timeout=5)


def test_lifespan_no_token_starts_no_thread(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(tb, "BASE", "/nonexistent-royce-test-dir")
    assert bot_runner.start_bot_thread() is None
    assert not bot_runner.running()


def test_shutdown_stops_and_joins_thread(monkeypatch):
    ended = threading.Event()
    def fake_poll(stop_event):
        stop_event.wait(30)
        ended.set()
    monkeypatch.setattr(tb, "_setup_bot", lambda: None)
    monkeypatch.setattr(tb, "poll_forever", fake_poll)
    with TestClient(main.app):
        time.sleep(0.05)
        assert bot_runner.running()
    # lifespan exit must have set the stop event and joined the thread
    assert ended.is_set()
    assert not bot_runner.running()


def test_stop_bot_thread_joins_within_timeout(monkeypatch):
    monkeypatch.setattr(tb, "_setup_bot", lambda: None)
    monkeypatch.setattr(tb, "poll_forever", lambda ev: ev.wait(30))
    t = bot_runner.start_bot_thread()
    assert t.is_alive()
    t0 = time.time()
    assert bot_runner.stop_bot_thread(timeout=5) is True
    assert time.time() - t0 < 5
    assert not t.is_alive()


def test_poll_forever_returns_when_stop_event_set(monkeypatch):
    ev = threading.Event()
    def fake_updates(offset=None):
        ev.set()  # request stop during the first poll; loop must exit after it
        return {"result": []}
    monkeypatch.setattr(tb, "get_updates", fake_updates)
    monkeypatch.setattr(tb, "retry_unavailable", lambda: None)
    monkeypatch.setattr(tb.leadbrain, "run_nudge_check", lambda now: None)
    tb.poll_forever(ev)  # must return, not spin


def test_standalone_entry_importable_and_callable(monkeypatch):
    assert callable(tb.main) and callable(tb.poll_forever) and callable(tb._setup_bot)
    seen = {}
    monkeypatch.setattr(tb, "_setup_bot", lambda: seen.setdefault("setup", True))
    monkeypatch.setattr(tb, "poll_forever", lambda ev: seen.setdefault("poll", ev))
    import signal
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    tb.main()
    assert seen["setup"] and isinstance(seen["poll"], threading.Event)
    out = subprocess.run([sys.executable, "-c", "import app.telegram_bot, app.bot_runner"],
                         cwd=bot_runner.__file__.rsplit("/app/", 1)[0], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr


def test_kill_switch_disables_in_process_bot(monkeypatch):
    monkeypatch.setenv("ROYCE_TELEGRAM_IN_PROCESS", "0")
    assert bot_runner.enabled() is False
    assert bot_runner.start_bot_thread() is None
    assert not bot_runner.running()


def test_kill_switch_default_on(monkeypatch):
    monkeypatch.delenv("ROYCE_TELEGRAM_IN_PROCESS", raising=False)
    assert bot_runner.enabled() is True
