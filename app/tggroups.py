"""Group and supergroup handling for the Royce bot.

Everything a group sends goes through handle_group_update(). It is written against injected
send(chat_id, text) and api(method, **params) callables, so tests drive it with fakes and never
touch the network. telegram_bot.handle_update routes group and supergroup updates here; private
chats never reach this module.

What it does, in order for a group message:
  1. stores text messages (idempotent, edits in place) in tgstore
  2. new_chat_members: sends the welcome text if welcomes are on for that group
  3. unless the sender is exempt, runs moderation checks and records review-queue flags
  4. answers group commands; config commands are admin-only and every refusal is audited

Honest limits: the bot only sees messages in groups it is a member of, and only text is stored.
Nothing here bans, deletes or restricts anyone. Flags are a review queue.
"""
import time

from . import tgstore, tgwelcomes, tgmod, tgallow, tgadmin
from .llm import chat_text
from .style import sanitize
from . import tgprecautions, tgrules
from .agent import audit

GROUP_TYPES = ("group", "supergroup", "channel")  # channel posts are stored like group text
RECAP_DEFAULT_HOURS = 24
RECAP_MAX_HOURS = 168
RECAP_MAX_LINES = 300
PROTECT_ADMINS = True  # admins and allowlisted members are exempt from flood and ads flags
DELETE_CONFIRM = "yes"

HELP_GROUP = (
    "royce ai in this group:\n"
    "/search <keyword> find stored messages\n"
    "/recap [hours] summary of the last hours (default 24, max 168)\n"
    "/deletemydata yes delete your stored messages here\n"
    "admins only: /setwelcome <text>, /welcome on|off, /welcomestatus, /setretention <hours>, "
    "/groupconfig, /flags, /flag <id> safe|dismiss|escalate, /allowadd <user id>, /allowremove <user id>\n\n"
    "what i can and can't see: i only see messages in groups i'm a member of, and only their text. "
    "i can't read anyone's private chats with each other, and i never ban, delete or restrict anyone. "
    "flags go to a review queue for admins."
)

HELP_PRIVATE = (
    "royce ai. in private chat i keep working as before.\n"
    "/deletemydata yes deletes the group messages i stored from you, in every group.\n\n"
    "honest limits: the bot api cannot read your private chats with other people, and it cannot see "
    "messages in groups it isn't in. i only see groups where i'm a member, and in those i store text only. "
    "channel history is only readable from the time the bot was added as an admin."
)


def is_group(chat):
    return (chat or {}).get("type") in GROUP_TYPES


def _name(user, chat=None):
    user = user or {}
    if not user and chat:  # channel posts have no sender; the channel title is the name
        return chat.get("title") or chat.get("username") or ""
    return user.get("first_name") or user.get("username") or ""


def _store_text(msg, edited):
    text = msg.get("text") or ""
    if not text:
        return "ignored"
    sender = msg.get("from") or {}
    return tgstore.save_message(msg["chat"]["id"], msg["message_id"], sender.get("id"), _name(sender, msg.get("chat")),
                                text, ts=msg.get("date"), edited=edited)


def _exempt(group_id, user_id, api, owner_id):
    if user_id is None:  # channel posts: no sender, so no user-level exemption
        return False
    if not PROTECT_ADMINS or user_id is None:
        return False
    if owner_id is not None and user_id == owner_id:
        return True
    if tgadmin.is_admin(group_id, user_id, api):
        return True
    return tgallow.is_allowed(group_id, user_id)


def _run_moderation(msg, edited, api, owner_id):
    chat = msg["chat"]
    sender = msg.get("from") or {}
    gid, mid, uid = chat["id"], msg["message_id"], sender.get("id")
    exempt = _exempt(gid, uid, api, owner_id)
    ts = msg.get("date")
    hits = tgmod.inspect(gid, mid, uid, msg.get("text") or "", ts=ts, exempt=exempt or edited)
    for category, reason in hits:
        if category == "flood" and edited:
            continue
        flag_id, created = tgmod.record(gid, mid, uid, category, reason, ts=ts)
        if created:
            audit.record("bot", "group_flag_created", f"group={gid} category={category}", f"flag={flag_id}")
            if category == "link_suspicious" and owner_id:
                try:
                    tgprecautions.queue_for_flag(owner_id, gid, chat.get("title") or "", mid, uid, flag_id)
                except Exception as e:
                    print(f"[bot] precaution queue failed: {e}", flush=True)


def _welcome(msg, send):
    chat = msg["chat"]
    members = msg.get("new_chat_members") or []
    lines = tgwelcomes.welcome_for_join(chat["id"], chat.get("title") or "", members)
    for line in lines:
        send(chat["id"], sanitize(line))


def _refuse(chat_id, command, user_id, send):
    audit.record("group", "config_refused", f"{command} user={user_id}", "not_admin")
    send(chat_id, "only admins can change that here.")


def _admin_only(command, chat_id, user_id, api, send):
    if tgadmin.is_admin(chat_id, user_id, api):
        return True
    _refuse(chat_id, command, user_id, send)
    return False


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
        prefix = f"ive only been in this group since {time.strftime('%d %b %H:%M', time.localtime(first_seen))}, so this covers that time only. "
    if not rows:
        send(chat_id, prefix + f"nothing stored in the last {hours}h. i only keep text messages sent while i was here.")
        return
    rows = rows[-RECAP_MAX_LINES:]
    transcript = "\n".join(f"{time.strftime('%H:%M', time.localtime(r['ts']))} {r['sender_name'] or 'someone'}: {r['text'][:400]}"
                           for r in rows)
    system = ("Summarize this Telegram group chat for a busy agency owner. Plain text, lowercase-friendly, "
              "no headers, no bullet glyphs, no asterisks. 4 to 8 short sentences. Only use what is in the transcript.")
    rules_line = tgrules.context_line(group_id)
    if rules_line:
        system += " " + rules_line + ". Note any talk in the transcript that breaks these rules."
    try:
        summary = chat_text([{"role": "system", "content": system},
                             {"role": "user", "content": transcript}], temperature=0.3, timeout=40)
    except Exception as e:
        print(f"[bot] recap llm failed: {e}", flush=True)
        send(chat_id, prefix + "couldn't summarize right now (the llm is busy). try again in a minute.")
        return
    send(chat_id, prefix + sanitize(summary))


def handle_setwelcome(chat_id, arg, send):
    try:
        tgwelcomes.set_text(chat_id, arg)
    except ValueError as e:
        send(chat_id, str(e))
        return
    send(chat_id, "welcome text saved. use /welcomestatus to see it.")


def handle_welcome_toggle(chat_id, arg, send):
    val = (arg or "").strip().lower()
    if val not in ("on", "off"):
        send(chat_id, "usage: /welcome on|off")
        return
    tgwelcomes.set_enabled(chat_id, val == "on")
    send(chat_id, f"welcomes are {val} for this group.")


def handle_welcomestatus(chat_id, send):
    st = tgwelcomes.status(chat_id)
    send(chat_id, f"welcomes: {'on' if st['enabled'] else 'off'}\ntext: {st['text']}")


def handle_setretention(chat_id, arg, send):
    try:
        hours = tgstore.set_retention(chat_id, arg.strip())
    except (ValueError, TypeError):
        send(chat_id, "usage: /setretention <hours>, at least 1. default is 720 (30 days).")
        return
    send(chat_id, f"stored messages here are kept for {hours} hours.")


def handle_groupconfig(chat_id, send):
    info = tgstore.group_info(chat_id)
    st = tgwelcomes.status(chat_id)
    allowed = len(tgallow.members(chat_id))
    pending = len([f for f in tgmod.list_flags(status="pending", limit=500) if f["group_id"] == chat_id])
    send(chat_id, (
        f"group config:\n"
        f"retention: {info['retention_hours']}h\n"
        f"welcomes: {'on' if st['enabled'] else 'off'}\n"
        f"allowlisted users: {allowed}\n"
        f"protect admins from flood/ads flags: {'on' if PROTECT_ADMINS else 'off'}\n"
        f"pending flags: {pending}"))


def handle_precautions(chat_id, arg, send):
    a = (arg or "").strip().lower()
    if a not in ("on", "off"):
        send(chat_id, "usage: /precautions on|off (admin only; default off)")
        return
    tgprecautions.set_on(chat_id, a == "on")
    audit.record("group", "precautions_set", f"group={chat_id}", a)
    send(chat_id, "precautions " + a + ". " + (
        "flagged links will be queued for your approval in your dm, nothing is deleted without your yes."
        if a == "on" else "flagged links stay review-only."))


def handle_setrules(chat_id, arg, send):
    text = (arg or "").strip()
    if not text:
        send(chat_id, "usage: /setrules <rules text>")
        return
    tgrules.set_rules(chat_id, text)
    audit.record("group", "rules_set", f"group={chat_id}", f"len={len(text)}")
    send(chat_id, "rules saved. members can see them with /rules.")


def handle_flags(chat_id, send):
    items = [f for f in tgmod.list_flags(status="pending", limit=100) if f["group_id"] == chat_id][:10]
    if not items:
        send(chat_id, "no pending flags in this group.")
        return
    lines = [f"#{f['id']} {f['category']}: {f['reason']} (message {f['message_id']})" for f in items]
    send(chat_id, "pending flags (review only, nothing was removed):\n" + "\n".join(lines) +
         "\n\n/flag <id> safe|dismiss|escalate")


FLAG_ACTIONS = {"safe": "reviewed_safe", "dismiss": "dismissed", "escalate": "escalated"}


def handle_flag(chat_id, arg, send):
    parts = (arg or "").split()
    if len(parts) != 2 or not parts[0].lstrip("#").isdigit() or parts[1].lower() not in FLAG_ACTIONS:
        send(chat_id, "usage: /flag <id> safe|dismiss|escalate")
        return
    fid = int(parts[0].lstrip("#"))
    f = tgmod.get(fid)
    if not f or f["group_id"] != chat_id:
        send(chat_id, f"no flag #{fid} in this group.")
        return
    status = FLAG_ACTIONS[parts[1].lower()]
    tgmod.set_status(fid, status)
    audit.record("group", "flag_status", f"flag={fid} group={chat_id}", status)
    send(chat_id, f"flag #{fid} marked {status}.")


def handle_allow(chat_id, arg, send, add):
    raw = (arg or "").strip().split()
    if len(raw) != 1 or not raw[0].lstrip("-").isdigit():
        send(chat_id, "usage: /allowadd <user id> or /allowremove <user id>")
        return
    uid = int(raw[0])
    if add:
        tgallow.add(chat_id, uid)
        audit.record("group", "allowlist_add", f"group={chat_id}", "ok")
        send(chat_id, "added to the allowlist: flood and ads checks skip them here.")
    else:
        existed = tgallow.remove(chat_id, uid)
        audit.record("group", "allowlist_remove", f"group={chat_id}", "ok" if existed else "not_listed")
        send(chat_id, "removed from the allowlist." if existed else "that user wasn't on the allowlist.")


def handle_deletemydata(chat_id, user_id, arg, send, private):
    if (arg or "").strip().lower() != DELETE_CONFIRM:
        scope = "every group i've stored your messages from" if private else "this group"
        send(chat_id, f"this deletes your stored messages from {scope}. reply /deletemydata yes to confirm.")
        return
    if user_id is None:
        send(chat_id, "i can't tell who you are from this message, so nothing was deleted.")
        return
    if private:
        removed = 0
        for gid in tgstore.groups_with_sender(user_id):
            n = tgstore.delete_user_messages(gid, user_id)
            removed += n
            audit.record("user", "group_messages_deleted", f"group={gid}", f"rows={n}")
        send(chat_id, f"deleted {removed} stored message(s) of yours across groups. memory and crm are not touched by this.")
        return
    n = tgstore.delete_user_messages(chat_id, user_id)
    audit.record("user", "group_messages_deleted", f"group={chat_id}", f"rows={n}")
    send(chat_id, f"deleted {n} stored message(s) of yours from this group.")


def handle_group_update(msg, edited, send, api, owner_id=None, now=None):
    """Entry point for group and supergroup updates. Returns True if it handled the update."""
    chat = msg.get("chat") or {}
    if not is_group(chat):
        return False
    gid = chat["id"]
    sender = msg.get("from") or {}
    uid = sender.get("id")
    if chat.get("title") or chat.get("type"):
        try:
            tgstore.set_chat_info(gid, chat.get("title") or chat.get("username") or "", chat.get("type") or "")
        except Exception as e:
            print(f"[bot] chat info failed: {e}", flush=True)
    text_now = (msg.get("text") or "").strip()
    # commands are not conversation: never store them, so /search and /recap do not index bot traffic
    if not (text_now.startswith("/") and not edited):
        try:
            _store_text(msg, edited)
        except Exception as e:
            print(f"[bot] group store failed: {e}", flush=True)
    if msg.get("new_chat_members") and not edited:
        try:
            _welcome(msg, send)
        except Exception as e:
            print(f"[bot] welcome failed: {e}", flush=True)
        return True
    text = (msg.get("text") or "").strip()
    if not text:
        return True
    if not edited:
        try:
            _run_moderation(msg, edited, api, owner_id)
        except Exception as e:
            print(f"[bot] moderation failed: {e}", flush=True)
    if edited or not text.startswith("/"):
        return True
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()
    is_owner = owner_id is not None and uid == owner_id
    admin = lambda: is_owner or tgadmin.is_admin(gid, uid, api)
    if cmd == "/search":
        handle_search(gid, gid, arg, send)
    elif cmd == "/recap":
        handle_recap(gid, gid, arg, send, now=now)
    elif cmd == "/help":
        send(gid, HELP_GROUP)
    elif cmd == "/deletemydata":
        handle_deletemydata(gid, uid, arg, send, private=False)
    elif cmd == "/rules":
        send(gid, tgrules.get(gid) or "no rules set for this group yet. an admin can /setrules <text>.")
    elif cmd in ("/setwelcome", "/welcome", "/welcomestatus", "/setretention", "/groupconfig",
                 "/flags", "/flag", "/allowadd", "/allowremove", "/precautions", "/setrules"):
        if not admin():
            _refuse(gid, cmd, uid, send)
            return True
        if cmd == "/setwelcome": handle_setwelcome(gid, arg, send)
        elif cmd == "/welcome": handle_welcome_toggle(gid, arg, send)
        elif cmd == "/welcomestatus": handle_welcomestatus(gid, send)
        elif cmd == "/setretention": handle_setretention(gid, arg, send)
        elif cmd == "/groupconfig": handle_groupconfig(gid, send)
        elif cmd == "/flags": handle_flags(gid, send)
        elif cmd == "/flag": handle_flag(gid, arg, send)
        elif cmd == "/allowadd": handle_allow(gid, arg, send, add=True)
        elif cmd == "/allowremove": handle_allow(gid, arg, send, add=False)
        elif cmd == "/precautions": handle_precautions(gid, arg, send)
        elif cmd == "/setrules": handle_setrules(gid, arg, send)
    return True


def handle_private_command(chat_id, user_id, text, send):
    """Private-chat commands owned by this module. Returns True if handled."""
    cmd, _, arg = text.partition(" ")
    cmd = cmd.lower()
    if cmd == "/help":
        send(chat_id, HELP_PRIVATE)
        return True
    if cmd == "/deletemydata":
        handle_deletemydata(chat_id, user_id, arg, send, private=True)
        return True
    return False
