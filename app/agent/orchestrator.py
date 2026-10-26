"""Orchestrator: run a plan step by step through existing engines.

Engines are thin adapters over modules that already work (script package, ad doctor,
content calendar, lead follow-up, collectors report, image generation, tasks). Each engine
declares its required input fields and the output shape it must return. Every step is
validated before it runs and verified after. Failures retry at most MAX_RETRIES times
with exponential backoff (1s, 2s). Nothing here sends a message; send actions are routed
through permissions.py approvals instead.
"""
import time

MAX_RETRIES = 2
BACKOFF = 1.0  # seconds; attempt n waits BACKOFF * 2**(n-1)


class StepError(Exception):
    pass


def _need(inp, fields):
    missing = [f for f in fields if not str(inp.get(f, "")).strip()]
    if missing:
        raise StepError(f"missing input: {', '.join(missing)}")


def _run_llm(inp):
    from ..llm import chat_text
    return chat_text([{"role": "user", "content": inp["prompt"]}], timeout=40)


def _run_script(inp):
    from ..llm import generate_script_package
    return generate_script_package(inp["product"], inp["audience"], inp.get("platform", "instagram"))


def _run_doctor(inp):
    from ..doctor import diagnose
    return diagnose(inp["text"], inp.get("kind", "ad_copy"))


def _run_content(inp):
    from .. import content
    return content.weekly_calendar(inp["client"], inp["industry"], inp["platform"], source="agent")


def _run_followup(inp):
    from .. import leadbrain, db
    lead = leadbrain.find_lead(inp["lead"])
    if not lead:
        raise StepError(f"no lead matches '{inp['lead']}'")
    return leadbrain.draft_followup(lead)


def _run_report(inp):
    from .. import collectors
    rows, errors = collectors.fetch_all(inp.get("preset", "last_7d"))
    return {"rows": rows, "errors": errors}


def _run_image(inp):
    from .. import imagegen
    return imagegen.generate(inp["prompt"])


def _run_tasks(inp):
    from .. import tasks
    return tasks.list_tasks(chat_id=inp.get("chat_id"))


# engine -> (required fields, runner, expected shape: "text" | "dict" | "list" | "bytes")
ENGINES = {
    "llm": (["prompt"], _run_llm, "text"),
    "script": (["product", "audience"], _run_script, "dict"),
    "doctor": (["text"], _run_doctor, "text"),
    "content": (["client", "industry", "platform"], _run_content, "dict"),
    "followup": (["lead"], _run_followup, "text"),
    "report": ([], _run_report, "dict"),
    "image": (["prompt"], _run_image, "bytes"),
    "tasks": ([], _run_tasks, "list"),
}


def verify(output, shape):
    """Result verification: non-empty and the expected shape. Raises StepError."""
    if output is None:
        raise StepError("empty result")
    if shape == "text":
        if not isinstance(output, str) or not output.strip():
            raise StepError("empty text result")
    elif shape == "dict":
        if not isinstance(output, dict) or not output:
            raise StepError("expected a non-empty object")
    elif shape == "list":
        if not isinstance(output, list):
            raise StepError("expected a list")
    elif shape == "bytes":
        if not isinstance(output, (bytes, bytearray)) or len(output) == 0:
            raise StepError("expected image bytes")
    return output


def run_step(step, sleep=time.sleep):
    """Run one step with validation, verification and bounded retries. Returns the verified output."""
    eng = step.get("engine", "llm")
    if eng not in ENGINES:
        raise StepError(f"unknown engine {eng}")
    required, runner, shape = ENGINES[eng]
    inp = step.get("input") or {}
    _need(inp, required)
    last = None
    from . import budget
    for attempt in range(MAX_RETRIES + 1):
        # llm steps reserve their budget slot inside the router (per provider attempt);
        # every other engine reserves here, before each attempt, retries included.
        if eng != "llm":
            budget.consume()
        try:
            out = runner(inp)
            return verify(out, shape)
        except (StepError, budget.BudgetExceeded):
            raise  # bad input, a deterministic refusal, or an exhausted budget: do not retry
        except Exception as e:
            last = e
            if attempt < MAX_RETRIES:
                sleep(BACKOFF * (2 ** attempt))
    raise StepError(f"{eng} failed after {MAX_RETRIES + 1} attempts: {str(last)[:200]}")


def run_plan(steps, sleep=time.sleep):
    """Run steps in order. Stops at the first failure. Returns {ok, results, error}."""
    results = []
    for i, step in enumerate(steps or []):
        try:
            results.append({"step": i, "engine": step.get("engine"), "output": run_step(step, sleep=sleep)})
        except StepError as e:
            return {"ok": False, "results": results, "error": f"step {i + 1} ({step.get('engine')}): {e}"}
    return {"ok": True, "results": results, "error": None}
