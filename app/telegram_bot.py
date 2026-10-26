import json, os, re, time, urllib.request, urllib.parse
from .llm import chat_text, chat_vision, generate_script_package
from . import imagegen
from . import db, collectors, workstore, tasks
from . import leadbrain
from . import skills

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = None
API = None
CHAT_HIST = {}  # chat_id -> list of messages
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

def send_voice(chat_id, data, caption=""):
    """Upload ogg opus bytes as a Telegram voice note (multipart sendVoice)."""
    boundary = "----royce" + os.urandom(8).hex()
    parts = []
    def field(name, value):
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    field("chat_id", str(chat_id))
    if caption:
        field("caption", caption[:1000])
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="voice"; filename="royce.ogg"\r\nContent-Type: audio/ogg\r\n\r\n'.encode())
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(parts)
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendVoice", data=body,
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

def send(chat_id, text):
    text = text.strip() or "(empty)"
    while text:
        chunk, text = text[:3900], text[3900:]
        api("sendMessage", chat_id=chat_id, text=chunk)

def get_env():
    global TOKEN, API
    if TOKEN: return
    with open(os.path.join(BASE, ".env")) as f:
        for line in f:
            if line.startswith("TELEGRAM_BOT_TOKEN="):
                TOKEN = line.split("=", 1)[1].strip()
    API = f"https://api.telegram.org/bot{TOKEN}"

def get_updates(offset=None):
    url = API + "/getUpdates?timeout=50"
    if offset: url += f"&offset={offset}"
    with urllib.request.urlopen(url, timeout=70) as r:
        return json.loads(r.read())

def handle_script(chat_id, arg):
    parts = [p.strip() for p in arg.split("|")]
    if len(parts) < 3:
        send(chat_id, "format: /script product | audience | platform\nexample: /script NOISE earbuds | young Lagos commuters | tiktok")
        return
    product, audience, platform = parts[0], parts[1], parts[2].lower()
    api("sendChatAction", chat_id=chat_id, action="typing")
    pkg = generate_script_package(product, audience, platform)
    workstore.add_package(pkg, product, audience, platform, source="telegram")
    hooks = "\n".join(f"{i+1}. {h}" for i, h in enumerate(pkg.get("hooks", [])))
    script = "\n".join(f"• {s.get('beat','')}: {s.get('visual','')} — {s.get('voiceover','')}" for s in pkg.get("script", []))
    caps = "\n\n".join(pkg.get("captions", []))
    send(chat_id, f"🎯 HOOKS\n{hooks}\n\n🎬 SCRIPT\n{script}\n\n✍️ CAPTIONS\n{caps}")


# ---------- task memory: work the user gives Royce is tracked until it is done ----------
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

def handle_image(chat_id, prompt):
    if not prompt:
        send(chat_id, "usage: /image a neon-lit earbud on a dark bronze background, product shot")
        return
    t = tasks.add_task(chat_id, prompt, status="open", check={"type": "retry", "action": "image", "prompt": prompt, "attempts": 0})
    send(chat_id, "generating that now")
    api("sendChatAction", chat_id=chat_id, action="upload_photo")
    try:
        run_task(t)
    except imagegen.ImageError as e:
        reason = str(e)
        tasks.mark_unavailable(t["id"], note=reason, check={"type": "retry", "action": "image", "prompt": prompt, "attempts": 1})
        send(chat_id, f"{reason}\nkept it on my list, i'll retry and send it when it works ✅")
        return
    tasks.complete_task(t["id"], note="done")

def handle_photo(chat_id, msg, caption):
    """Describe or analyze a photo. A caption with the photo is treated as the request ('make an ad like this')."""
    api("sendChatAction", chat_id=chat_id, action="typing")
    got = download_photo(msg)
    if not got:
        send(chat_id, "couldn't read that photo, try sending it again")
        return
    data, mime = got
    ask = caption or "describe this image in a few sentences: what's in it, how it looks, and what it could be used for in an ad."
    if caption:
        ask = (f"The user sent this photo with the request: \"{caption}\". "
               "Use the photo for it. If they want an ad, script or creative based on it, do that using what you see.")
    sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot()
    try:
        reply = chat_vision([{"role": "system", "content": sysmsg}, {"role": "user", "content": ask}], data, mime=mime)
    except Exception as e:
        print(f"[bot] vision error: {e}", flush=True)
        send(chat_id, f"couldn't look at that photo: image understanding (gemini, openrouter fallback) failed: {str(e)[:200]}")
        return
    send(chat_id, reply)

def handle_inbox_now(chat_id):
    s = collectors.get("gmail").summary(days=7)
    lines = [f"gmail: {s['unread']} unread, {s['recent']} received in the last 7 days."]
    if s["unread_senders"]:
        lines.append("unread from:")
        lines += [f"• {x['sender']} ({x['count']})" for x in s["unread_senders"]]
    send(chat_id, "\n".join(lines))

def handle_inbox(chat_id):
    if not collectors.get("gmail").configured:
        t = tasks.add_task(chat_id, "check my gmail inbox", status="unavailable",
                           check={"type": "collector", "name": "gmail", "action": "inbox"},
                           note="gmail not connected")
        send(chat_id, "gmail isn't connected yet. paste an app password on the connectors page and i'll pull your inbox. i saved this on my list and ill ping u once it's connected ✅")
        return
    api("sendChatAction", chat_id=chat_id, action="typing")
    try:
        handle_inbox_now(chat_id)
    except collectors.CollectorError as e:
        send(chat_id, str(e))

def do_chat(text, hist=None):
    sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot() + skills.skill_block(text)
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
    reply = do_chat(desc, hist)
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
    "You track tasks until completion. If something the user asked for is not available, tell them plainly and say you will notify them when it is, then actually follow up."
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

def handle_chat(chat_id, text):
    hist = CHAT_HIST.setdefault(chat_id, [])
    api("sendChatAction", chat_id=chat_id, action="typing")
    low = text.lower()
    reply = None
    if any(w in low for w in REPORT_WORDS) and any(w in low for w in ("report", "performance", "how", "pull", "show", "give", "result", "doing")):
        handle_report(chat_id, text)
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent campaign report]"}]
        del hist[:-HIST_MAX]
        return
    img = re.search(r"\b(generate|create|make|draw|design)\s+(an?\s+|the\s+|me\s+an?\s+)?(image|picture|pic|photo|visual|graphic)s?\b\s*(of|for|with|showing)?", low)
    if img and reply is None:
        prompt = re.sub(r"^.*?\b(image|picture|pic|photo|visual|graphic)s?\b\s*(of|for|with|showing)?\s*", "", text, count=1, flags=re.I).strip() or text
        handle_image(chat_id, prompt)
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[generated image]"}]
        del hist[:-HIST_MAX]
        return
    if re.search(r"\b(gmail|inbox|unread)\b", low) and re.search(r"\b(check|show|pull|summar|what|any|how|read|list|see)\w*", low):
        handle_inbox(chat_id)
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent inbox summary]"}]
        del hist[:-HIST_MAX]
        return
    if re.search(r"\b(script|scripts|hooks?|captions?|ad package)\b", low) and re.search(r"\b(make|generate|create|write|give me)\b", low):
        reply = ("send me these 3, separated by |:\nproduct | audience | platform\n\nexample:\nNOISE earbuds | young Lagos commuters | tiktok")
    parts = [p.strip() for p in text.split("|")]
    if reply is None and len(parts) == 3 and all(parts):
        # conversational ad generation: "product | audience | platform" in chat
        handle_script(chat_id, text)
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[generated ad package]"}]
        del hist[:-HIST_MAX]
        return
    if re.search(r"\b(check|pull|show|review)\b.*\bcampaigns?\b", low) or re.search(r"\bcheck my ads\b", low):
        handle_doctor(chat_id, "")
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent ad teardown]"}]
        del hist[:-HIST_MAX]
        return
    from . import doctor as _doc
    if _doc.wants_diagnose(text):
        handle_doctor(chat_id, "" if re.search(r"\b(this|my)\b.*\bcampaigns?\b", low) and not re.search(r"\d", text) else text)
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent ad teardown]"}]
        del hist[:-HIST_MAX]
        return
    cm = re.search(r"\bcontent (plan|calendar)\s+(for|of)\s+(.+)", text, re.I)
    if cm:
        handle_content_chat(chat_id, cm.group(3).strip())
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent content calendar]"}]
        del hist[:-HIST_MAX]
        return
    vm = re.search(r"\b(say (this|that|it) in voice|reply in voice|voice (note|reply)|in a voice note)\b", low)
    if vm:
        spoken = re.sub(r"\b(say (this|that|it) in voice|reply in voice|voice (note|reply)|in a voice note)\b[:,]?\s*", "", text, flags=re.I).strip()
        handle_voice(chat_id, spoken or "royce ai here, what do you need")
        hist += [{"role": "user", "content": text}, {"role": "assistant", "content": "[sent voice note]"}]
        del hist[:-HIST_MAX]
        return
    if reply is None:
        cls = classify(text)
        if cls.get("is_task") is True:
            reply = run_new_task(chat_id, text, "chat", needs=cls.get("needs"), summary=str(cls.get("summary") or ""), hist=hist)
            hist += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
            del hist[:-HIST_MAX]
            return
        sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot() + skills.skill_block(text)
        reply = chat_text([{"role": "system", "content": sysmsg}] + hist[-HIST_MAX:] + [{"role": "user", "content": text}], timeout=40)
    hist += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    del hist[:-HIST_MAX]
    send(chat_id, reply)

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


def handle_doctor(chat_id, arg):
    from . import doctor
    arg = (arg or "").strip()
    api("sendChatAction", chat_id=chat_id, action="typing")
    try:
        if not arg:
            names = collectors.configured_names()
            if not names:
                send(chat_id, "usage: /doctor <paste ad copy or campaign numbers>\nor connect an ad platform and say 'check my campaigns'.")
                return
            rows, errors = collectors.fetch_all("last_7d")
            if not rows:
                send(chat_id, "pulled from " + ", ".join(names) + " but no campaign rows came back for last_7d.")
                return
            kind, body = "campaign_numbers", rows
        else:
            kind = "campaign_numbers" if re.search(r"\d", arg) and re.search(r"\b(ctr|cpc|spend|impressions|clicks|roas)\b", arg, re.I) else "ad_copy"
            body = arg
        send(chat_id, "teardown:\n\n" + doctor.diagnose(body, kind))
    except ValueError as e:
        send(chat_id, str(e))
    except Exception as e:
        print("[bot] doctor error", e)
        send(chat_id, "couldn't get a teardown right now (the llm is busy). try again in a minute.")


def handle_content(chat_id, arg):
    from . import content
    parts = [p.strip() for p in (arg or "").split("|")]
    if len(parts) != 3 or not all(parts):
        send(chat_id, "usage: /content <client> | <industry> | <platform>\nexample: /content NOISE earbuds | consumer electronics | tiktok")
        return
    client, industry, platform = parts
    api("sendChatAction", chat_id=chat_id, action="typing")
    try:
        cal = content.weekly_calendar(client, industry, platform, source="telegram")
        send(chat_id, content.format_calendar(client, platform, cal) + "\n\nsaved to your generated work.")
    except ValueError as e:
        send(chat_id, str(e))
    except Exception as e:
        print("[bot] content error", e)
        send(chat_id, "couldn't build the calendar right now (the llm is busy). try again in a minute.")


def handle_voice(chat_id, text):
    from . import voice
    text = (text or "").strip()
    if not text:
        send(chat_id, "usage: /voice <what to say>\nexample: /voice royce is online, send me the lead list")
        return
    api("sendChatAction", chat_id=chat_id, action="record_voice")
    try:
        data = voice.tts(text)
    except voice.VoiceUnavailable as e:
        send(chat_id, str(e))
        return
    except Exception as e:
        print("[bot] voice error", e)
        send(chat_id, voice.NO_PROVIDER_MSG)
        return
    try:
        send_voice(chat_id, data)
    except Exception as e:
        print("[bot] sendVoice error", e)
        send(chat_id, "made the voice but telegram rejected the upload. try again in a minute.")


def handle_skills(chat_id):
    lines = ["marketing skills i use in answers (max 2 per reply, picked by what you ask):"]
    for s in skills.list_skills():
        lines.append(f"• {s['name']}: {s['description']}")
    lines.append("\nask about hooks, brand, posts, funnels, reports or pricing and i pull the right one.")
    send(chat_id, "\n".join(lines))


def handle_content_chat(chat_id, client):
    send(chat_id, f"for a 7-day plan for {client} i need the industry and platform too. send:\n/content {client} | <industry> | <platform>\nexample: /content {client} | skincare | instagram")


def handle_update(u):
    msg = u.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private": return
    chat_id, text = chat["id"], (msg.get("text") or msg.get("caption") or "").strip()
    if not text and not msg.get("photo"): return
    try:
        leadbrain.remember_chat(chat_id)
    except Exception as e:
        print(f"[bot] remember_chat failed: {e}", flush=True)
    try:
        if msg.get("photo"):
            handle_photo(chat_id, msg, text)
            return
        if text.startswith("/start"):
            send(chat_id, "👑 ROYCE AI online.\n\n/script product | audience | platform — full ad package\n/addlead Name | Business | Contact\n/leads — view leads\n/report — summary\n\n/tasks: what's on my list\n/skills: marketing playbooks i use\n\nor just talk to me normally.")
        elif text.startswith("/script "): handle_script(chat_id, text[8:])
        elif text.startswith("/addlead "):
            p = [x.strip() for x in text[9:].split("|")]
            lid = db.add_lead(p[0], p[1] if len(p) > 1 else "", p[2] if len(p) > 2 else "")
            send(chat_id, f"✅ lead #{lid} added: {p[0]}")
        elif text == "/leads":
            leads = db.list_leads()
            send(chat_id, "\n".join(f"#{l['id']} {l['name']} — {l['status']} (followup {l['next_followup'] or '—'})" for l in leads) or "no leads yet, /addlead Name | Business | Contact")
        elif text == "/report":
            s = db.summary()
            send(chat_id, f"📊 {s['total']} leads. {json.dumps(s['by_status'])}. {len(s['overdue'])} overdue.")
        elif text == "/tasks": handle_tasks(chat_id)
        elif text == "/followup" or text.startswith("/followup "): handle_followup(chat_id, text[9:])
        elif text == "/doctor" or text.startswith("/doctor "): handle_doctor(chat_id, text[7:])
        elif text == "/content" or text.startswith("/content "): handle_content(chat_id, text[8:])
        elif text == "/skills": handle_skills(chat_id)
        elif text == "/voice" or text.startswith("/voice "): handle_voice(chat_id, text[6:])
        elif text == "/image" or text.startswith("/image "): handle_image(chat_id, text[6:].strip())
        elif text.startswith("/"): send(chat_id, "unknown command, try /start")
        else: handle_chat(chat_id, text)
    except Exception as e:
        print(f"[bot] error handling update: {e}", flush=True)
        try: send(chat_id, "something broke on my side, try again")
        except Exception: pass

def main():
    global TOKEN, API
    get_env()
    me = api("getMe")
    print(f"[bot] connected as @{me['result']['username']}", flush=True)
    offset = None
    last_check = 0
    while True:
        try:
            data = get_updates(offset)
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            time.sleep(3)
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

if __name__ == "__main__":
    main()
