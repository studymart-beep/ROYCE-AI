"""Content engine: 7-day content calendars per client.

weekly_calendar(client, industry, platform) asks the LLM for STRICT JSON
{"days": [{day, format, idea, hook, cta} x 7]}, validates it, saves it to the
workstore as type "content_calendar", and returns the calendar dict.
"""
import json
import re

from .llm import chat_text
from . import workstore
from . import skills
from .style import VOICE_RULES, sanitize

CONTENT_SYSTEM = (
    "You are Royce AI for Royce Marketing Agency, a content strategist for Lagos brands. "
    "Plan a 7-day social content calendar. Return STRICT JSON only, no prose, no markdown fences. "
    'Shape: {"days":[{"day":"Monday","format":"short video|carousel|static|reel|story|live",'
    '"idea":"...","hook":"first line of the post or video","cta":"the call to action"}, ... 7 items]} '
    "Exactly 7 days, Monday to Sunday, in that order. Keep hooks short and specific to the industry. "
    + VOICE_RULES
)

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _parse_json(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    try:
        return json.loads(t)
    except Exception:
        m = re.search(r"\{.*\}", t, re.S)
        if not m:
            raise ValueError("calendar came back in the wrong shape, try again")
        return json.loads(m.group(0))


def _validate(data):
    days = data.get("days") if isinstance(data, dict) else None
    if not isinstance(days, list) or len(days) != 7:
        raise ValueError("calendar must have exactly 7 days")
    clean = []
    for i, d in enumerate(days):
        if not isinstance(d, dict):
            raise ValueError("bad day entry")
        clean.append({
            "day": str(d.get("day") or DAYS[i]).strip(),
            "format": str(d.get("format") or "").strip(),
            "idea": str(d.get("idea") or "").strip(),
            "hook": str(d.get("hook") or "").strip(),
            "cta": str(d.get("cta") or "").strip(),
        })
    return {"days": clean}


def weekly_calendar(client, industry, platform, source="chat"):
    client = (client or "").strip()
    industry = (industry or "").strip()
    platform = (platform or "").strip()
    if not client or not industry or not platform:
        raise ValueError("need client, industry and platform")
    user = f"Client: {client}\nIndustry: {industry}\nPlatform: {platform}\nMake the 7-day calendar."
    raw = chat_text([{"role": "system", "content": CONTENT_SYSTEM + skills.engine_skill("content")}, {"role": "user", "content": user}],
                    temperature=0.7, timeout=45)
    cal = _validate(_parse_json(raw))
    workstore.add_content_calendar(client, industry, platform, cal, source=source)
    return cal


def format_calendar(client, platform, cal):
    lines = [f"7-day content plan for {client} ({platform}):"]
    for d in cal["days"]:
        lines.append(f"\n{d['day']} · {d['format']}\nidea: {d['idea']}\nhook: {d['hook']}\ncta: {d['cta']}")
    return sanitize("\n".join(lines))
