"""Workspace conversation store: web chats with real titles and messages.

Stored in conversations.json at the project root (git-ignored). Each conversation:
  {id, title, created_at, updated_at, messages: [{role: "user"|"assistant", text, ts, task_id?}]}
A conversation is created by its first message, so an empty chat never appears in the list.
Only completed exchanges are stored: a failed or budget-blocked send is not written, so a retry
never duplicates the user's message.
"""
import json, os, threading, time, uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import DATA_DIR
PATH = os.path.join(DATA_DIR, "conversations.json")
_LOCK = threading.Lock()
TITLE_MAX = 60


def _load():
    try:
        with open(PATH) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, PATH)


def _title(text):
    t = " ".join((text or "").split())
    return t if len(t) <= TITLE_MAX else t[: TITLE_MAX - 3].rstrip() + "..."


def append_exchange(conv_id, user_text, reply, task_id=None, meta=None):
    """Store one completed exchange. Creates the conversation when conv_id is None or unknown."""
    now = time.time()
    with _LOCK:
        d = _load()
        if not conv_id or conv_id not in d:
            conv_id = uuid.uuid4().hex[:12]
            d[conv_id] = {"id": conv_id, "title": _title(user_text), "created_at": now, "updated_at": now, "messages": []}
        conv = d[conv_id]
        conv["messages"].append({"role": "user", "text": user_text, "ts": now})
        assistant = {"role": "assistant", "text": reply, "ts": now}
        if task_id:
            assistant["task_id"] = task_id
        if meta:
            assistant["meta"] = meta
        conv["messages"].append(assistant)
        conv["updated_at"] = now
        _save(d)
        return json.loads(json.dumps(conv))


def get(conv_id):
    with _LOCK:
        conv = _load().get(conv_id)
    return json.loads(json.dumps(conv)) if conv else None


def history(conv_id, limit=20):
    """Last messages in llm chat format (user/assistant). Empty when the conversation is unknown."""
    conv = get(conv_id) if conv_id else None
    if not conv:
        return []
    return [{"role": m["role"], "content": m["text"]} for m in conv["messages"][-limit:]]


def list_all(q=None):
    """Summaries newest first. q filters by title or any message text (case-insensitive substring)."""
    with _LOCK:
        d = _load()
    needle = (q or "").strip().lower()
    out = []
    for c in d.values():
        if needle:
            hay = (c.get("title", "") + "\n" + "\n".join(m.get("text", "") for m in c.get("messages", []))).lower()
            if needle not in hay:
                continue
        out.append({"id": c["id"], "title": c.get("title", ""), "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at"), "message_count": len(c.get("messages", []))})
    return sorted(out, key=lambda c: c.get("updated_at") or 0, reverse=True)
