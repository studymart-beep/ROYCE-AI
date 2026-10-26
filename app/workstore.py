"""Store of generated ad packages (work.json at the project root, git-ignored).

Every /api/script call and every bot script generation appends one entry here.
The mini app's Generated Work page reads it newest first.
"""
import datetime, json, os, threading, uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import DATA_DIR
PATH = os.path.join(DATA_DIR, "work.json")
_lock = threading.Lock()


def _load():
    try:
        with open(PATH) as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception:
        return []


def add_package(pkg, product, audience, platform, source="mini app"):
    """Append one generated package. Returns the stored entry."""
    pkg = pkg or {}
    entry = {
        "id": uuid.uuid4().hex[:10],
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "product": str(product or "").strip(),
        "audience": str(audience or "").strip(),
        "platform": str(platform or "").strip().lower(),
        "source": source,
        "hooks": list(pkg.get("hooks") or []),
        "script": list(pkg.get("script") or []),
        "captions": list(pkg.get("captions") or []),
    }
    with _lock:
        items = _load()
        items.append(entry)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PATH)
    return entry


def list_work():
    """All generated packages, newest first."""
    with _lock:
        items = _load()
    return list(reversed(items))


def add_content_calendar(client, industry, platform, cal, source="chat"):
    """Append a 7-day content calendar (type content_calendar)."""
    entry = {
        "id": uuid.uuid4().hex[:10],
        "type": "content_calendar",
        "created_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "client": str(client or "").strip(),
        "product": str(client or "").strip(),
        "industry": str(industry or "").strip(),
        "audience": str(industry or "").strip(),
        "platform": str(platform or "").strip().lower(),
        "source": source,
        "days": list((cal or {}).get("days") or []),
    }
    with _lock:
        items = _load()
        items.append(entry)
        tmp = PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        os.replace(tmp, PATH)
    return entry
