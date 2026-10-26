"""Background jobs: scheduled backups and an hourly self-check.

start() launches one daemon thread inside the bot process. It sleeps in short steps so it
can stop cleanly, and it never raises into the bot loop: each job is wrapped and its error
is recorded in logs/health.json and the audit log.

  every 6 hours  db_backup (tools.db_backup, keeps the newest 10)
  every 1 hour   self_check: llm providers configured, db reachable, disk space free

logs/health.json holds only the latest self-check (overwritten each run, atomic replace).
No keys or message text are written there.
"""
import datetime
import json
import os
import shutil
import sqlite3
import threading
import time

from . import audit, router, tools
from ..config import PROVIDERS, provider_status

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEALTH_PATH = os.path.join(BASE, "logs", "health.json")
BACKUP_EVERY = 6 * 3600
CHECK_EVERY = 3600
MIN_FREE_BYTES = 200 * 1024 * 1024  # warn below 200 MB free
TICK = 30

_LOCK = threading.Lock()
_STOP = threading.Event()
_THREAD = None


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def self_check(health_path=None):
    """Run the lightweight checks and return the result dict (also written to health.json)."""
    checks = {}
    providers = {}
    for pid in PROVIDERS:
        providers[pid] = {"configured": router.is_configured(pid), "healthy": router.is_healthy(pid)}
    checks["llm_providers"] = {"ok": any(p["configured"] for p in providers.values()), "detail": providers}

    try:
        conn = sqlite3.connect(f"file:{tools.DB_PATH}?mode=ro", uri=True, timeout=5)
        conn.execute("select 1").fetchone()
        conn.close()
        checks["db"] = {"ok": True, "detail": "reachable"}
    except Exception as e:
        checks["db"] = {"ok": False, "detail": str(e)[:160]}

    try:
        free = shutil.disk_usage(BASE).free
        checks["disk"] = {"ok": free >= MIN_FREE_BYTES, "detail": {"free_mb": free // (1024 * 1024)}}
    except Exception as e:
        checks["disk"] = {"ok": False, "detail": str(e)[:160]}

    result = {
        "checked_at": _now(),
        "ok": all(c["ok"] for c in checks.values()),
        "checks": checks,
        "last_backup": _last_backup_note(),
    }
    _write_health(result, health_path)
    audit.record("job", "self_check", "health.json", "ok" if result["ok"] else "degraded")
    return result


def _last_backup_note():
    folder = os.path.join(BASE, "backups")
    try:
        files = sorted(f for f in os.listdir(folder) if f.startswith("crm-") and f.endswith(".db"))
    except FileNotFoundError:
        return None
    return files[-1] if files else None


def _write_health(result, path=None):
    target = path or HEALTH_PATH
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with _LOCK:
        tmp = target + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
        os.replace(tmp, target)


def latest(path=None):
    """Latest self-check dict, or None when none has run yet."""
    try:
        with open(path or HEALTH_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run_backup():
    try:
        out = tools.db_backup()
        audit.record("job", "backup_run", os.path.basename(out["file"]), "ok")
        return out
    except Exception as e:
        audit.record("job", "backup_run", "crm.db", f"failed: {str(e)[:60]}")
        return None


def _loop(backup_every, check_every):
    last_backup = 0.0
    last_check = 0.0
    while not _STOP.is_set():
        now = time.time()
        if now - last_check >= check_every:
            last_check = now
            try:
                self_check()
            except Exception as e:
                audit.record("job", "self_check", "health.json", f"failed: {str(e)[:60]}")
        if now - last_backup >= backup_every:
            last_backup = now
            run_backup()
        _STOP.wait(TICK)


def start(backup_every=BACKUP_EVERY, check_every=CHECK_EVERY):
    """Start the job thread once. Safe to call twice; returns the thread."""
    global _THREAD
    if _THREAD and _THREAD.is_alive():
        return _THREAD
    _STOP.clear()
    _THREAD = threading.Thread(target=_loop, args=(backup_every, check_every),
                               name="royce-jobs", daemon=True)
    _THREAD.start()
    return _THREAD


def stop():
    _STOP.set()


def format_status(result=None):
    """Plain text for the /health bot command. No keys, no raw dumps."""
    r = result if result is not None else latest()
    ps = provider_status()
    lines = []
    for pid in PROVIDERS:
        conf = bool(ps.get(pid, {}).get("configured"))
        healthy = router.is_healthy(pid) if conf else False
        state = "configured, healthy" if conf and healthy else ("configured, recent errors" if conf else "not configured")
        lines.append(f"{pid}: {state}")
    if not r:
        lines.append("self-check: not run yet (first one runs within a minute of start, then hourly)")
        return "\n".join(lines)
    lines.append(f"self-check {r.get('checked_at')}: {'all good' if r.get('ok') else 'needs attention'}")
    checks = r.get("checks") or {}
    db = checks.get("db") or {}
    lines.append(f"database: {'reachable' if db.get('ok') else 'FAILED'}")
    disk = checks.get("disk") or {}
    free = (disk.get("detail") or {}).get("free_mb") if isinstance(disk.get("detail"), dict) else None
    lines.append(f"disk: {'ok' if disk.get('ok') else 'LOW'}" + (f", {free} MB free" if free is not None else ""))
    lines.append(f"last backup: {r.get('last_backup') or 'none yet'}")
    return "\n".join(lines)
