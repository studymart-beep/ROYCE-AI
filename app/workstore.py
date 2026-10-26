"""Store of generated ad packages (work.json at the project root, git-ignored).

Every /api/script call and every bot script generation appends one entry here.
The mini app's Generated Work page reads it newest first.
"""
import datetime, json, os, threading, uuid

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(BASE, "work.json")
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
