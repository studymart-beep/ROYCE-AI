"""Audit log: append-only jsonl of what Royce did, for incident review.

Each line: {"ts", "actor", "action", "target", "outcome"}. Nothing else is written.
Message text, prompts, replies, file contents and secrets are never accepted: any extra
field passed in is dropped, and string values are cut to a short length and checked for
key-like patterns before they reach the file.

Stored at logs/audit.jsonl (git-ignored). Writes take a lock and open the file in append
mode, so lines are never rewritten.
"""
import datetime
import json
import os
import re
import threading

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(BASE, "logs", "audit.jsonl")
_LOCK = threading.Lock()
MAX_LEN = 120
# anything that looks like a credential is replaced, not written
_SECRET = re.compile(r"(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|bot\d{6,}:[A-Za-z0-9_-]{20,}|"
                     r"(api[_-]?key|token|secret|password)\s*[=:]\s*\S+)", re.I)


def _clean(value):
    s = "" if value is None else str(value)
    s = _SECRET.sub("[redacted]", s)
    s = s.replace("\n", " ").replace("\r", " ")
    return s[:MAX_LEN]


def record(actor, action, target="", outcome="ok", path=None):
    """Append one event. actor is 'owner', 'bot', 'job', 'tool' or a chat id label; never message text."""
    entry = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "actor": _clean(actor),
        "action": _clean(action),
        "target": _clean(target),
        "outcome": _clean(outcome),
    }
    target_path = path or PATH
    line = json.dumps(entry, ensure_ascii=False)
    with _LOCK:
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    return entry


def read(path=None, limit=50):
    """Last `limit` events, oldest first. Malformed lines are skipped."""
    target_path = path or PATH
    try:
        with open(target_path, encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    out = []
    for ln in lines:
        try:
            out.append(json.loads(ln))
        except ValueError:
            continue
    return out[-limit:]
