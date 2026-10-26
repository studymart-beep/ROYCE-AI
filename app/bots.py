"""Bots store: a bot is a named persona that pins one marketing skill into the chat system prompt.

Six built-in templates map 1:1 to the playbooks in skills/. Creating a bot from a template stores
the template key; the skill text is read live from skills/<name>.md, so editing a skill updates
every bot that pins it. Stored as bots.json in the data directory, written atomically.
"""
import datetime
import json
import os
import re
import threading
import uuid

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "bots.json")
_lock = threading.Lock()
MAX_NAME = 60

# template key -> (label, category, short blurb). Keys are skill names in skills/.
TEMPLATES = {
    "direct-response": ("Direct response", "Featured", "Scroll-stopping hooks, PAS and AIDA copy, honest CTAs."),
    "brand-strategy": ("Brand strategy", "Featured", "Positioning, voice, audience and competitor angles."),
    "social-growth": ("Social growth", "Productivity", "Organic content plans, reels, trends and calendars."),
    "funnel-design": ("Funnel design", "Research", "Lead magnets, landing pages and retargeting paths."),
    "reporting-storytelling": ("Reporting storytelling", "Research", "Turns ad metrics into a plain story for the client."),
    "negotiation-closing": ("Negotiation and closing", "Personal Assistant", "Pricing replies, objections and closing lines."),
}
CATEGORIES = ["Featured", "Personal Assistant", "Productivity", "Research", "More"]


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


def templates():
    """Template catalog for the 'Make a bot' screen. Each entry is real (backed by a skill file)."""
    out = []
    for key, (label, cat, blurb) in TEMPLATES.items():
        out.append({"key": key, "label": label, "category": cat, "description": blurb})
    return out


def create(name, template=None):
    """Create a bot. template must be a known skill key or None (general Royce persona)."""
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        raise ValueError("bot name is required")
    if len(name) > MAX_NAME:
        raise ValueError(f"bot name must be {MAX_NAME} characters or fewer")
    if template and template not in TEMPLATES:
        raise ValueError("unknown template")
    bot = {
        "id": uuid.uuid4().hex[:10],
        "name": name,
        "template": template or None,
        "created_at": _now(),
    }
    with _lock:
        data = _load()
        data[bot["id"]] = bot
        _save(data)
    return bot


def list_bots():
    with _lock:
        items = list(_load().values())
    return sorted(items, key=lambda b: b.get("created_at", ""))


def get(bot_id):
    with _lock:
        return _load().get(bot_id)


def delete(bot_id):
    with _lock:
        data = _load()
        if bot_id not in data:
            return False
        del data[bot_id]
        _save(data)
    return True


def persona_block(bot):
    """Prompt text for a bot: its name and the pinned skill playbook (if any)."""
    if not bot:
        return ""
    from . import skills
    parts = [f"You are working as the bot named \"{bot.get('name', '')}\"."]
    if bot.get("template"):
        body = skills.get_skill(bot["template"])
        if body:
            parts.append("Follow this playbook for this bot:\n" + body.strip())
    return "\n\n".join(parts)
