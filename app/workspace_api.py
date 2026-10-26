"""Workspace API: web chat, conversations, planner tasks, approvals, memory, provider status.

Every route here is mounted under /api/ws. OPEN ACCESS: there is no token gate. Anyone who can reach
the URL can use the AI and read CRM and memory. See README and DEPLOY-WEB.md for re-enabling options.

Reuses the same modules the Telegram bot uses: chat_text, chatplan, approvals, memory, permissions,
budget and audit. The web chat binds one shared budget bucket ("web"), so the hourly cap is shared
by all web conversations and cannot be multiplied by creating new chats.
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from . import conversations
from .agent import approvals, audit, budget, chatplan, memory

router = APIRouter(prefix="/api/ws")
WEB_BUCKET = "web"
MAX_MESSAGE = 4000


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    conversation_id: str | None = None
    bot_id: str | None = Field(default=None, max_length=40)


def _llm_chat(msgs, **kw):
    from .llm import chat_text
    return chat_text(msgs, **kw)


def memory_block(text, chat_id):
    try:
        return memory.block(text, chat_id=chat_id)
    except Exception:
        return ""


def _system_prompt(text):
    from .telegram_bot import SYSTEM_PROMPT, crm_snapshot
    from . import skills
    mem = memory_block(text, WEB_BUCKET)
    return SYSTEM_PROMPT + "\n\n" + crm_snapshot() + ("\n\n" + mem if mem else "") + skills.skill_block(text) + skills.coding_block(text)


def _direct_reply(text, history):
    msgs = [{"role": "system", "content": _system_prompt(text)}] + history[-20:] + [{"role": "user", "content": text}]
    return _llm_chat(msgs, timeout=40)


def _sanitize(t):
    from .style import sanitize
    return sanitize(t)


@router.post("/chat")
def chat(body: ChatIn):
    """Send one message. Multi-step asks run through the planner; everything else is a direct reply.

    Returns {conversation_id, reply, task_id, status}. A budget or provider failure returns an error and
    stores nothing, so a retry from the UI cannot duplicate the user's message.
    """
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "empty message")
    history = conversations.history(body.conversation_id)
    bot = _active_bot(body.bot_id)
    if bot is not None:
        return _bot_chat(body, text, history, bot)
    token = budget.bind(WEB_BUCKET)
    try:
        task_id = None
        if chatplan.looks_multi_step(text):
            task_id, reason = chatplan.start(WEB_BUCKET, text, chat_fn=lambda m: _llm_chat(m, temperature=0, timeout=20))
            if task_id is None:
                reply = _sanitize("i couldn't put that into safe steps: " + reason + ". try asking one step at a time.")
                status = "rejected"
            else:
                rec = chatplan.run(task_id)
                reply = _sanitize(chatplan.summary(rec))
                status = rec.get("status")
        else:
            reply = _sanitize(_direct_reply(text, history))
            status = "completed"
    except budget.BudgetExceeded as e:
        raise HTTPException(429, {"error": "budget", "message": str(e), "retry_after": e.retry_after})
    except RuntimeError as e:
        # router messages: no provider, all rejected, cooling down, all failed. Surfaced as-is, nothing stored.
        raise HTTPException(503, {"error": "provider", "message": str(e)[:300]})
    finally:
        budget.unbind(token)
    conv = conversations.append_exchange(body.conversation_id, text, reply, task_id=task_id)
    return {"conversation_id": conv["id"], "reply": reply, "task_id": task_id, "status": status}


def _active_bot(bot_id):
    """The bot the owner picked for this message, or None for the plain Royce chat. Unknown ids are a 404."""
    if not bot_id:
        return None
    from . import bots
    bot = bots.get(bot_id)
    if bot is None:
        raise HTTPException(404, "no such bot")
    return bot


def _bot_chat(body, text, history, bot):
    """Chat through a bot. The Chief of Staff routes to specialists; a specialist answers with its own playbook."""
    from . import bots, chief
    from .llm import chat_text
    token = budget.bind(WEB_BUCKET)
    try:
        if bots.is_coordinator(bot):
            res = chief.run(text, history, bot, chat=lambda m, **kw: chat_text(m, **kw), sanitize=_sanitize)
            reply, status, delegations = res["reply"], res["status"], res["delegations"]
        else:
            reply = _sanitize(chief._direct(bot, text, history, lambda m, **kw: chat_text(m, **kw)))
            status, delegations = "completed", []
    except budget.BudgetExceeded as e:
        raise HTTPException(429, {"error": "budget", "message": str(e), "retry_after": e.retry_after})
    except RuntimeError as e:
        raise HTTPException(503, {"error": "provider", "message": str(e)[:300]})
    finally:
        budget.unbind(token)
    meta = {"bot_id": bot["id"], "bot_name": bot["name"], "delegations": delegations} if delegations else {"bot_id": bot["id"], "bot_name": bot["name"]}
    conv = conversations.append_exchange(body.conversation_id, text, reply, meta=meta, bot_id=bot["id"])
    return {"conversation_id": conv["id"], "reply": reply, "task_id": None, "status": status,
            "bot": {"id": bot["id"], "name": bot["name"]}, "delegations": delegations}


@router.get("/conversations")
def list_conversations(q: str | None = Query(default=None, max_length=200), bot_id: str | None = Query(default=None, max_length=40)):
    return conversations.list_all(q, bot_id=bot_id)


@router.get("/conversations/{cid}")
def get_conversation(cid: str):
    conv = conversations.get(cid)
    if not conv:
        raise HTTPException(404, "no such conversation")
    return conv


@router.get("/tasks")
def list_tasks():
    return [_task_view(rec) for rec in chatplan.all_plans() if rec.get("chat_id") == WEB_BUCKET]


@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    rec = chatplan.get(task_id)
    if not rec or rec.get("chat_id") != WEB_BUCKET:
        raise HTTPException(404, "no such task")
    return _task_view(rec)


def _task_view(rec):
    steps = []
    current = None
    for s in rec.get("steps", []):
        steps.append({"n": s.get("n"), "engine": s.get("engine"), "status": s.get("status"),
                      "error": s.get("error"), "approval_id": s.get("approval_id")})
        if current is None and s.get("status") in (chatplan.PENDING, chatplan.RUNNING, chatplan.WAITING):
            current = s.get("n")
    done = sum(1 for s in rec.get("steps", []) if s.get("status") == chatplan.DONE)
    return {"id": rec.get("id"), "status": rec.get("status"), "goal": rec.get("goal"),
            "created_at": rec.get("created_at"), "steps": steps, "current_step": current,
            "steps_done": done, "steps_total": len(steps),
            "summary": chatplan.summary(rec) if rec.get("steps") else ""}


@router.get("/approvals")
def list_approvals():
    out = []
    for r in approvals.all_records():
        out.append({"id": r.get("id"), "action_class": r.get("action_class"), "status": r.get("status"),
                    "summary": r.get("summary", ""), "created_at": r.get("created_at"),
                    "result": r.get("result"), "task_id": r.get("task_id")})
    return out


@router.post("/approvals/{rid}/approve")
def approve(rid: str):
    """Approve a pending action. Checks the record exists and is pending; the approvals store and the
    planner enforce the rest (chat ownership, single execution)."""
    rec = approvals.get(rid)
    if not rec:
        raise HTTPException(404, "no such approval")
    if rec.get("status") != approvals.PENDING:
        raise HTTPException(409, f"approval is already {rec.get('status')}")
    task_id = chatplan.task_for_approval(rid, rec.get("chat_id"))
    if task_id:
        rec_task = chatplan.resolve_approval(task_id, approved=True)
        audit.record("owner", "web_approval_granted", rec.get("action_class", ""), "ok")
        return _task_view(rec_task) if rec_task else {"ok": True}
    ok, msg = approvals.approve(rec.get("chat_id"), rid)
    audit.record("owner", "web_approval_granted", rec.get("action_class", ""), "ok" if ok else "failed")
    return {"ok": ok, "message": msg}


@router.post("/approvals/{rid}/reject")
def reject(rid: str):
    rec = approvals.get(rid)
    if not rec:
        raise HTTPException(404, "no such approval")
    if rec.get("status") != approvals.PENDING:
        raise HTTPException(409, f"approval is already {rec.get('status')}")
    task_id = chatplan.task_for_approval(rid, rec.get("chat_id"))
    if task_id:
        rec_task = chatplan.resolve_approval(task_id, approved=False)
        audit.record("owner", "web_approval_denied", rec.get("action_class", ""), "ok")
        return _task_view(rec_task) if rec_task else {"ok": True}
    ok = approvals.reject(rec.get("chat_id"), rid)
    audit.record("owner", "web_approval_denied", rec.get("action_class", ""), "ok" if ok else "failed")
    return {"ok": bool(ok)}


@router.get("/memory")
def list_memory(q: str | None = Query(default=None, max_length=200)):
    items = memory.all_items()
    needle = (q or "").strip().lower()
    if needle:
        items = [m for m in items if needle in (str(m.get("key", "")) + " " + str(m.get("text", ""))).lower()]
    return [{"key": m.get("key"), "text": m.get("text"), "scope": m.get("scope"), "created_at": m.get("created_at")}
            for m in items]


@router.delete("/memory/{key}")
def forget_memory(key: str):
    if not memory.forget(key):
        raise HTTPException(404, "no such memory entry")
    audit.record("owner", "memory_forgotten", "memory", "ok")
    return {"ok": True}


@router.get("/status")
def status():
    from .config import provider_status
    from .agent import validate, jobs
    from . import collectors
    providers = {}
    for pid, info in provider_status().items():
        if pid == "default":
            continue
        providers[pid] = {"configured": info.get("configured"), "model": info.get("model")}
    try:
        checks = validate.check_all()
    except Exception:
        checks = {}
    for pid, res in checks.items():
        if pid in providers:
            providers[pid]["state"] = res.get("state")
            providers[pid]["state_text"] = validate.STATE_TEXT.get(res.get("state"), "")
    from .agent import router
    for pid in providers:
        ms = router.model_states(pid)
        if ms:
            providers[pid]["models"] = ms  # real cascade order + router health per model (gemini only)
    cs = collectors.status()
    return {"providers": providers, "default_provider": provider_status().get("default"),
            "budget": {"used_this_hour": budget.used(WEB_BUCKET), "remaining_this_hour": budget.remaining(WEB_BUCKET)},
            "collectors": {k: {"label": v.get("label"), "configured": bool(v.get("configured"))}
                           for k, v in cs.items()}}


# --- bots, routines (added with the workspace rebuild) ----------------------------------------

class BotIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    template: str | None = Field(default=None, max_length=60)


@router.get("/bots/templates")
def bot_templates():
    from . import bots
    return {"templates": bots.templates(), "categories": bots.CATEGORIES}


@router.get("/bots")
def list_bots():
    from . import bots
    bots.ensure_coordinator()  # seeds the Chief of Staff once, idempotent
    return {"bots": bots.list_bots()}


@router.get("/bots/{bot_id}/persona")
def bot_persona(bot_id: str):
    """The prompt text this bot chats with: its name plus the pinned playbook body, read live."""
    from . import bots
    bot = bots.get(bot_id)
    if not bot:
        raise HTTPException(404, "no such bot")
    return {"bot_id": bot["id"], "name": bot["name"], "template": bot.get("template"),
            "persona": bots.persona_block(bot)}


@router.get("/bots/chief")
def chief_bot():
    """The Chief of Staff. Created on first call if missing, never duplicated."""
    from . import bots
    bot = bots.ensure_coordinator()
    return bot


@router.post("/bots")
def create_bot(body: BotIn):
    from . import bots
    try:
        bot = bots.create(body.name, body.template)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("owner", "bot_created", bot["id"], "ok")
    return bot


@router.delete("/bots/{bot_id}")
def delete_bot(bot_id: str):
    from . import bots
    try:
        if not bots.delete(bot_id):
            raise HTTPException(404, "no such bot")
    except ValueError as e:
        raise HTTPException(409, str(e))
    audit.record("owner", "bot_deleted", bot_id, "ok")
    return {"ok": True}


class RoutineIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    schedule: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


@router.get("/routines")
def list_routines():
    from . import routines
    return {"system": routines.system_jobs(), "user": routines.list_user()}


@router.post("/routines")
def create_routine(body: RoutineIn):
    from . import routines
    try:
        rec = routines.create(body.name, body.schedule, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("owner", "routine_created", rec["id"], "ok")
    return rec


@router.delete("/routines/{rid}")
def delete_routine(rid: str):
    from . import routines
    if not routines.delete(rid):
        raise HTTPException(404, "no such routine")
    audit.record("owner", "routine_deleted", rid, "ok")
    return {"ok": True}


# --- library: tools, skills, messaging, connectors, work, settings (owner-gated) -------------------

class ToolToggleIn(BaseModel):
    enabled: bool


@router.get("/tools")
def list_tools():
    from .agent import tools
    return {"tools": tools.describe()}


@router.put("/tools/{name}")
def toggle_tool(name: str, body: ToolToggleIn):
    from .agent import tools
    try:
        on = tools.set_enabled(name, body.enabled)
    except tools.ToolError:
        raise HTTPException(404, "no such tool")
    audit.record("owner", "tool_toggled", f"{name}={'on' if on else 'off'}", "ok")
    return {"name": name, "enabled": on}


@router.get("/skills")
def list_skills_route():
    from . import skills
    return {"skills": skills.list_skills()}


class SkillIn(BaseModel):
    body: str = Field(min_length=1, max_length=20000)
    description: str = Field(default="", max_length=300)


@router.get("/skills/{name}")
def get_skill_route(name: str):
    from . import skills
    body = skills.get_skill(name)
    if body is None:
        raise HTTPException(404, "no such skill")
    meta = next((s for s in skills.list_skills() if s["name"] == name), {"name": name, "description": ""})
    return {"name": name, "description": meta.get("description", ""), "body": body}


@router.put("/skills/{name}")
def save_skill_route(name: str, body: SkillIn):
    from . import skills
    try:
        saved = skills.save_skill(name, body.body, body.description)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("owner", "skill_saved", saved, "ok")
    return {"name": saved}


@router.get("/messaging")
def list_messaging():
    from . import tgstore, tgprecautions
    chats = []
    for gid, title, ctype in tgstore.known_chats():
        chats.append({"group_id": str(gid), "title": title, "type": ctype,
                      "messages": tgstore.count(gid),
                      "precautions_on": tgprecautions.is_on(gid)})
    return {"chats": chats}


class MessagingToggleIn(BaseModel):
    precautions_on: bool


@router.put("/messaging/{group_id}")
def toggle_messaging(group_id: str, body: MessagingToggleIn):
    from . import tgstore, tgprecautions
    known = {str(g) for g, _t, _c in tgstore.known_chats()}
    if group_id not in known:
        raise HTTPException(404, "no such chat")
    on = tgprecautions.set_on(group_id, body.precautions_on)
    audit.record("owner", "precautions_toggled", f"{group_id}={'on' if on else 'off'}", "ok")
    return {"group_id": group_id, "precautions_on": on}


@router.get("/connectors")
def list_connectors():
    from . import collectors
    out = []
    for name, row in collectors.status().items():
        out.append({"name": name, "label": row.get("label"), "configured": bool(row.get("configured")),
                    "account_label": row.get("account_label") or "", "is_ads": bool(row.get("is_ads"))})
    return {"connectors": out}


@router.get("/work")
def list_work_route():
    from . import workstore
    return {"items": workstore.list_work()}


@router.get("/usage")
def usage():
    return {"bucket": WEB_BUCKET, "used_this_hour": budget.used(WEB_BUCKET),
            "remaining_this_hour": budget.remaining(WEB_BUCKET)}


class DefaultProviderIn(BaseModel):
    provider: str = Field(min_length=1, max_length=20)


@router.put("/settings/default-provider")
def set_default_provider(body: DefaultProviderIn):
    from .config import PROVIDERS, load_settings, save_settings
    if body.provider not in PROVIDERS:
        raise HTTPException(400, "unknown provider")
    s = load_settings()
    s["default_provider"] = body.provider
    save_settings(s)
    audit.record("owner", "default_provider_set", body.provider, "ok")
    return {"default_provider": body.provider}
