"""Welcome messages for groups. Config persists in tgwelcomes.json under the data dir.

Shape: {"enabled": bool, "default_text": str, "per_group": {"<chat_id>": {"enabled": bool, "text": str}}}
Changing the config is admin-only and is checked by the caller with tgadmin.is_admin.
"""
import json
import os
import threading

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "tgwelcomes.json")
DEFAULT_TEXT = "welcome {name} to {group}. say hi and check the pinned message for the rules."
MAX_TEXT = 500
_LOCK = threading.Lock()


def _default():
    return {"enabled": True, "default_text": DEFAULT_TEXT, "per_group": {}}


def load(path=None):
    path = path or PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return _default()
    if not isinstance(data, dict):
        return _default()
    cfg = _default()
    cfg["enabled"] = bool(data.get("enabled", True))
    if isinstance(data.get("default_text"), str) and data["default_text"].strip():
        cfg["default_text"] = data["default_text"]
    if isinstance(data.get("per_group"), dict):
        cfg["per_group"] = {str(k): v for k, v in data["per_group"].items() if isinstance(v, dict)}
    return cfg


def save(cfg, path=None):
    path = path or PATH
    with _LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def set_text(group_id, text, path=None):
    text = (text or "").strip()
    if not text:
        raise ValueError("usage: /setwelcome <text>. use {name} for the new member and {group} for the group name.")
    if len(text) > MAX_TEXT:
        raise ValueError(f"welcome text is too long (max {MAX_TEXT} characters)")
    cfg = load(path)
    g = cfg["per_group"].setdefault(str(group_id), {})
    g["text"] = text
    save(cfg, path)
    return text


def set_enabled(group_id, on, path=None):
    cfg = load(path)
    g = cfg["per_group"].setdefault(str(group_id), {})
    g["enabled"] = bool(on)
    save(cfg, path)
    return bool(on)


def status(group_id, path=None):
    cfg = load(path)
    g = cfg["per_group"].get(str(group_id), {})
    enabled = bool(cfg["enabled"]) and bool(g.get("enabled", True))
    text = g.get("text") or cfg["default_text"]
    return {"enabled": enabled, "text": text, "custom": "text" in g}


def render(template, name, group_title):
    return template.replace("{name}", name or "there").replace("{group}", group_title or "the group")


def should_welcome(group_id, path=None):
    return status(group_id, path)["enabled"]


def welcome_for_join(group_id, group_title, new_members, path=None):
    """Build the welcome lines for a new_chat_members event. Bots that joined are not greeted."""
    st = status(group_id, path)
    if not st["enabled"]:
        return []
    out = []
    for m in new_members or []:
        if m.get("is_bot"):
            continue
        name = m.get("first_name") or m.get("username") or ""
        out.append(render(st["text"], name, group_title))
    return out
