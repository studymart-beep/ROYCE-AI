"""Marketing skills: markdown playbooks injected into the agent's prompts.

Each playbook lives in skills/<name>.md at the project root. The file starts with a
front matter block (name, description) and then the playbook body.

Public API:
- list_skills() -> [{"name", "description"}, ...]
- get_skill(name) -> playbook text (front matter stripped), or None
- select_skills(text, limit=2) -> names of the most relevant skills for a message
- skill_block(text, limit=2) -> prompt text to append to a system prompt ("" if none match)
- engine_skill(kind) -> the default skill for an engine ("followup", "doctor", "content")
"""
import os
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR = os.path.join(BASE, "skills")
MAX_INJECT = 2

# keyword -> skill. Matched as whole words (case-insensitive), so "ad" does not match "tailwind".
KEYWORDS = {
    "direct-response": ["hook", "hooks", "ad", "ads", "script", "scripts", "copy", "cta",
                        "offer", "urgency", "headline", "aida", "pas", "scroll"],
    "brand-strategy": ["brand", "positioning", "voice", "persona", "audience", "competitor",
                       "competitors", "pillar", "pillars", "identity"],
    "social-growth": ["content", "post", "posts", "reel", "reels", "tiktok", "instagram",
                      "followers", "trend", "trends", "engagement", "calendar", "organic", "carousel"],
    "funnel-design": ["funnel", "funnels", "leads", "lead", "magnet", "retarget", "retargeting",
                      "landing", "conversion", "pixel"],
    "reporting-storytelling": ["report", "reports", "reporting", "metrics", "metric", "roas", "ctr",
                               "cpc", "cpm", "cpl", "kpi", "kpis", "performance", "numbers",
                               "spend", "impressions", "teardown", "results"],
    "negotiation-closing": ["price", "pricing", "prices", "client", "clients", "deal", "close",
                            "closing", "quote", "proposal", "objection", "objections", "package",
                            "packages", "retainer", "invoice", "discovery"],
}

# default skill per engine, used when the message itself gives no clear signal
ENGINE_DEFAULTS = {
    "followup": "negotiation-closing",
    "doctor": "direct-response",
    "doctor_numbers": "reporting-storytelling",
    "content": "social-growth",
}


def _parse(text):
    """Split front matter from body. Returns (meta dict, body)."""
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = text[m.end():]
    return meta, body.strip()


def _path(name):
    # only plain names: no path separators, no traversal
    if not name or not re.fullmatch(r"[a-z0-9-]+", name):
        return None
    return os.path.join(SKILLS_DIR, name + ".md")


def list_skills():
    out = []
    if not os.path.isdir(SKILLS_DIR):
        return out
    for fn in sorted(os.listdir(SKILLS_DIR)):
        if not fn.endswith(".md"):
            continue
        name = fn[:-3]
        with open(os.path.join(SKILLS_DIR, fn), encoding="utf-8") as f:
            meta, _ = _parse(f.read())
        out.append({"name": meta.get("name") or name, "description": meta.get("description", "")})
    return out


def save_skill(name, body, description=""):
    """Write skills/<name>.md with a front matter block. The name must be a plain slug, the file is
    written atomically, and the body is the playbook text. Returns the saved name."""
    key = (name or "").strip().lower()
    p = _path(key)
    if not p:
        raise ValueError("skill names use lowercase letters, digits and dashes only")
    if not os.path.isfile(p):
        raise ValueError("no such skill; only existing playbooks can be edited here")
    desc = " ".join((description or "").split())
    text = "---\nname: " + key + "\n"
    if desc:
        text += "description: " + desc + "\n"
    text += "---\n" + (body or "").strip() + "\n"
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, p)
    return key


def get_skill(name):
    """Return the playbook body for a skill name, or None if it does not exist."""
    p = _path((name or "").strip().lower())
    if not p or not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        _, body = _parse(f.read())
    return body


def select_skills(text, limit=MAX_INJECT):
    """Score each skill by how many of its keywords appear as whole words in text."""
    low = (text or "").lower()
    if not low.strip():
        return []
    limit = max(0, min(limit, MAX_INJECT))
    scores = []
    for order, (skill, words) in enumerate(KEYWORDS.items()):
        hits = sum(1 for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", low))
        if hits:
            scores.append((-hits, order, skill))
    scores.sort()
    return [s for _, _, s in scores[:limit]]


def skill_block(text, limit=MAX_INJECT):
    """Prompt text with the most relevant playbooks, or '' when nothing matches."""
    names = select_skills(text, limit)
    return _block(names)


def engine_skill(kind):
    """Playbook text for an engine's default skill (followup, doctor, doctor_numbers, content)."""
    name = ENGINE_DEFAULTS.get(kind)
    return _block([name]) if name else ""


def _block(names):
    parts = []
    for n in names:
        body = get_skill(n)
        if body:
            parts.append(f"[skill: {n}]\n{body}")
    if not parts:
        return ""
    return ("\n\nMARKETING PLAYBOOKS (use these frameworks where they apply, do not recite them):\n\n"
            + "\n\n".join(parts))


CODING_DIR = os.path.join(SKILLS_DIR, "coding")
CODING_TRIGGERS = re.compile(
    r"\b(debug|debugging|bug|bugs|traceback|stack ?trace|error|exception|failing|failed test|"
    r"explain (the )?code|read (the )?code|codebase|repo|repository|refactor|why (is|does) (it|this) (fail|break))\b",
    re.I)


def coding_block(text):
    """Return the coding playbooks (codebase, debugging) when the message is about code. Else ''."""
    if not CODING_TRIGGERS.search(text or ""):
        return ""
    parts = []
    for name in ("codebase", "debugging"):
        p = os.path.join(CODING_DIR, name + ".md")
        if os.path.isfile(p):
            with open(p, encoding="utf-8") as f:
                _, body = _parse(f.read())
            parts.append(body)
    return ("\n\n## Coding playbooks\n" + "\n\n".join(parts)) if parts else ""

