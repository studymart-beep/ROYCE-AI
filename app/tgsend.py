"""Owner-approved sends into groups and channels.

Flow (private chat, owner only):
  1. owner says "tell the group <name>: <text>" or "post to the channel <name>: <text>"
  2. the bot resolves the target from the chats it has stored (tgstore group_meta) and drafts the exact text
  3. it queues a send_message approval and shows the exact text; the owner replies /approve <id> to send
  4. the executor calls sendMessage to that chat id only; the audit log records the id and outcome, never the text

Refused for anyone but the owner. The approval queue is chat-bound and approvals.py already gates send_message.
"""
import re

from .agent import approvals, audit, permissions
from . import tgstore

PREFIX = re.compile(r"^(?:tell|message|post|send|say)\s+(?:this\s+)?(?:to\s+)?(?:the\s+)?(group|channel)\s+(.+?)\s*[:\-]\s*(.+)$", re.I | re.S)
MAX_TEXT = 1000

_SEND = {"fn": None}  # set by the bot: fn(chat_id, text) -> dict (telegram response)


def set_sender(fn):
    _SEND["fn"] = fn


def parse(text):
    """Return (kind, target_name, body) or None. Only explicit 'tell/post ... <kind> <name>: <text>' forms match."""
    m = PREFIX.match((text or "").strip())
    if not m:
        return None
    return m.group(1).lower(), m.group(2).strip(), m.group(3).strip()


def resolve_target(kind, name, path=None):
    """Find a stored group or channel by title (case-insensitive). Returns (group_id, title) or (None, reason)."""
    rows = tgstore.known_chats(path=path)  # [(group_id, title, chat_type)]
    want = name.lower()
    hits = [(gid, title, ctype) for gid, title, ctype in rows
            if (title or "").lower() == want and (kind == "group" and ctype in ("group", "supergroup")
                                                  or kind == "channel" and ctype == "channel")]
    if not hits:
        return None, f"no {kind} named '{name}' that i'm in and have stored. check the name."
    if len(hits) > 1:
        return None, f"more than one {kind} is called '{name}'. rename one or use the chat id."
    gid, title, _ = hits[0]
    return gid, title


def queue_send(owner_chat_id, kind, name, body, path=None):
    """Owner-only. Returns (ok, reply_text). Queues an approval; nothing is sent here."""
    if not body or len(body) > MAX_TEXT:
        return False, f"message is empty or over {MAX_TEXT} characters. nothing was queued."
    gid, title = resolve_target(kind, name, path=path)
    if gid is None:
        return False, title
    rec = approvals.queue(owner_chat_id, "send_message", {"group_id": gid, "text": body},
                          summary=f"send to {kind} {title}")
    audit.record("owner", "send_queued", f"{kind}:{gid}", "pending")
    return True, (f"ready to send to the {kind} {title}. exact text:\n\n{body}\n\n"
                  f"send /approve {rec['id']} to post it, or ignore it to cancel.")


def execute_send(payload):
    """Approval executor for send_message. Only sends to the chat id stored in the approval."""
    gid = payload.get("group_id")
    text = (payload.get("text") or "")[:MAX_TEXT]
    if gid is None or not text:
        raise ValueError("missing target or text")
    fn = _SEND["fn"]
    if fn is None:
        raise RuntimeError("sender not configured")
    result = fn(gid, text)  # returns the telegram response dict
    ok = bool((result or {}).get("ok"))
    audit.record("bot", "group_send", f"chat:{gid}", "done" if ok else "failed")
    if not ok:
        raise RuntimeError("telegram refused the send")
    return "sent"


approvals.register_executor("send_message", execute_send)
