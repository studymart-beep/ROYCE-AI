"""Run the Telegram bot poll loop inside the web process (a daemon thread started from the FastAPI lifespan).

Why in-process: Render persistent disks attach to one service only, so the bot and the API share the
same sqlite files by living in one process. Guarantees:
- exactly one poll thread per process (start_bot_thread is guarded; a second call returns the first thread)
- startup never blocks or crashes the API: setup and polling run in the thread, and failures are logged
  and retried with a capped backoff
- clean shutdown: stop() sets the event and joins the thread with a timeout
Standalone use (python -m app.telegram_bot) does not touch this module.
"""
import os
import threading

_lock = threading.Lock()
_thread = None
_stop = threading.Event()
RETRY_BACKOFF = (5, 15, 60, 300)  # seconds between setup retries when Telegram is unreachable or rejects the token


def _run(stop_event, backoff=RETRY_BACKOFF):
    from . import telegram_bot as tb
    attempt = 0
    while not stop_event.is_set():
        try:
            tb._setup_bot()
            break
        except Exception as e:
            wait = backoff[min(attempt, len(backoff) - 1)]
            attempt += 1
            print(f"[bot] startup failed ({type(e).__name__}); api keeps running, retry in {wait}s", flush=True)
            if stop_event.wait(wait):
                return
    if stop_event.is_set():
        return
    try:
        tb.poll_forever(stop_event)
    except Exception as e:  # never let the thread die silently without a log line
        print(f"[bot] poll loop stopped: {type(e).__name__}: {e}", flush=True)


def enabled():
    """Kill switch: ROYCE_TELEGRAM_IN_PROCESS=0 keeps the API up without the in-process bot
    (e.g. when the bot is run standalone elsewhere). Default on."""
    return os.environ.get("ROYCE_TELEGRAM_IN_PROCESS", "1").strip().lower() not in ("0", "false", "no", "off")


def start_bot_thread():
    """Start the single bot thread. Returns None when no token is configured. Idempotent."""
    global _thread
    from . import telegram_bot as tb
    with _lock:
        if _thread is not None and _thread.is_alive():
            return _thread
        if not enabled():
            print("[bot] ROYCE_TELEGRAM_IN_PROCESS is off; telegram bot not started in this process", flush=True)
            return None
        if not tb.token_configured():
            print("[bot] TELEGRAM_BOT_TOKEN not set; telegram bot not started", flush=True)
            return None
        _stop.clear()
        _thread = threading.Thread(target=_run, args=(_stop,), name="royce-telegram-poll", daemon=True)
        _thread.start()
        return _thread


def stop_bot_thread(timeout=60):
    """Signal the poll loop to stop and wait up to timeout seconds. Returns True if the thread ended."""
    global _thread
    with _lock:
        t = _thread
    _stop.set()
    if t is None:
        return True
    t.join(timeout)
    alive = t.is_alive()
    if not alive:
        with _lock:
            if _thread is t:
                _thread = None
    else:
        print("[bot] poll thread still running after shutdown timeout (daemon; process exit will end it)", flush=True)
    return not alive


def running():
    t = _thread
    return t is not None and t.is_alive()
