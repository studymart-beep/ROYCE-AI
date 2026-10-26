"""Lead brain: AI follow-up drafts per lead, and a once-a-day nudge of who to chase.

Drafts go through app/llm.py chat_text (gemini first, openrouter failover).
Nudge state (the date we last nudged, and when we last checked) lives in
nudge_state.json at the project root (git-ignored), so a bot restart does not
spam the owner twice in one day.
"""
import datetime, json, os
from zoneinfo import ZoneInfo
from .llm import chat_text
from . import db
from . import skills

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(BASE, "nudge_state.json")
TZ = ZoneInfo("Africa/Lagos")
CHECK_EVERY = 3600  # run the nudge check at most once an hour
OWNER_CHAT_ID = int(os.environ.get("OWNER_CHAT_ID", "7531626960"))  # fallback until the owner has messaged the bot

FOLLOWUP_SYSTEM = (
    "You are Royce AI for Royce Marketing Agency. Write a short follow-up message to a lead "
    "in a warm professional Nigerian tone. Reference their business. Max 3 sentences. Return just the message."
)


def today_lagos():
    return datetime.datetime.now(TZ).date()


def draft_followup(lead, context=""):
    """Draft one follow-up message for a lead dict. context is any extra notes the user gave."""
    lines = [
        f"Lead name: {lead.get('name') or 'unknown'}",
        f"Business: {lead.get('business') or 'not given'}",
        f"Contact: {lead.get('contact') or 'not given'}",
        f"Status: {lead.get('status') or 'open'}",
        f"Notes on file: {lead.get('notes') or 'none'}",
    ]
    if context:
        lines.append(f"Extra notes from the user: {context}")
    user = "\n".join(lines) + "\n\nWrite the follow-up message now."
    text = chat_text([{"role": "system", "content": FOLLOWUP_SYSTEM + skills.engine_skill("followup")},
                      {"role": "user", "content": user}], temperature=0.7, timeout=30)
    return text.strip().strip('"').strip()


def find_lead(query):
    """Match a lead by numeric id or by case-insensitive name (exact first, then contains)."""
    q = (query or "").strip().lstrip("#")
    if not q:
        return None
    leads = db.list_leads()
    if q.isdigit():
        return next((l for l in leads if l["id"] == int(q)), None)
    ql = q.lower()
    exact = [l for l in leads if (l.get("name") or "").lower() == ql]
    if exact:
        return exact[0]
    partial = [l for l in leads if ql in (l.get("name") or "").lower() or ql in (l.get("business") or "").lower()]
    return partial[0] if partial else None


def due_leads(today=None):
    """Open leads whose next_followup is today or earlier, with a one-line reason each."""
    today = today or today_lagos()
    out = []
    for l in db.list_leads():
        if l.get("status") != "open" or not l.get("next_followup"):
            continue
        try:
            due = datetime.date.fromisoformat(str(l["next_followup"])[:10])
        except ValueError:
            continue
        if due > today:
            continue
        if due == today:
            reason = "follow-up due today"
        else:
            reason = f"overdue by {(today - due).days} day(s), was due {due.isoformat()}"
        out.append({"lead": l, "reason": reason})
    return out


def _load_state():
    try:
        with open(STATE_PATH) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def _save_state(d):
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, STATE_PATH)


def nudge_message(today=None):
    """Return the nudge text, or None if nothing is due or the owner was already nudged today."""
    today = today or today_lagos()
    due = due_leads(today)
    if not due:
        return None
    lines = [f"heads up, {len(due)} lead(s) need a follow-up:"]
    for d in due:
        l = d["lead"]
        biz = f" ({l['business']})" if l.get("business") else ""
        lines.append(f"• #{l['id']} {l['name']}{biz}: {d['reason']}")
    lines.append("say /followup <name or id> and i'll draft the message.")
    return "\n".join(lines)


def should_check(now_ts, state=None):
    state = state if state is not None else _load_state()
    return now_ts - float(state.get("last_check", 0)) >= CHECK_EVERY


def remember_chat(chat_id):
    """The bot calls this on every inbound private message so nudges know where to go."""
    state = _load_state()
    if state.get("chat_id") != chat_id:
        state["chat_id"] = chat_id
        _save_state(state)


def run_nudge_check(now_ts, today=None):
    """Called from the bot loop. Returns (chat_id, text) to send, or None.
    Checks at most once an hour and nudges at most once per Lagos day.
    State is written before returning so a restart cannot repeat the nudge."""
    state = _load_state()
    if not should_check(now_ts, state):
        return None
    today = today or today_lagos()
    state["last_check"] = now_ts
    chat_id = state.get("chat_id") or OWNER_CHAT_ID
    if state.get("nudged_on") == today.isoformat() or not chat_id:
        _save_state(state)
        return None
    msg = nudge_message(today)
    if msg:
        state["nudged_on"] = today.isoformat()
    _save_state(state)
    return (chat_id, msg) if msg else None
