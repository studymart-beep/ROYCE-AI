import json, os, re, threading, time, urllib.request, urllib.parse
from .llm import chat_text
from . import imagegen
from . import db, collectors, tasks
from . import leadbrain
from . import skills
from .style import VOICE_RULES, sanitize
from . import tgstore, tgwelcomes, tgmod
from .agent import budget, memory, approvals, permissions, jobs, audit

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = None
API = None
CACHE = {}      # (text) -> (ts, reply)
CACHE_TTL = 300

def api(method, **params):
    data = json.dumps(params).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/{method}",
                                 data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read())

def send_photo(chat_id, data, caption=""):
    """Upload raw image bytes to Telegram (multipart/form-data)."""
    boundary = "----royce" + os.urandom(8).hex()
    parts = []
    def field(name, value):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption[:1000])
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="photo"; filename="royce.png"\r\nContent-Type: image/png\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendPhoto", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=70) as r:
        return json.loads(r.read())

def download_photo(msg):
    """Largest size of a Telegram photo message, returned as (bytes, mime)."""
    sizes = msg.get("photo") or []
    if not sizes:
        return None
    best = max(sizes, key=lambda p: p.get("file_size", 0) or p.get("width", 0) * p.get("height", 0))
    info = api("getFile", file_id=best["file_id"])
    path = (info.get("result") or {}).get("file_path")
    if not path:
        raise RuntimeError("telegram returned no file path for the photo")
    with urllib.request.urlopen(f"https://api.telegram.org/file/bot{TOKEN}/{path}", timeout=70) as r:
        data = r.read()
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    return data, mime

def _send_into(chat_id, text):
    """Owner-approved post into a group or channel. Returns the telegram response."""
    return api("sendMessage", chat_id=chat_id, text=text)

from . import tgsend as _tgsend_boot
_tgsend_boot.set_sender(_send_into)
from . import tgprecautions as _tgprec_boot
_tgprec_boot.set_api(lambda method, **params: api(method, **params))


def send(chat_id, text):
    text = sanitize(text or "") or "(empty)"
    while text:
        chunk, text = text[:3900], text[3900:]
        api("sendMessage", chat_id=chat_id, text=chunk)

ENV_TOKEN_KEY = "TELEGRAM_BOT_TOKEN"

def _token_from_env_file():
    try:
        with open(os.path.join(BASE, ".env")) as f:
            for line in f:
                if line.startswith(ENV_TOKEN_KEY + "="):
                    return line.split("=", 1)[1].strip()
    except FileNotFoundError:
        pass
    return ""

def token_configured():
    """True when a bot token is set in the environment or .env. Never returns the value."""
    return bool(os.environ.get(ENV_TOKEN_KEY, "").strip()) or bool(_token_from_env_file())

def get_env():
    # Render sets the token as an env var (dashboard); locally it comes from .env. Env wins.
    global TOKEN, API
    if TOKEN: return
    tok = os.environ.get(ENV_TOKEN_KEY, "").strip() or _token_from_env_file()
    if not tok:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set (env var or .env)")
    TOKEN = tok
    API = f"https://api.telegram.org/bot{TOKEN}"

def get_updates(offset=None):
    url = API + "/getUpdates?timeout=50"
    if offset: url += f"&offset={offset}"
    # message carries group text and new_chat_members (a field of a message, not its own update type);
    # edited_message carries edits of group text
    url += "&allowed_updates=" + urllib.parse.quote(json.dumps(["message", "edited_message", "channel_post", "edited_channel_post"]))
    with urllib.request.urlopen(url, timeout=70) as r:
        return json.loads(r.read())

RETRY_EVERY = 600          # re-test unavailable tasks at most every 10 minutes
MAX_RETRY_ATTEMPTS = 144   # 10-minute checks for about 24 hours, then the task stays listed

TASK_SYSTEM = (
    "Classify one chat message sent to Royce AI, an ad agency assistant. "
    "is_task is true only when the user asks Royce to DO a piece of work (make, find, pull, write, draft, check, prepare), "
    "not when they chat, greet, or ask a quick factual question. "
    "Reply with STRICT JSON only, no prose: "
    "{\"is_task\": true or false, \"summary\": \"the work in under 12 words\", \"needs\": null or the connected account the work requires}. "
    "needs is one of: meta, tiktok, google, or null."
)

def classify(text):
    try:
        raw = chat_text([{"role": "system", "content": TASK_SYSTEM}, {"role": "user", "content": text}], temperature=0, timeout=20)
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        if not isinstance(d, dict):
            return {"is_task": False}
        if d.get("needs") not in (None, "") and d.get("needs") not in collectors.COLLECTORS:
            d["needs"] = None
        return d
    except Exception as e:
        print(f"[bot] classify failed: {e}", flush=True)
        return {"is_task": False}

def _needs_connected(name):
    if not name or name not in collectors.COLLECTORS:
        return True
    return collectors.get(name).configured

def _check_passes(check):
    t = check.get("type")
    if t == "collector":
        return _needs_connected(check.get("name"))
    if t == "any_collector":
        return bool(collectors.configured_names())
    return False

def handle_inbox_now(chat_id):
    s = collectors.get("gmail").summary(days=7)
    lines = [f"gmail: {s['unread']} unread, {s['recent']} received in the last 7 days."]
    if s["unread_senders"]:
        lines.append("unread from:")
        lines += [f"• {x['sender']} ({x['count']})" for x in s["unread_senders"]]
    send(chat_id, "\n".join(lines))

def memory_block(text, chat_id=None):
    try:
        return memory.block(text, chat_id=chat_id)
    except Exception as e:
        print(f"[bot] memory lookup failed: {e}", flush=True)
        return ""

def do_chat(text, hist=None, chat_id=None):
    mem = memory_block(text, chat_id)
    sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot() + ("\n\n" + mem if mem else "") + skills.skill_block(text) + skills.coding_block(text)
    msgs = [{"role": "system", "content": sysmsg}] + (hist[-HIST_MAX:] if hist else []) + [{"role": "user", "content": text}]
    return chat_text(msgs, timeout=40)

def run_task(task, announce=None, hist=None):
    """Do the work a stored task describes. announce() runs once the work succeeded, just before the result goes out."""
    check = task.get("check") or {}
    action = check.get("action", "chat")
    chat_id, desc = task["chat_id"], task["description"]
    if action == "report":
        if announce: announce()
        handle_report(chat_id, desc)
        return "[sent campaign report]"
    if action == "image":
        data = imagegen.generate(check.get("prompt") or desc)  # raises ImageError naming the service
        if announce: announce()
        send_photo(chat_id, data, caption=(check.get("prompt") or desc)[:200])
        return "[sent generated image]"
    if action == "inbox":
        if announce: announce()
        handle_inbox_now(chat_id)
        return "[sent inbox summary]"
    reply = do_chat(desc, hist, chat_id=chat_id)
    if announce: announce()
    send(chat_id, reply)
    return reply

def run_new_task(chat_id, text, action, needs=None, summary="", hist=None):
    """Record a task, confirm it and do it. Work that cannot run is kept as unavailable and retried."""
    check = {"type": "retry", "action": action, "attempts": 0}
    if needs and not _needs_connected(needs):
        label = collectors.COLLECTORS[needs].label
        tasks.add_task(chat_id, text, status="unavailable", check={"type": "collector", "name": needs, "action": action},
                       note=f"{needs} not connected. {summary}".strip())
        reply = f"can't do that yet, {label} isn't connected. i saved it on my list and i'll ping u the moment it is ✅"
        send(chat_id, reply)
        return reply
    t = tasks.add_task(chat_id, text, status="open", check=check, note=summary)
    send(chat_id, "on it, added to my list. ill ping u when its done")
    try:
        reply = run_task(t, hist=hist)
    except budget.BudgetExceeded:
        tasks.complete_task(t["id"], note="stopped: hourly ai budget reached")
        raise
    except Exception as e:
        reason = str(e)[:300] or e.__class__.__name__
        check["attempts"] = 1
        tasks.mark_unavailable(t["id"], note=reason, check=check)
        msg = f"couldn't finish that just now: {reason}\nkept it on my list, i'll retry and ping u when it works ✅"
        send(chat_id, msg)
        return msg
    tasks.complete_task(t["id"], note="done")
    return reply

def retry_unavailable():
    """Re-test every unavailable task. Connector tasks wait for the connector; work that failed is retried."""
    for t in tasks.unavailable_tasks():
        check = dict(t.get("check") or {})
        ctype = check.get("type")
        if ctype not in ("collector", "any_collector", "retry"):
            continue
        chat_id = t["chat_id"]
        if ctype in ("collector", "any_collector"):
            if not _check_passes(check):
                continue
            ready = f"the thing u asked for earlier is now available ✅\n\n{t['description']}"
            try:
                run_task(dict(t, check={**check, "action": check.get("action", "chat")}), announce=lambda: send(chat_id, ready))
            except Exception as e:
                reason = str(e)[:300] or e.__class__.__name__
                check = {"type": "retry", "action": check.get("action", "chat"), "attempts": 1}
                tasks.mark_unavailable(t["id"], note=reason, check=check)
                send(chat_id, f"it's connected now but it still failed: {reason}. kept on my list, i'll retry ✅")
                continue
            tasks.complete_task(t["id"], note="done")
            continue
        # ctype == "retry": the work itself failed earlier; try again quietly
        attempts = int(check.get("attempts", 0))
        if attempts >= MAX_RETRY_ATTEMPTS:
            continue
        ready = f"the thing u asked for earlier is now available ✅\n\n{t['description']}"
        try:
            run_task(t, announce=lambda: send(chat_id, ready))
        except Exception as e:
            reason = str(e)[:300] or e.__class__.__name__
            check["attempts"] = attempts + 1
            tasks.mark_unavailable(t["id"], note=reason, check=check)
            if check["attempts"] == MAX_RETRY_ATTEMPTS:
                send(chat_id, f"gave up retrying '{t['description']}' after many tries. last error: {reason}")
            print(f"[bot] retry {t['id']} failed ({check['attempts']}): {reason}", flush=True)
            continue
        tasks.complete_task(t["id"], note="done")

def handle_tasks(chat_id):
    items = tasks.list_tasks(chat_id=chat_id)
    if not items:
        send(chat_id, "nothing on my list yet. give me work and i'll track it until it's done.")
        return
    icon = {"open": "⏳", "done": "✅", "unavailable": "⏸"}
    lines = []
    for t in items[-20:]:
        line = f"{icon.get(t['status'], '•')} #{t['id']} {t['status']}: {t['description'][:90]}"
        if t.get("note") and t["status"] != "done":
            line += f"\n    {t['note'][:140]}"
        lines.append(line)
    send(chat_id, "your tasks (latest 20):\n" + "\n".join(lines))

SYSTEM_PROMPT = (
    "You are Royce AI, the in-house AI for Royce Marketing Agency, a Nigerian ad agency in Lagos. "
    "You speak like a sharp, friendly colleague: concise, confident, no corporate fluff, light Nigerian flavor is fine. "
    "You know the agency runs Meta, TikTok and Google ad campaigns for clients and maintains a CRM of leads. "
    "Never introduce or re-introduce yourself unless explicitly asked who you are. "
    "Answer the actual question directly. "
    "You track tasks until completion. If something the user asked for is not available, tell them plainly and say you will notify them when it is, then actually follow up. "
    + VOICE_RULES
)
HIST_MAX = 20

def crm_snapshot():
    s = db.summary()
    st = ", ".join(f"{k} {v}" for k, v in sorted(s["by_status"].items())) or "none yet"
    return f"Live CRM snapshot: {s['total']} leads total. By status: {st}. {len(s['overdue'])} overdue follow-ups."

REPORT_WORDS = ("report", "performance", "campaign", "campaigns", "spend", "roas", "ctr", "how are the ads", "ads doing", "ad results")

def handle_report(chat_id, text):
    names = collectors.configured_names()
    if not names:
        tasks.add_task(chat_id, text, status="unavailable", check={"type": "any_collector", "action": "report"}, note="no ad platform connected")
        send(chat_id, (
            "no ad platforms connected yet. connect at least one so i can pull real numbers:\n\n"
            "• meta: system user token + ad account id. in Meta Business Settings > Users > System users, "
            "create a system user, assign your ad account, generate a token with ads_read. "
            "account id is the number after act_ in your ad account.\n"
            "• tiktok: business api access token from your TikTok for Business developer app, plus the advertiser id.\n"
            "• google ads: coming soon (needs oauth + developer token).\n\n"
            "paste them in the reporting page of royce ai.\n\nsaved your request on my list. i\'ll run it and ping u the moment a platform is connected ✅"))
        return
    preset = "last_7d"
    for p, words in (("yesterday", ("yesterday",)), ("today", ("today",)), ("last_30d", ("30 days", "month")), ("last_14d", ("14 days", "two weeks")), ("last_7d", ("7 days", "week"))):
        if any(w in text.lower() for w in words):
            preset = p
            break
    api("sendChatAction", chat_id=chat_id, action="typing")
    rows, errors = collectors.fetch_all(preset)
    if not rows:
        msg = "pulled from " + ", ".join(names) + " but no campaign rows came back for " + preset + "."
        if errors:
            msg += "\n" + "\n".join(f"{e['collector']}: {e['error']}" for e in errors)
        send(chat_id, msg)
        return
    table = "\n".join(f"{r['platform']} | {r['campaign']} | spend {r['spend']} | impr {r['impressions']} | clicks {r['clicks']} | ctr {r['ctr']}% | cpc {r['cpc']}" for r in rows)
    tot_spend = sum(r["spend"] for r in rows)
    tot_clicks = sum(r["clicks"] for r in rows)
    tot_impr = sum(r["impressions"] for r in rows)
    prompt = (f"Here are the REAL campaign rows for {preset} (use only these numbers, do not invent any):\n{table}\n\n"
              f"Totals: spend {tot_spend:.2f}, impressions {tot_impr}, clicks {tot_clicks}.\n"
              f"Write a client-ready summary paragraph of the performance: what worked, what didn't, one clear recommendation. "
              f"Plain text, no headers, no bullet glyphs, no greeting or self-introduction.")
    sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot()
    narrative = chat_text([{"role": "system", "content": sysmsg}, {"role": "user", "content": prompt}], temperature=0.5, timeout=40)
    footer = "\n\n" + "\n".join(f"{e['collector']}: {e['error']}" for e in errors) if errors else ""
    send(chat_id, narrative + "\n\n" + table + footer)

def handle_followup(chat_id, arg):
    arg = (arg or "").strip()
    if not arg:
        send(chat_id, "usage: /followup <lead name or id>\nexample: /followup Chidi or /followup 3")
        return
    lead = leadbrain.find_lead(arg)
    if not lead:
        send(chat_id, f"no lead matches '{arg}'. check /leads for names and ids.")
        return
    api("sendChatAction", chat_id=chat_id, action="typing")
    draft = leadbrain.draft_followup(lead)
    send(chat_id, f"follow-up for #{lead['id']} {lead['name']}:\n\n{draft}\n\nwant me to tweak it or add a note?")


def handle_skills(chat_id):
    lines = ["marketing skills i use in answers (max 2 per reply, picked by what you ask):"]
    for s in skills.list_skills():
        lines.append(f"• {s['name']}: {s['description']}")
    lines.append("\nask about hooks, brand, posts, funnels, reports or pricing and i pull the right one.")
    send(chat_id, "\n".join(lines))


def handle_deny(chat_id, arg):
    from .agent import chatplan
    rid = (arg or "").strip().lstrip("#")
    if not rid:
        send(chat_id, "send /deny <task id> to stop a waiting task.")
        return
    rec = chatplan.get(rid)
    if not rec or rec.get("chat_id") != chat_id or rec.get("status") != "waiting_approval":
        send(chat_id, f"no task waiting for approval with id {rid}.")
        return
    send(chat_id, sanitize(chatplan.summary(chatplan.resolve_approval(rid, approved=False))))

def handle_approve(chat_id, arg):
    rid = (arg or "").strip().lstrip("#")
    if not rid:
        items = approvals.pending(chat_id)
        if not items:
            send(chat_id, "nothing waiting for approval.")
            return
        lines = [f"#{r['id']} {r['action_class']}: {r.get('summary') or ''}".strip() for r in items]
        send(chat_id, "waiting for your approval:\n" + "\n".join(lines) + "\n\nsend /approve <id> to run one.")
        return
    from .agent import chatplan
    waiting = chatplan.task_for_approval(rid, chat_id)
    if waiting:
        send(chat_id, sanitize(chatplan.summary(chatplan.resolve_approval(waiting, approved=True))))
        return
    ok, msg = approvals.approve(chat_id, rid)
    send(chat_id, msg)

def handle_group_update(msg, chat, edited):
    """Groups and supergroups go to app/tggroups.py. Private chats never reach this path."""
    from . import tggroups
    owner = leadbrain.OWNER_CHAT_ID
    token = budget.bind(chat["id"])
    try:
        tggroups.handle_group_update(msg, edited, send, api, owner_id=owner)
    except budget.BudgetExceeded as e:
        try: send(chat["id"], str(e))
        except Exception: pass
    except Exception as e:
        print(f"[bot] group update error: {e}", flush=True)
    finally:
        budget.unbind(token)

def handle_update(u):
    # edited_message carries edits of earlier messages; treat them as the same message id
    edited = "edited_message" in u or "edited_channel_post" in u
    msg = u.get("message") or u.get("edited_message") or u.get("channel_post") or u.get("edited_channel_post") or {}
    chat = msg.get("chat") or {}
    from . import tggroups
    if tggroups.is_group(chat):
        return handle_group_update(msg, chat, edited)
    if chat.get("type") != "private": return
    if edited: return  # private edits were ignored before; keep it that way
    from . import tggroups as _tg
    from . import tgsend as _ts
    sender_id = (msg.get("from") or {}).get("id")
    # telegram is ops only: all ai chat lives in the desktop app and the pwa
    if sender_id != leadbrain.OWNER_CHAT_ID:
        send(chat["id"], PRIVATE_POINTER)
        audit.record("user", "private_pointer", "non_owner", "ok")
        return
    if _ts.parse((msg.get("text") or "")) is not None:
        kind, name, body = _ts.parse(msg["text"])
        ok, reply = _ts.queue_send(chat["id"], kind, name, body)
        send(chat["id"], reply)
        return
    text = (msg.get("text") or "").strip()
    if text.startswith(("/help", "/deletemydata")):
        if _tg.handle_private_command(chat["id"], sender_id, text, lambda cid, t: send(cid, t)):
            return
    token = budget.bind(chat["id"])
    try:
        return _handle_private_ops(chat["id"], text)
    except budget.BudgetExceeded as e:
        try: send(chat["id"], str(e))
        except Exception: pass
    finally:
        budget.unbind(token)

PRIVATE_POINTER = "all ai chat lives in the desktop app or pwa. im on telegram for group ops and alerts only"
PRIVATE_OPS_HELP = (
    "royce ops on telegram. chat with me in the desktop app or the pwa.\n"
    "/start this list\n"
    "/leads view leads\n"
    "/addlead Name | Business | Contact\n"
    "/report pipeline summary\n"
    "/followup [lead id] followup drafts due\n"
    "/tasks open items on my list\n"
    "/approve <id> or /deny <id> answer a pending approval\n"
    "/health job and provider status\n"
    "/skills marketing playbooks i use\n"
    "tell group <name>: <text> or post channel <name>: <text> send into a stored group or channel\n"
    "/help and /deletemydata yes, the group ops above"
)

def _handle_private_ops(chat_id, text):
    """Owner private chat: ops and admin commands only. No ai chat, no generators."""
    if text == "/start":
        send(chat_id, PRIVATE_OPS_HELP)
    elif text.startswith("/addlead "):
        p = [x.strip() for x in text[9:].split("|")]
        lid = db.add_lead(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else "")
        send(chat_id, f"✅ lead #{lid} added: {p[0]}")
    elif text == "/leads":
        leads = db.list_leads()
        send(chat_id, "\n".join(f"#{l['id']} {l['name']}: {l['status']} (followup {l['next_followup'] or 'none'})" for l in leads) or "no leads yet, /addlead Name | Business | Contact")
    elif text == "/report":
        s = db.summary()
        send(chat_id, f"📊 {s['total']} leads. {json.dumps(s['by_status'])}. {len(s['overdue'])} overdue.")
    elif text == "/tasks": handle_tasks(chat_id)
    elif text == "/followup" or text.startswith("/followup "): handle_followup(chat_id, text[9:])
    elif text == "/skills": handle_skills(chat_id)
    elif text == "/health": send(chat_id, jobs.format_status())
    elif text == "/approve" or text.startswith("/approve "): handle_approve(chat_id, text[8:])
    elif text == "/deny" or text.startswith("/deny "): handle_deny(chat_id, text[5:])
    elif text.startswith("/"): send(chat_id, "unknown command. ops commands are in /start. chat with me in the desktop app or the pwa.")
    else: send(chat_id, PRIVATE_POINTER)

def _on_signal(signum, frame):
    # safe stop: the poll loop exits and running planner tasks stop before their next step
    from .agent import chatplan
    chatplan.STOP_EVENT.set()
    raise SystemExit(0)

def _setup_bot():
    """One-time startup: verify the token (getMe), recover interrupted tasks, start background jobs.
    Raises if Telegram is unreachable or the token is rejected; the caller decides whether to retry."""
    from .agent import chatplan
    get_env()
    me = api("getMe")
    print(f"[bot] connected as @{me['result']['username']}", flush=True)
    # clear any old mini app menu button so it stops pointing at the removed pages
    try:
        api("setChatMenuButton", menu_button={"type": "default"})
    except Exception as e:
        print(f"[bot] could not reset menu button: {e}", flush=True)
    n = chatplan.recover_on_start()
    if n:
        print(f"[bot] marked {n} interrupted task(s) as failed (not repeated)", flush=True)
    jobs.start()
    audit.record("bot", "bot_started", "telegram", "ok")

def poll_forever(stop_event):
    """The getUpdates long-poll loop. Its offset is local to this call (stateless per poll), so one
    background thread is safe. Returns once stop_event is set (checked between polls; a poll in flight
    finishes within its 50s server timeout, so callers join with a timeout)."""
    offset = None
    last_check = 0
    last_cleanup = 0
    while not stop_event.is_set():
        try:
            data = get_updates(offset)
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            if stop_event.wait(3):
                break
        if time.time() - last_check >= RETRY_EVERY:
            last_check = time.time()
            try:
                retry_unavailable()
            except Exception as e:
                print(f"[bot] retry check error: {e}", flush=True)
        try:
            nudge = leadbrain.run_nudge_check(time.time())
            if nudge:
                send(nudge[0], nudge[1])
        except Exception as e:
            print(f"[bot] nudge check error: {e}", flush=True)
        if time.time() - last_cleanup >= tgstore.CLEANUP_EVERY:
            last_cleanup = time.time()
            try:
                removed = tgstore.cleanup()
                if removed:
                    # counts only: deleted message content is never audited
                    audit.record("bot", "group_messages_expired", "telegram", f"rows={sum(removed.values())}")
            except Exception as e:
                print(f"[bot] group cleanup error: {e}", flush=True)

def main():
    # standalone entry point (python -m app.telegram_bot): same behaviour as before, SIGTERM stops safely
    import signal
    _setup_bot()
    signal.signal(signal.SIGTERM, _on_signal)
    poll_forever(threading.Event())

if __name__ == "__main__":
    main()
