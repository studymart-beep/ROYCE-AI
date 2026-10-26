"""Group message store: text messages the bot sees in Telegram groups, kept in sqlite.

Only text is stored (never photos, files or service events). Inserts are idempotent on
(group_id, message_id): a repeated update does not duplicate a row. Edits update the row in
place and set edited=1. Each group has its own retention window (default 720 hours); cleanup()
deletes rows older than that window and is run from the bot loop about every 30 minutes.

Nothing here writes to the audit log. Deletions are reported to the caller as counts only,
and the caller audits counts, never content.

Lives at tgstore.db under the data dir (git-ignored). Functions take an explicit db path so
tests can use a temp file.
"""
import os
import sqlite3
import time

from .paths import DATA_DIR

DEFAULT_RETENTION_HOURS = 720
CLEANUP_EVERY = 30 * 60
PATH = os.path.join(DATA_DIR, "tgstore.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS group_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    sender_name TEXT,
    ts REAL NOT NULL,
    text TEXT NOT NULL,
    edited INTEGER NOT NULL DEFAULT 0,
    UNIQUE (group_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_gm_group_ts ON group_messages (group_id, ts);
CREATE TABLE IF NOT EXISTS group_meta (
    group_id INTEGER PRIMARY KEY,
    retention_hours INTEGER NOT NULL,
    first_seen REAL NOT NULL
);
"""


def _connect(path=None):
    path = path or PATH
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    con = sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    con.executescript(_SCHEMA)
    return con


def _ensure_group(con, group_id, now):
    row = con.execute("SELECT group_id FROM group_meta WHERE group_id=?", (group_id,)).fetchone()
    if row is None:
        con.execute("INSERT INTO group_meta (group_id, retention_hours, first_seen) VALUES (?,?,?)",
                    (group_id, DEFAULT_RETENTION_HOURS, now))


def save_message(group_id, message_id, sender_id, sender_name, text, ts=None, edited=False, path=None):
    """Insert a text message, or update it in place when the same message id arrives again.

    Returns 'inserted', 'edited', 'duplicate' or 'ignored' (empty text).
    """
    text = (text or "").strip()
    if not text:
        return "ignored"
    now = time.time()
    ts = float(ts if ts is not None else now)
    con = _connect(path)
    try:
        with con:
            _ensure_group(con, group_id, now)
            row = con.execute("SELECT text, edited FROM group_messages WHERE group_id=? AND message_id=?",
                              (group_id, message_id)).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO group_messages (group_id, message_id, sender_id, sender_name, ts, text, edited)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (group_id, message_id, sender_id, sender_name or "", ts, text, 1 if edited else 0))
                return "inserted"
            if edited and row["text"] != text:
                con.execute("UPDATE group_messages SET text=?, edited=1, sender_name=? "
                            "WHERE group_id=? AND message_id=?",
                            (text, sender_name or "", group_id, message_id))
                return "edited"
            return "duplicate"
    finally:
        con.close()


def set_retention(group_id, hours, path=None):
    hours = int(hours)
    if hours < 1:
        raise ValueError("retention must be at least 1 hour")
    con = _connect(path)
    try:
        with con:
            _ensure_group(con, group_id, time.time())
            con.execute("UPDATE group_meta SET retention_hours=? WHERE group_id=?", (hours, group_id))
    finally:
        con.close()
    return hours


def group_info(group_id, path=None):
    """Retention and first-seen time for a group. Defaults apply to a group the store has not seen yet."""
    con = _connect(path)
    try:
        row = con.execute("SELECT retention_hours, first_seen FROM group_meta WHERE group_id=?",
                          (group_id,)).fetchone()
        if row is None:
            return {"retention_hours": DEFAULT_RETENTION_HOURS, "first_seen": None}
        return {"retention_hours": row["retention_hours"], "first_seen": row["first_seen"]}
    finally:
        con.close()


def cleanup(now=None, path=None):
    """Delete messages older than each group's retention window. Returns {group_id: rows_deleted}."""
    now = float(now if now is not None else time.time())
    con = _connect(path)
    deleted = {}
    try:
        with con:
            groups = con.execute("SELECT group_id, retention_hours FROM group_meta").fetchall()
            for g in groups:
                cutoff = now - g["retention_hours"] * 3600
                cur = con.execute("DELETE FROM group_messages WHERE group_id=? AND ts<?", (g["group_id"], cutoff))
                if cur.rowcount:
                    deleted[g["group_id"]] = cur.rowcount
    finally:
        con.close()
    return deleted


def search(group_id, keyword, limit=10, path=None):
    kw = (keyword or "").strip()
    if not kw:
        return []
    like = "%" + kw.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT message_id, sender_name, ts, text, edited FROM group_messages "
            "WHERE group_id=? AND text LIKE ? ESCAPE '\\' ORDER BY ts DESC LIMIT ?",
            (group_id, like, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def messages_since(group_id, since_ts, path=None):
    con = _connect(path)
    try:
        rows = con.execute(
            "SELECT sender_name, ts, text FROM group_messages WHERE group_id=? AND ts>=? ORDER BY ts ASC",
            (group_id, float(since_ts))).fetchall()
        return [dict(r) for r in rows]
    finally:
        con.close()


def delete_user_messages(group_id, sender_id, path=None):
    """Delete one member's stored messages in one group. Returns the number of rows removed."""
    con = _connect(path)
    try:
        with con:
            cur = con.execute("DELETE FROM group_messages WHERE group_id=? AND sender_id=?", (group_id, sender_id))
            return cur.rowcount
    finally:
        con.close()


def count(group_id=None, path=None):
    con = _connect(path)
    try:
        if group_id is None:
            return con.execute("SELECT COUNT(*) FROM group_messages").fetchone()[0]
        return con.execute("SELECT COUNT(*) FROM group_messages WHERE group_id=?", (group_id,)).fetchone()[0]
    finally:
        con.close()
