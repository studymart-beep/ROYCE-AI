"""Json-backed memory: durable facts Royce keeps (client names, preferences, how the agency works).

Stored in memory.json at the project root (git-ignored). Each item:
  key, text, scope ("project" or "chat:<chat_id>"), updated_at.
Search is keyword scoring: count of query words found in key+text, key matches weighted 2x.
A conservative heuristic (detect) saves only explicit statements:
  "remember that ...", "remember: ...", "my client X is ...", "we use ..."
"""
import json, os, re, threading, time

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(BASE, "memory.json")
_LOCK = threading.Lock()
STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "it", "that", "this", "for", "on", "with", "we", "i", "my", "our", "be"}


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, PATH)


def _words(s):
    return [w for w in re.findall(r"[a-z0-9]+", (s or "").lower()) if w not in STOP and len(w) > 1]


def _slug(s):
    return "-".join(_words(s))[:60] or "fact"


def save(key, text, scope="project"):
    key = (key or "").strip().lower()[:80]
    text = (text or "").strip()[:500]
    if not key or not text:
        raise ValueError("memory needs a key and text")
    if scope != "project" and not scope.startswith("chat:"):
        raise ValueError("scope must be 'project' or 'chat:<id>'")
    with _LOCK:
        d = _load()
        d[key] = {"key": key, "text": text, "scope": scope, "updated_at": time.time()}
        _save(d)
    return dict(d[key])


def forget(key):
    key = (key or "").strip().lower()
    with _LOCK:
        d = _load()
        if key not in d:
            return False
        del d[key]
        _save(d)
        return True


def all_items(scope=None):
    d = _load()
    return [dict(v) for v in d.values() if scope is None or v.get("scope") in (scope, "project")]


def search(query, scope=None, limit=5):
    """Top matching items for the query. scope=None searches everything; a chat scope also includes project items."""
    q = set(_words(query))
    if not q:
        return []
    scored = []
    for it in all_items(scope):
        kw = set(_words(it["text"]))
        kk = set(_words(it["key"].replace("-", " ")))
        score = len(q & kw) + 2 * len(q & kk)
        if score > 0:
            scored.append((score, it["updated_at"], it))
    scored.sort(key=lambda x: (-x[0], -x[1]))
    return [dict(x[2], score=x[0]) for x in scored[:limit]]


def block(query, chat_id=None, limit=5):
    """System prompt block with the top matching memories, or empty string."""
    scope = f"chat:{chat_id}" if chat_id is not None else None
    hits = search(query, scope=scope, limit=limit)
    if not hits:
        return ""
    lines = ["Things you already know about this business (from earlier chats, use only if relevant):"]
    lines += [f"- {h['text']}" for h in hits]
    return "\n".join(lines)


_REMEMBER = re.compile(r"^\s*remember(?: that| this)?\s*[:,]?\s+(.{4,300})$", re.I | re.S)
_CLIENT = re.compile(r"\bmy client ([A-Za-z][A-Za-z0-9&' -]{1,40}?) is (.{3,200})$", re.I)
_USE = re.compile(r"^\s*we use (.{3,200})$", re.I)


def detect(text):
    """Return a list of (key, text) facts from explicit statements only. Empty when nothing is clearly durable."""
    t = (text or "").strip()
    out = []
    m = _REMEMBER.match(t)
    if m:
        body = m.group(1).strip().rstrip(".")
        out.append((_slug(body), body))
        return out
    m = _CLIENT.search(t)
    if m:
        name, rest = m.group(1).strip(), m.group(2).strip().rstrip(".")
        out.append((f"client-{_slug(name)}", f"client {name} is {rest}"))
    m = _USE.match(t)
    if m:
        body = m.group(1).strip().rstrip(".")
        out.append((f"tool-{_slug(body)}", f"we use {body}"))
    return out


def learn(text, chat_id=None):
    """Save every fact detect() finds. Returns the saved keys."""
    saved = []
    scope = "project"
    for key, body in detect(text):
        save(key, body, scope=scope)
        saved.append(key)
    return saved
