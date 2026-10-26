"""Ad doctor: a ruthless teardown of ad copy or campaign numbers.

diagnose(text, kind) with kind in ("ad_copy", "campaign_numbers").
- ad_copy: text is the ad copy itself (headline, primary text, hook, caption).
- campaign_numbers: text is pasted rows, OR a list of dicts from the collectors
  (spend, impressions, clicks, ctr, cpc, campaign, platform). Dicts are rendered into
  rows first so the model only sees the real numbers.
Uses app/llm.py chat_text (gemini first, openrouter failover).
"""
from .llm import chat_text
from . import skills

DOCTOR_SYSTEM = (
    "You are a ruthless senior media buyer. Tear down this ad or campaign data: "
    "what's weak, what to fix, 3 specific changes. Plain text, short bullets, no fluff."
)

KINDS = ("ad_copy", "campaign_numbers")
NUMERIC_FIELDS = ("spend", "impressions", "clicks", "ctr", "cpc")


def _fmt_row(r):
    if isinstance(r, str):
        return r.strip()
    name = r.get("campaign") or r.get("name") or r.get("ad") or "row"
    plat = r.get("platform")
    head = f"{plat} | {name}" if plat else str(name)
    parts = [f"{k} {r[k]}" for k in NUMERIC_FIELDS if k in r and r[k] not in (None, "")]
    return head + " | " + " | ".join(parts)


def rows_to_text(data):
    """Turn a list of collector dicts (or pasted strings) into the text the model reads."""
    if isinstance(data, str):
        return data.strip()
    return "\n".join(_fmt_row(r) for r in data)


def diagnose(text, kind):
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    if isinstance(text, (list, tuple)):
        body = rows_to_text(list(text))
    else:
        body = (text or "").strip()
    if not body:
        raise ValueError("nothing to diagnose: send the ad copy or the campaign numbers")
    if kind == "ad_copy":
        user = f"AD COPY:\n{body}\n\nTear it down: hook strength, clarity of offer, call to action, what to fix, and 3 specific rewrites or changes."
    else:
        user = (f"CAMPAIGN NUMBERS (use only these numbers, do not invent any):\n{body}\n\n"
                "Tear it down: which campaign is bleeding money, what the CTR and CPC say, "
                "what to fix, and 3 specific changes.")
    kind_key = "doctor_numbers" if kind == "campaign_numbers" else "doctor"
    return chat_text([{"role": "system", "content": DOCTOR_SYSTEM + skills.engine_skill(kind_key)}, {"role": "user", "content": user}],
                     temperature=0.4, timeout=40).strip()


def wants_diagnose(text):
    """Chat detection for the bot: roast/teardown/why-is-my-ctr style asks."""
    import re
    low = (text or "").lower()
    return bool(re.search(r"\b(roast|tear (this|it) down|teardown|critique|review)\b.*\b(ad|ads|copy|campaign)\b", low)
                or re.search(r"\bwhy (is|are) (my|the) (ctr|cpc|roas|cpa|ads?|campaigns?)\b", low)
                or re.search(r"\b(diagnose|doctor)\b", low))
