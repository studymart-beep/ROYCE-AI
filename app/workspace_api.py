"""Workspace API: web chat, conversations, planner tasks, approvals, memory, provider status.

Every route here is mounted under /api/ws and gated by the owner token (WORKSPACE_TOKEN in .env).
The API is reachable through the public tunnel, so a missing or wrong token gets 401, and an unset
WORKSPACE_TOKEN makes every route return 503 (fail closed, never open).

Reuses the same modules the Telegram bot uses: chat_text, chatplan, approvals, memory, permissions,
budget and audit. The web chat binds one shared budget bucket ("web"), so the hourly cap is shared
by all web conversations and cannot be multiplied by creating new chats.
"""
import hmac, os
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field

from . import conversations
from .agent import approvals, audit, budget, chatplan, memory

router = APIRouter(prefix="/api/ws")
WEB_BUCKET = "web"
MAX_MESSAGE = 4000


def require_owner(authorization: str = Header(default=""), x_workspace_token: str = Header(default="")):
    expected = os.environ.get("WORKSPACE_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "workspace is locked: WORKSPACE_TOKEN is not set on the server")
    supplied = x_workspace_token.strip()
    if not supplied and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "owner token required")
    return True


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=MAX_MESSAGE)
    conversation_id: str | None = None


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


@router.post("/chat", dependencies=[Depends(require_owner)])
def chat(body: ChatIn):
    """Send one message. Multi-step asks run through the planner; everything else is a direct reply.

    Returns {conversation_id, reply, task_id, status}. A budget or provider failure returns an error and
    stores nothing, so a retry from the UI cannot duplicate the user's message.
    """
    text = body.message.strip()
    if not text:
        raise HTTPException(400, "empty message")
    history = conversations.history(body.conversation_id)
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


@router.get("/conversations", dependencies=[Depends(require_owner)])
def list_conversations(q: str | None = Query(default=None, max_length=200)):
    return conversations.list_all(q)


@router.get("/conversations/{cid}", dependencies=[Depends(require_owner)])
def get_conversation(cid: str):
    conv = conversations.get(cid)
    if not conv:
        raise HTTPException(404, "no such conversation")
    return conv


@router.get("/tasks", dependencies=[Depends(require_owner)])
def list_tasks():
    return [_task_view(rec) for rec in chatplan.all_plans() if rec.get("chat_id") == WEB_BUCKET]


@router.get("/tasks/{task_id}", dependencies=[Depends(require_owner)])
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


@router.get("/approvals", dependencies=[Depends(require_owner)])
def list_approvals():
    out = []
    for r in approvals.all_records():
        out.append({"id": r.get("id"), "action_class": r.get("action_class"), "status": r.get("status"),
                    "summary": r.get("summary", ""), "created_at": r.get("created_at"),
                    "result": r.get("result"), "task_id": r.get("task_id")})
    return out


@router.post("/approvals/{rid}/approve", dependencies=[Depends(require_owner)])
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


@router.post("/approvals/{rid}/reject", dependencies=[Depends(require_owner)])
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


@router.get("/memory", dependencies=[Depends(require_owner)])
def list_memory(q: str | None = Query(default=None, max_length=200)):
    items = memory.all_items()
    needle = (q or "").strip().lower()
    if needle:
        items = [m for m in items if needle in (str(m.get("key", "")) + " " + str(m.get("text", ""))).lower()]
    return [{"key": m.get("key"), "text": m.get("text"), "scope": m.get("scope"), "created_at": m.get("created_at")}
            for m in items]


@router.delete("/memory/{key}", dependencies=[Depends(require_owner)])
def forget_memory(key: str):
    if not memory.forget(key):
        raise HTTPException(404, "no such memory entry")
    audit.record("owner", "memory_forgotten", "memory", "ok")
    return {"ok": True}


@router.get("/status", dependencies=[Depends(require_owner)])
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
    cs = collectors.status()
    return {"providers": providers, "default_provider": provider_status().get("default"),
            "budget": {"used_this_hour": budget.used(WEB_BUCKET), "remaining_this_hour": budget.remaining(WEB_BUCKET)},
            "collectors": {k: {"label": v.get("label"), "configured": bool(v.get("configured"))}
                           for k, v in cs.items()}}


# --- bots, routines (added with the workspace rebuild) ----------------------------------------

class BotIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    template: str | None = Field(default=None, max_length=60)


@router.get("/bots/templates", dependencies=[Depends(require_owner)])
def bot_templates():
    from . import bots
    return {"templates": bots.templates(), "categories": bots.CATEGORIES}


@router.get("/bots", dependencies=[Depends(require_owner)])
def list_bots():
    from . import bots
    return {"bots": bots.list_bots()}


@router.post("/bots", dependencies=[Depends(require_owner)])
def create_bot(body: BotIn):
    from . import bots
    try:
        bot = bots.create(body.name, body.template)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("owner", "bot_created", bot["id"], "ok")
    return bot


@router.delete("/bots/{bot_id}", dependencies=[Depends(require_owner)])
def delete_bot(bot_id: str):
    from . import bots
    if not bots.delete(bot_id):
        raise HTTPException(404, "no such bot")
    audit.record("owner", "bot_deleted", bot_id, "ok")
    return {"ok": True}


class RoutineIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    schedule: str = Field(default="", max_length=120)
    note: str = Field(default="", max_length=500)


@router.get("/routines", dependencies=[Depends(require_owner)])
def list_routines():
    from . import routines
    return {"system": routines.system_jobs(), "user": routines.list_user()}


@router.post("/routines", dependencies=[Depends(require_owner)])
def create_routine(body: RoutineIn):
    from . import routines
    try:
        rec = routines.create(body.name, body.schedule, body.note)
    except ValueError as e:
        raise HTTPException(400, str(e))
    audit.record("owner", "routine_created", rec["id"], "ok")
    return rec


@router.delete("/routines/{rid}", dependencies=[Depends(require_owner)])
def delete_routine(rid: str):
    from . import routines
    if not routines.delete(rid):
        raise HTTPException(404, "no such routine")
    audit.record("owner", "routine_deleted", rid, "ok")
    return {"ok": True}
