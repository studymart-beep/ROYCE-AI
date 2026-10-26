"""Per-group allowlist: user ids exempt from flood and ads flags. Stored in tgallow.json.

Shape: {"<group_id>": [user_id, ...]}. Only a verified group admin or the bot owner may change
it (the caller checks that and audits refusals). Being on the list does not grant admin rights.
"""
import json
import os
import threading

from .paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "tgallow.json")
_LOCK = threading.Lock()


def load(path=None):
    path = path or PATH
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    for k, v in data.items():
        if isinstance(v, list):
            out[str(k)] = sorted({int(x) for x in v if str(x).lstrip("-").isdigit()})
    return out


def save(data, path=None):
    path = path or PATH
    with _LOCK:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)


def members(group_id, path=None):
    return load(path).get(str(group_id), [])


def is_allowed(group_id, user_id, path=None):
    return user_id is not None and int(user_id) in members(group_id, path)


def add(group_id, user_id, path=None):
    data = load(path)
    lst = set(data.get(str(group_id), []))
    lst.add(int(user_id))
    data[str(group_id)] = sorted(lst)
    save(data, path)
    return data[str(group_id)]


def remove(group_id, user_id, path=None):
    data = load(path)
    lst = set(data.get(str(group_id), []))
    existed = int(user_id) in lst
    lst.discard(int(user_id))
    data[str(group_id)] = sorted(lst)
    save(data, path)
    return existed
