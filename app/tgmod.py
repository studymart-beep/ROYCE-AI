"""Moderation review queue for groups. Flags only: nothing is ever banned or deleted here.

Categories:
  link_suspicious  shortener domains, .tk/.xyz/.top, raw IP urls, punycode (xn--) hosts
  flood            more than 5 messages from one sender in 60 seconds, or the same text 3 times in 10 minutes
  ads              promo keywords together with a link or a buy/join/earn call to action

Flags persist in tgflags.db (sqlite). A flag is deduped on (group_id, message_id, category).
Audit records only the event (flag created, status changed) with ids and categories, never text.
Admins, and members on the group's allowlist, are exempt from flood and ads checks when
protect_admins is on (default on). Link checks still apply to everyone.
"""
import re
import sqlite3
import time
from urllib.parse import urlparse

from .paths import DATA_DIR

PATH = None  # resolved per call so tests can pass a temp path
FLOOD_WINDOW = 60
FLOOD_MAX = 5
REPEAT_WINDOW = 600
REPEAT_MAX = 3
STATUSES = ("pending", "reviewed_safe", "dismissed", "escalated")
SHORTENERS = {"bit.ly", "tinyurl.com", "t.co", "goo.gl", "ow.ly", "is.gd", "buff.ly", "rebrand.ly",
              "cutt.ly", "shorturl.at", "tiny.cc", "rb.gy", "s.id", "bitly.com"}
BAD_TLDS = (".tk", ".xyz", ".top")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"']+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:tk|xyz|top|ly|cc|id)\b(?:/[^\s]*)?", re.I)
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
PROMO_WORDS = re.compile(r"\b(promo|discount|crypto|forex|giveaway|free money|earn|bonus|jackpot|guaranteed|"
                         r"cashback|airdrop|limited offer|%\s*off|sale)\b", re.I)
CTA = re.compile(r"\b(buy|join|earn|sign ?up|click here|dm me|register|subscribe|invest|get paid|claim)\b", re.I)


def _path(path=None):
    if path:
        return path
    import os
    return os.path.join(DATA_DIR, "tgflags.db")


def _connect(path=None):
    con = sqlite3.connect(_path(path), timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript("""
    CREATE TABLE IF NOT EXISTS flags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        sender_id INTEGER,
        category TEXT NOT NULL,
        reason TEXT NOT NULL,
        ts REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        UNIQUE (group_id, message_id, category)
    );
    """)
    return con


def _urls(text):
    found = []
    for m in URL_RE.findall(text or ""):
        u = m if m.lower().startswith(("http://", "https://")) else "http://" + m
        host = (urlparse(u).hostname or "").lower()
        if host:
            found.append(host)
    return found


def link_reasons(text):
    """Reasons a message's links look suspicious, empty list when none."""
    reasons = []
    for host in _urls(text):
        if host in SHORTENERS or any(host.endswith("." + s) for s in SHORTENERS):
            reasons.append(f"shortener link ({host})")
        elif host.endswith(BAD_TLDS):
            reasons.append(f"risky domain ending ({host})")
        elif IP_RE.match(host):
            reasons.append("raw ip address link")
        elif "xn--" in host:
            reasons.append(f"punycode lookalike domain ({host})")
    return reasons


def has_link(text):
    return bool(_urls(text))


def ads_reason(text):
    if not text:
        return None
    if PROMO_WORDS.search(text) and (has_link(text) or CTA.search(text)):
        return "promo keywords with a link or buy/join/earn call to action"
    return None


class FloodTracker:
    """Per-(group, sender) message history. Pure in-memory; restart resets the window."""

    def __init__(self):
        self.times = {}   # (group, sender) -> [ts]
        self.texts = {}   # (group, sender, text) -> [ts]

    def observe(self, group_id, sender_id, text, ts):
        key = (group_id, sender_id)
        t = [x for x in self.times.get(key, []) if ts - x < FLOOD_WINDOW] + [ts]
        self.times[key] = t
        tkey = (group_id, sender_id, (text or "").strip().lower())
        r = [x for x in self.texts.get(tkey, []) if ts - x < REPEAT_WINDOW] + [ts]
        self.texts[tkey] = r
        if len(t) > FLOOD_MAX:
            return "flood", f"{len(t)} messages in {FLOOD_WINDOW}s (limit {FLOOD_MAX})"
        if len(r) >= REPEAT_MAX:
            return "flood", f"same text {len(r)} times in {REPEAT_WINDOW // 60} minutes"
        return None


_tracker = FloodTracker()


def inspect(group_id, message_id, sender_id, text, ts=None, exempt=False, tracker=None):
    """Return a list of (category, reason) for one group message. exempt members skip flood and ads."""
    ts = float(ts if ts is not None else time.time())
    out = []
    for r in link_reasons(text):
        out.append(("link_suspicious", r))
    if not exempt:
        flood = (tracker or _tracker).observe(group_id, sender_id, text, ts)
        if flood:
            out.append(flood)
        ad = ads_reason(text)
        if ad:
            out.append(("ads", ad))
    return out


def record(group_id, message_id, sender_id, category, reason, ts=None, path=None):
    """Create a pending flag. Returns (flag_id, created). Same message and category is never flagged twice."""
    ts = float(ts if ts is not None else time.time())
    con = _connect(path)
    try:
        with con:
            cur = con.execute(
                "INSERT OR IGNORE INTO flags (group_id, message_id, sender_id, category, reason, ts, status)"
                " VALUES (?,?,?,?,?,?,'pending')",
                (group_id, message_id, sender_id, category, reason, ts))
            created = cur.rowcount == 1
            row = con.execute("SELECT id FROM flags WHERE group_id=? AND message_id=? AND category=?",
                              (group_id, message_id, category)).fetchone()
            return row["id"], created
    finally:
        con.close()


def list_flags(status=None, limit=20, path=None):
    con = _connect(path)
    try:
        if status:
            rows = con.execute("SELECT * FROM flags WHERE status=? ORDER BY id DESC LIMIT ?", (status, limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM flags ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def get(flag_id, path=None):
    con = _connect(path)
    try:
        row = con.execute("SELECT * FROM flags WHERE id=?", (int(flag_id),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def set_status(flag_id, status, path=None):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}")
    con = _connect(path)
    try:
        with con:
            cur = con.execute("UPDATE flags SET status=? WHERE id=?", (status, int(flag_id)))
            return cur.rowcount == 1
    finally:
        con.close()
