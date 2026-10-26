"""Deletion precautions for suspicious-link flags. Off by default. Never automatic.

Flow, only when /precautions is on for a group:
  1. a link_suspicious flag is recorded (tgmod)
  2. the bot queues a delete approval bound to the OWNER's private chat, showing the flagged message id and
     the group. Nothing is deleted or restricted here.
  3. the owner sends /approve <id> in the owner DM, and only then the executor runs:
     deleteMessage, and when the owner asked for a restriction, restrictChatMember on the sender.

Audit records ids and outcomes only, never message text. The bot needs admin rights in the group
(Telegram checks that when the call is made; a refused call is reported as failed).
"""
import json
import os
import threading

from .agent import approvals, audit
from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "tgprecautions.json")
_LOCK = threading.Lock()
CATEGORY = "link_suspicious"

_API = {"fn": None}  # set by the bot: fn(method, **params) -> telegram response dict


def set_api(fn):
    _API["fn"] = fn


def _load(path=None):
    try:
        with open(path or PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data, path=None):
    p = path or PATH
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, p)


def is_on(group_id, path=None):
    return bool(_load(path).get(str(group_id), {}).get("enabled", False))


def set_on(group_id, on, path=None):
    with _LOCK:
        d = _load(path)
        d.setdefault(str(group_id), {})["enabled"] = bool(on)
        _save(d, path)
        return bool(on)


def queue_for_flag(owner_chat_id, group_id, group_title, message_id, sender_id, flag_id, path=None):
    """Called after a link_suspicious flag is recorded. Queues an approval only if precautions are on.
    Returns the approval id, or None when precautions are off (no action of any kind is taken)."""
    if not is_on(group_id, path=path):
        return None
    rec = approvals.queue(owner_chat_id, "delete",
                          {"group_id": group_id, "message_id": message_id, "sender_id": sender_id,
                           "flag_id": flag_id, "restrict": False},
                          summary=f"delete flagged link (flag #{flag_id}) in {group_title or group_id}")
    audit.record("bot", "precaution_queued", f"flag={flag_id} group={group_id}", "pending")
    return rec["id"]


def execute(payload):
    """Approval executor for 'delete'. Runs only after the owner's /approve. Returns a short status."""
    fn = _API["fn"]
    if fn is None:
        raise RuntimeError("api not configured")
    gid, mid = payload["group_id"], payload["message_id"]
    res = fn("deleteMessage", chat_id=gid, message_id=mid)
    if not (res or {}).get("ok"):
        audit.record("bot", "precaution_delete", f"flag={payload.get('flag_id')} group={gid}", "failed")
        raise RuntimeError("telegram refused the delete (is the bot an admin with delete rights?)")
    audit.record("bot", "precaution_delete", f"flag={payload.get('flag_id')} group={gid}", "done")
    out = "deleted the flagged message."
    if payload.get("restrict") and payload.get("sender_id"):
        r2 = fn("restrictChatMember", chat_id=gid, user_id=payload["sender_id"],
                permissions={"can_send_messages": False})
        audit.record("bot", "precaution_restrict", f"flag={payload.get('flag_id')} group={gid}",
                     "done" if (r2 or {}).get("ok") else "failed")
        out += " restricted the sender." if (r2 or {}).get("ok") else " restriction failed."
    return out


approvals.register_executor("delete", execute)
