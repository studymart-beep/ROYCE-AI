"""Task memory: every piece of work the user gives Royce is written down until it is done.

Stored in tasks.json at the project root (git-ignored). Statuses:
  open         accepted, being worked on or waiting for a reply
  done         finished (sync answers are done when sent; async work when the user is pinged)
  unavailable  Royce cannot do it yet (missing data, connector not connected). It stays here
               with a `check` blob and the bot re-tests it every few minutes. When the check
               passes, the user is told and the task is marked done.

check blobs:
  {"type": "collector", "name": "meta"}   done when that connector is configured
  {"type": "any_collector"}               done when any ad platform is configured
  {"type": "image_provider"}              done when the image provider answers again
  {"type": "manual"}                      never auto-resolves; the user sees it in /tasks
"""
import json, os, threading, uuid, datetime
from .agent import audit

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import DATA_DIR
PATH = os.path.join(DATA_DIR, "tasks.json")
STATUSES = ("open", "done", "unavailable")
_LOCK = threading.Lock()


def _now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def _load():
    try:
        with open(PATH) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, ValueError):
        return []


def _save(items):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, indent=2)
    os.replace(tmp, PATH)


def add_task(chat_id, description, status="open", check=None, note=""):
    if status not in STATUSES:
        raise ValueError(f"bad status {status}")
    with _LOCK:
        items = _load()
        t = {
            "id": uuid.uuid4().hex[:8],
            "chat_id": chat_id,
            "description": (description or "").strip()[:500],
            "status": status,
            "created_at": _now(),
            "note": note or "",
            "check": check,
        }
        items.append(t)
        _save(items)
        audit.record("bot", "task_created", f"task:{t['id']}", status)
        return dict(t)


def list_tasks(chat_id=None, status=None):
    items = _load()
    out = []
    for t in items:
        if chat_id is not None and t.get("chat_id") != chat_id:
            continue
        if status is not None and t.get("status") != status:
            continue
        out.append(t)
    return out


def get_task(task_id):
    for t in _load():
        if t.get("id") == task_id:
            return t
    return None


def _set(task_id, **fields):
    with _LOCK:
        items = _load()
        for t in items:
            if t.get("id") == task_id:
                t.update(fields)
                _save(items)
                return dict(t)
    raise KeyError(f"no task {task_id}")


def complete_task(task_id, note=""):
    out = _set(task_id, status="done", note=note or "done")
    audit.record("bot", "task_completed", f"task:{task_id}", "done")
    return out


def mark_unavailable(task_id, note, check=None):
    return _set(task_id, status="unavailable", note=note, check=check)


def open_tasks():
    return list_tasks(status="open")


def unavailable_tasks():
    return list_tasks(status="unavailable")
