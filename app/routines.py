"""Routines: the background jobs the system already runs (read-only, from logs/health.json and the
job constants) plus user-created reminders stored in routines.json. A user reminder is a name, a
schedule hint (free text, shown back to the user) and an action note. The bot loop does not execute
schedule hints yet; the store says so through the 'runs_automatically' flag, which is always False.
"""
import datetime
import json
import os
import threading
import uuid

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "routines.json")
_lock = threading.Lock()
MAX_NAME = 80
MAX_NOTE = 500


def _now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _load():
    if not os.path.exists(PATH):
        return {}
    try:
        with open(PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(data):
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PATH)


def create(name, schedule="", note=""):
    name = " ".join(str(name or "").split())
    if not name:
        raise ValueError("routine name is required")
    if len(name) > MAX_NAME:
        raise ValueError(f"routine name must be {MAX_NAME} characters or fewer")
    rec = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "schedule": " ".join(str(schedule or "").split())[:120],
        "note": str(note or "").strip()[:MAX_NOTE],
        "created_at": _now(),
        "runs_automatically": False,
    }
    with _lock:
        data = _load()
        data[rec["id"]] = rec
        _save(data)
    return rec


def list_user():
    with _lock:
        items = list(_load().values())
    return sorted(items, key=lambda r: r.get("created_at", ""))


def delete(rid):
    with _lock:
        data = _load()
        if rid not in data:
            return False
        del data[rid]
        _save(data)
    return True


def system_jobs(health_path=None):
    """The background jobs the server runs, with the last self-check result from logs/health.json."""
    from .agent import jobs
    from .agent.jobs import BACKUP_EVERY, CHECK_EVERY
    latest = None
    try:
        latest = jobs.latest(health_path)
    except Exception:
        latest = None
    last_check = (latest or {}).get("checked_at") or (latest or {}).get("at") or None
    return [
        {"id": "self-check", "name": "Health self-check", "every_seconds": CHECK_EVERY,
         "last_run": last_check, "last_ok": (latest or {}).get("ok")},
        {"id": "backup", "name": "Database backup", "every_seconds": BACKUP_EVERY,
         "last_run": (latest or {}).get("last_backup"), "last_ok": None},
    ]
