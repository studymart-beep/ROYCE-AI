from . import audit
"""Approval queue: sends and other approval-gated actions wait here until /approve <id>.

Stored in approvals.json at the project root (git-ignored). Each record is bound to the
chat that queued it, so one chat cannot approve another chat's action.
Executors are registered by action class. An action with no executor is refused on approve.
"""
import json, os, threading, time, uuid
from . import permissions

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(BASE, "approvals.json")
_LOCK = threading.Lock()
_EXECUTORS = {}  # action_class -> callable(payload) -> result
PENDING, DONE, FAILED = "pending", "done", "failed"


def register_executor(action_class, fn):
    _EXECUTORS[action_class] = fn


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, PATH)


def queue(chat_id, action_class, payload, summary=""):
    """Store an action that needs approval. Returns the record. Refuses classes that do not need approval."""
    if not permissions.needs_approval(action_class):
        raise permissions.PermissionError_(f"'{action_class}' does not need approval; run it directly")
    with _LOCK:
        d = _load()
        rid = uuid.uuid4().hex[:6]
        rec = {"id": rid, "chat_id": chat_id, "action_class": action_class, "payload": payload,
               "summary": (summary or "")[:300], "status": PENDING, "created_at": time.time(), "result": None}
        d[rid] = rec
        _save(d)
        return dict(rec)


def pending(chat_id=None):
    d = _load()
    return [dict(r) for r in d.values() if r.get("status") == PENDING and (chat_id is None or r.get("chat_id") == chat_id)]


def approve(chat_id, rid):
    """Execute a pending action. Only the chat that queued it may approve. Returns (ok, message)."""
    with _LOCK:
        d = _load()
        rec = d.get(rid)
        if not rec:
            return False, f"no pending action #{rid}"
        if rec.get("chat_id") != chat_id:
            return False, f"no pending action #{rid}"
        if rec.get("status") != PENDING:
            return False, f"#{rid} is already {rec.get('status')}"
        fn = _EXECUTORS.get(rec["action_class"])
        if fn is None:
            return False, f"no executor registered for '{rec['action_class']}', nothing was sent"
        rec["status"] = PENDING  # held while running
        _save(d)
    try:
        result = fn(rec["payload"])
        status, msg = DONE, f"approved #{rid}: done"
        audit.record("user", "approval_granted", rec["action_class"], "done")
    except Exception as e:
        result, status, msg = str(e)[:300], FAILED, f"approved #{rid} but it failed: {str(e)[:200]}"
    with _LOCK:
        d = _load()
        d[rid]["status"] = status
        d[rid]["result"] = str(result)[:300] if result is not None else None
        _save(d)
    return status == DONE, msg


def reject(chat_id, rid):
    with _LOCK:
        d = _load()
        rec = d.get(rid)
        if not rec or rec.get("chat_id") != chat_id or rec.get("status") != PENDING:
            return False
        rec["status"] = "rejected"
        _save(d)
        return True
