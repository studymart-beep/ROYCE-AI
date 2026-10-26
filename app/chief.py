"""Chief of Staff: the coordinator bot that routes a request to specialist bots and reports back.

When the active bot is the Chief of Staff, one chat message goes like this:
  1. Routing call (chief persona). The model must return JSON:
       {"bot": "<specialist id> or self", "instruction": "<refined task>", "needs_approval": true|false}
     or {"steps": [ ...up to 3 of those... ]} for a plainly multi-specialist job.
  2. Malformed or missing JSON: the chief answers the message directly (no delegation). Audited as a fallback.
  3. "self": the chief answers directly with its own persona.
  4. A specialist step: the specialist's own persona (its skill playbook) is the system prompt, the refined
     instruction is the user message, and the call goes through the normal provider router and the shared
     hourly budget. Each step's output feeds the next step's instruction.
  5. needs_approval: nothing runs. The planned steps are returned for the owner to confirm.

Limits: at most 3 steps in a plan, and at most MAX_HOPS (2) specialist calls per request. A specialist can
never route further (no recursion, no loops), a bot id may appear at most once in a plan, and the chief
is never a specialist. Each specialist call is audited as bot ids only (no task or reply text).

The final reply is deterministic: "here's what <bot> came back with:" followed by the specialist's text,
so the specialist's content is not paraphrased away. Every outgoing text is sanitized.
"""
import json

from . import bots
from .agent import audit

MAX_STEPS = 3
MAX_HOPS = 2
MAX_INSTRUCTION = 1500
PRIOR_CLIP = 3000
ROUTE_TEMPERATURE = 0.2
SPECIALIST_TIMEOUT = 40
SELF = "self"


class Routing:
    """Parsed routing decision. steps: list of {bot, instruction, needs_approval}."""

    def __init__(self, steps):
        self.steps = steps

    @property
    def needs_approval(self):
        return any(s["needs_approval"] for s in self.steps)

    @property
    def is_self(self):
        return len(self.steps) == 1 and self.steps[0]["bot"] == SELF


def _valid_bot(bot_id, specialist_ids):
    return bot_id == SELF or bot_id in specialist_ids


def _extract_json(raw):
    """First JSON object in the model text, tolerating code fences and surrounding prose."""
    if not isinstance(raw, str):
        return None
    text = raw.strip().replace("```json", "```")
    dec = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = dec.raw_decode(text, start)
            if isinstance(obj, dict):
                return obj
        except ValueError:
            pass
        start = text.find("{", start + 1)
    return None


def _one_step(obj, specialist_ids):
    if not isinstance(obj, dict):
        return None
    bot_id = obj.get("bot")
    instruction = obj.get("instruction")
    if not isinstance(bot_id, str) or not _valid_bot(bot_id.strip(), specialist_ids):
        return None
    if not isinstance(instruction, str) or not instruction.strip():
        return None
    if "needs_approval" in obj and not isinstance(obj["needs_approval"], bool):
        return None
    return {
        "bot": bot_id.strip(),
        "instruction": " ".join(instruction.split())[:MAX_INSTRUCTION],
        # a missing flag is treated as needs approval (safe default); an explicit false runs the step
        "needs_approval": obj.get("needs_approval", True),
    }


def parse_routing(raw, specialist_ids):
    """Return a Routing, or None when the output is malformed (caller falls back to a direct answer)."""
    obj = _extract_json(raw)
    if obj is None:
        return None
    if "steps" in obj:
        steps_raw = obj.get("steps")
        if not isinstance(steps_raw, list) or not (1 <= len(steps_raw) <= MAX_STEPS):
            return None
        steps = [_one_step(s, specialist_ids) for s in steps_raw]
    else:
        steps = [_one_step(obj, specialist_ids)]
    if not steps or any(s is None for s in steps):
        return None
    ids = [s["bot"] for s in steps]
    if len(set(ids)) != len(ids):  # no loops: a bot may appear once per plan
        return None
    if SELF in ids and len(steps) > 1:
        return None
    return Routing(steps)


def specialists():
    """Every bot the chief may delegate to: all bots except the chief itself."""
    return [b for b in bots.list_bots() if not bots.is_coordinator(b)]


def _route_prompt(chief, specs):
    lines = [bots.persona_block(chief), "", "Specialist bots you can delegate to (id, name, playbook):"]
    for b in specs:
        lines.append(f"- id {b['id']}: {b['name']} ({b.get('template') or 'custom persona'})")
    if not specs:
        lines.append("- none yet. answer directly with bot \"self\" and say a specialist is needed.")
    lines += [
        "",
        "Reply with ONLY one JSON object, no prose:",
        '{"bot": "<specialist id or self>", "instruction": "<refined task with product, audience, platform, numbers>",'
        ' "needs_approval": true or false}',
        'For a job that needs two or three specialists in order, reply {"steps": [ one such object per step ]}.',
        "Use needs_approval true for anything that sends, posts, spends money or changes live data.",
        "If the request is ambiguous or missing a key detail, use bot \"self\" and make the instruction one short "
        "clarifying question.",
        "A chief never routes to a chief.",
    ]
    return "\n".join(lines)


def _history_text(history):
    rows = []
    for m in (history or [])[-6:]:
        rows.append(f"{m.get('role', 'user')}: {str(m.get('content', ''))[:400]}")
    return "\n".join(rows)


def _direct(persona_bot, message, history, chat):
    from .style import VOICE_RULES
    system = bots.persona_block(persona_bot) + "\n\n" + VOICE_RULES
    msgs = [{"role": "system", "content": system}] + (history or [])[-20:] + [{"role": "user", "content": message}]
    return chat(msgs, timeout=40)


def _specialist_call(spec, instruction, chat):
    from .style import VOICE_RULES
    system = (bots.persona_block(spec) + "\n\nYou are doing this job for the Chief of Staff. Do the work directly, "
              "no preamble, no routing, no talk about who asked.\n\n" + VOICE_RULES)
    msgs = [{"role": "system", "content": system}, {"role": "user", "content": instruction}]
    return chat(msgs, timeout=SPECIALIST_TIMEOUT)


def _delegation_audit(chief, spec, outcome):
    audit.record("chief", "delegated", f"{chief['id']}->{spec['id']}", outcome)


def run(message, history, chief, chat, sanitize):
    """Handle one chat message for the Chief of Staff. Returns a dict with reply, status and delegations.

    chat(msgs, **kw) is the llm call (the real one is llm.chat_text, which goes through the router and the
    shared budget). BudgetExceeded and RuntimeError from it propagate so the endpoint returns its usual error
    and nothing is stored.
    """
    specs = specialists()
    by_id = {b["id"]: b for b in specs}
    route_msgs = [{"role": "system", "content": _route_prompt(chief, specs)}]
    if history:
        route_msgs.append({"role": "system", "content": "Recent conversation:\n" + _history_text(history)})
    route_msgs.append({"role": "user", "content": message})
    raw = chat(route_msgs, temperature=ROUTE_TEMPERATURE, timeout=20)
    plan = parse_routing(raw, set(by_id))
    if plan is None:
        audit.record("chief", "routing_fallback", chief["id"], "malformed")
        return {"reply": sanitize(_direct(chief, message, history, chat)), "status": "completed", "delegations": []}
    if plan.is_self:
        return {"reply": sanitize(_direct(chief, message, history, chat)), "status": "completed", "delegations": []}
    if plan.needs_approval:
        lines = ["this needs your ok before anything runs. planned steps:"]
        for i, s in enumerate(plan.steps, 1):
            name = by_id[s["bot"]]["name"] if s["bot"] in by_id else "chief"
            lines.append(f"{i}. {name}: {s['instruction']}")
        lines.append("tell me to go ahead and i'll run it.")
        audit.record("chief", "approval_pending", chief["id"], f"steps={len(plan.steps)}")
        return {"reply": sanitize("\n".join(lines)), "status": "awaiting_approval", "delegations": [
            {"bot_id": s["bot"], "bot_name": by_id[s["bot"]]["name"], "status": "awaiting_approval"}
            for s in plan.steps]}

    delegations, sections, prior = [], [], ""
    for i, s in enumerate(plan.steps):
        if i >= MAX_HOPS:
            delegations.append({"bot_id": s["bot"], "bot_name": by_id[s["bot"]]["name"], "status": "skipped_hop_limit"})
            continue
        spec = by_id[s["bot"]]
        instruction = s["instruction"]
        if prior:
            instruction = "Earlier step output:\n" + prior[:PRIOR_CLIP] + "\n\nYour task: " + instruction
        try:
            out = _specialist_call(spec, instruction, chat)
        except (RuntimeError, OSError, ValueError) as e:  # budget propagates (BudgetExceeded is not caught here)
            _delegation_audit(chief, spec, "failed")
            delegations.append({"bot_id": spec["id"], "bot_name": spec["name"], "status": "failed"})
            sections.append(f"{spec['name']} couldn't finish this step: {str(e)[:160]}")
            break
        _delegation_audit(chief, spec, "ok")
        delegations.append({"bot_id": spec["id"], "bot_name": spec["name"], "status": "completed"})
        sections.append(f"here's what {spec['name']} came back with:\n\n{sanitize(out)}")
        prior = out
    status = "completed" if all(d["status"] == "completed" for d in delegations) else "partial"
    return {"reply": "\n\n".join(sections), "status": status, "delegations": delegations}
