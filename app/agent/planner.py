"""Planner: turn one user request into 1 to 5 steps.

Each step is either an engine call (engine name from orchestrator.ENGINES) or a plain
llm step. The LLM is asked for strict JSON. Any error (no key, bad JSON, empty, too many
steps, unknown engine) falls back to a single plain llm step, so a plan is never empty.
"""
import json, re
from . import orchestrator

MAX_STEPS = 5
PLAN_SYSTEM = (
    "You plan work for Royce AI, an ad agency assistant. Break the request into 1 to 5 steps. "
    "Each step is an object: {\"engine\": one of " + ", ".join(sorted(orchestrator.ENGINES)) + " or \"llm\", "
    "\"input\": object with the fields that engine needs, or {\"prompt\": \"...\"} for llm}. "
    "Use an engine only when it clearly fits; otherwise use llm. Reply with STRICT JSON only: {\"steps\": [...]}."
)


def single_step(request):
    return [{"engine": "llm", "input": {"prompt": request}}]


def _parse(raw):
    t = (raw or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t, flags=re.I).strip()
    m = re.search(r"\{.*\}", t, re.S)
    data = json.loads(m.group(0) if m else t)
    steps = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(steps, list) or not (1 <= len(steps) <= MAX_STEPS):
        raise ValueError("plan must have 1 to 5 steps")
    clean = []
    for s in steps:
        if not isinstance(s, dict):
            raise ValueError("step must be an object")
        eng = s.get("engine") or "llm"
        if eng != "llm" and eng not in orchestrator.ENGINES:
            raise ValueError(f"unknown engine {eng}")
        inp = s.get("input") if isinstance(s.get("input"), dict) else {}
        clean.append({"engine": eng, "input": inp})
    return clean


def plan(request, chat_fn=None):
    """Return a list of steps. chat_fn(messages) -> text is injectable for tests; defaults to llm.chat_text."""
    request = (request or "").strip()
    if not request:
        return single_step("")
    try:
        if chat_fn is None:
            from ..llm import chat_text
            chat_fn = lambda msgs: chat_text(msgs, temperature=0, timeout=20)
        raw = chat_fn([{"role": "system", "content": PLAN_SYSTEM}, {"role": "user", "content": request}])
        return _parse(raw)
    except Exception as e:
        print(f"[planner] fell back to single step: {str(e)[:120]}", flush=True)
        return single_step(request)
