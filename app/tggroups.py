"""Group and supergroup handling for the Royce bot: stores text, answers /search and /recap.

Private chats never reach this module (telegram_bot.handle_update routes them as before).
The bot only sees messages in groups it is a member of, and only text is stored.
"""
import time

from . import tgstore
from .llm import chat_text
from .style import sanitize

GROUP_TYPES = ("group", "supergroup")
RECAP_DEFAULT_HOURS = 24
RECAP_MAX_HOURS = 168
RECAP_MAX_LINES = 300


def is_group(chat):
    return (chat or {}).get("type") in GROUP_TYPES


def store_group_message(msg, edited=False):
    """Store one group text message (or an edit of one). Returns the store outcome string."""
    chat = msg.get("chat") or {}
    text = msg.get("text") or ""
    if not text or not is_group(chat):
        return "ignored"
    sender = msg.get("from") or {}
    name = sender.get("first_name") or sender.get("username") or ""
    return tgstore.save_message(chat["id"], msg["message_id"], sender.get("id"), name, text,
                                ts=msg.get("date"), edited=edited)


def _since_label(first_seen):
    return time.strftime("%d %b %H:%M", time.localtime(first_seen))


def handle_search(chat_id, group_id, arg, send):
    kw = (arg or "").strip()
    if not kw:
        send(chat_id, "usage: /search <keyword>\nexample: /search delivery")
        return
    hits = tgstore.search(group_id, kw, limit=8)
    if not hits:
        send(chat_id, f"no messages with '{kw}' in what i've stored for this group.")
        return
    lines = []
    for h in hits:
        when = time.strftime("%d %b %H:%M", time.localtime(h["ts"]))
        tag = " (edited)" if h["edited"] else ""
        lines.append(f"{when} {h['sender_name'] or 'someone'}{tag}: {h['text'][:160]}")
    send(chat_id, f"{len(hits)} hit(s) for '{kw}' (newest first):\n" + "\n".join(lines))


def _parse_hours(arg):
    arg = (arg or "").strip()
    if not arg:
        return RECAP_DEFAULT_HOURS
    try:
        h = int(arg)
    except ValueError:
        raise ValueError("usage: /recap [hours], e.g. /recap 6")
    if h < 1 or h > RECAP_MAX_HOURS:
        raise ValueError(f"hours must be between 1 and {RECAP_MAX_HOURS}")
    return h


def handle_recap(chat_id, group_id, arg, send, now=None):
    now = now if now is not None else time.time()
    try:
        hours = _parse_hours(arg)
    except ValueError as e:
        send(chat_id, str(e))
        return
    info = tgstore.group_info(group_id)
    first_seen = info.get("first_seen")
    window_start = now - hours * 3600
    rows = tgstore.messages_since(group_id, window_start)
    prefix = ""
    if first_seen is not None and first_seen > window_start:
        prefix = f"ive only been in this group since {_since_label(first_seen)}, so this covers that time only. "
    if not rows:
        send(chat_id, prefix + f"nothing stored in the last {hours}h. i only keep text messages sent while i was here.")
        return
    rows = rows[-RECAP_MAX_LINES:]
    transcript = "\n".join(f"{time.strftime('%H:%M', time.localtime(r['ts']))} {r['sender_name'] or 'someone'}: {r['text'][:400]}"
                           for r in rows)
    system = ("Summarize this Telegram group chat for a busy agency owner. Plain text, lowercase-friendly, "
              "no headers, no bullet glyphs, no asterisks. 4 to 8 short sentences. Only use what is in the transcript.")
    try:
        summary = chat_text([{"role": "system", "content": system},
                             {"role": "user", "content": transcript}], temperature=0.3, timeout=40)
    except Exception as e:
        print(f"[bot] recap llm failed: {e}", flush=True)
        send(chat_id, prefix + "couldn't summarize right now (the llm is busy). try again in a minute.")
        return
    send(chat_id, prefix + sanitize(summary))
