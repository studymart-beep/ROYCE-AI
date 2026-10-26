"""Per-group rules text (set by admins with /setrules, shown with /rules).

Persisted in a small json file keyed by group id. The rules text is referenced in the recap context and
in the moderation context so the model can judge a message against the group's stated rules. It never
changes whether a flag is raised on its own (flags stay review-only).
"""
import json
import os
import threading

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "tgrules.json")
MAX_LEN = 2000
_LOCK = threading.Lock()


def _load(path=None):
    try:
        with open(path or PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def get(group_id, path=None):
    return _load(path).get(str(group_id), "")


def set_rules(group_id, text, path=None):
    text = (text or "").strip()[:MAX_LEN]
    with _LOCK:
        d = _load(path)
        d[str(group_id)] = text
        p = path or PATH
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f, indent=2)
        os.replace(tmp, p)
    return text


def context_line(group_id, path=None):
    """One line for the LLM context (recap and moderation). Empty when no rules are set."""
    r = get(group_id, path)
    return f"group rules (set by admins): {r}" if r else ""
