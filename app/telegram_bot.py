import json, os, re, time, urllib.request, urllib.parse
from .llm import chat_text, generate_script_package
from . import db

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
    hooks = "\n".join(f"{i+1}. {h}" for i, h in enumerate(pkg.get("hooks", [])))
    script = "\n".join(f"• {s.get('beat','')}: {s.get('visual','')} — {s.get('voiceover','')}" for s in pkg.get("script", []))
    caps = "\n\n".join(pkg.get("captions", []))
    send(chat_id, f"🎯 HOOKS\n{hooks}\n\n🎬 SCRIPT\n{script}\n\n✍️ CAPTIONS\n{caps}")

SYSTEM_PROMPT = (
    "You are Royce AI, the in-house AI for Royce Marketing Agency, a Nigerian ad agency in Lagos. "
    "You speak like a sharp, friendly colleague: concise, confident, no corporate fluff, light Nigerian flavor is fine. "
    "You know the agency runs Meta, TikTok and Google ad campaigns for clients and maintains a CRM of leads. "
    "Never introduce or re-introduce yourself unless explicitly asked who you are. "
    "Answer the actual question directly."
)
HIST_MAX = 20

def crm_snapshot():
    s = db.summary()
    st = ", ".join(f"{k} {v}" for k, v in sorted(s["by_status"].items())) or "none yet"
    return f"Live CRM snapshot: {s['total']} leads total. By status: {st}. {len(s['overdue'])} overdue follow-ups."

def handle_chat(chat_id, text):
    hist = CHAT_HIST.setdefault(chat_id, [])
    api("sendChatAction", chat_id=chat_id, action="typing")
    low = text.lower()
    reply = None
    if re.search(r"\b(script|scripts|hooks?|captions?|ad package)\b", low) and re.search(r"\b(make|generate|create|write|give me)\b", low):
        reply = ("Lets do it properly. Give me these 3, separated by |:\nproduct | audience | platform\n\nexample:\nNOISE earbuds | young Lagos commuters | tiktok")
    if reply is None:
        sysmsg = SYSTEM_PROMPT + "\n\n" + crm_snapshot()
        reply = chat_text([{"role": "system", "content": sysmsg}] + hist[-HIST_MAX:] + [{"role": "user", "content": text}], timeout=40)
    hist += [{"role": "user", "content": text}, {"role": "assistant", "content": reply}]
    del hist[:-HIST_MAX]
    send(chat_id, reply)

def handle_update(u):
    msg = u.get("message") or {}
    chat = msg.get("chat") or {}
    if chat.get("type") != "private": return
    chat_id, text = chat["id"], (msg.get("text") or "").strip()
    if not text: return
    try:
        if text.startswith("/start"):
            send(chat_id, "👑 ROYCE AI online.\n\n/script product | audience | platform — full ad package\n/addlead Name | Business | Contact\n/leads — view leads\n/report — summary\n\nor just talk to me normally.")
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
    while True:
        try:
            data = get_updates(offset)
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                handle_update(u)
        except Exception as e:
            print(f"[bot] poll error: {e}", flush=True)
            time.sleep(3)

if __name__ == "__main__":
    main()
