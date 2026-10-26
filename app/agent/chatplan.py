"""Multi-step chat tasks: heuristic routing, validated plans, step-by-step execution.

Flow (wired into telegram_bot.handle_chat):
  1. looks_multi_step(text): cheap heuristic. Only explicit multi-step asks (sequencing words
     or a numbered list with 2+ items) go to the planner. Everything else keeps the existing
     direct chat path, unchanged.
  2. start(chat_id, text): the planner makes a plan (planner.plan), validate_plan() rejects any
     step that is not an engine in orchestrator.ENGINES or contains shell-like content, then
     the task is stored in tasks file with a stable id per step.
  3. run(task_id): runs steps in order through orchestrator.run_step. Each step:
       - skipped if already done (idempotency: a step id is marked "running" BEFORE its
         consequential action, so a resume never repeats it)
       - a step whose dependency failed or was declined is marked blocked, never run
       - a consequential step (permission class approval) is queued in approvals and the
         task pauses in state "waiting_approval" until /approve or /deny
       - transient failures retry inside orchestrator.run_step; budget exhaustion stops the task
  4. Every transition is audited (action names only; no message text, prompts, or secrets).

Steps are sequential: step n depends on n-1. A failed or declined step blocks all later steps.
Final state is never "completed" unless every step is done.
"""
import json, os, re, threading, time, uuid

from . import audit, budget, orchestrator, permissions, planner

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PATH = os.path.join(BASE, "chat_tasks.json")
_LOCK = threading.Lock()

MULTI_MARKERS = re.compile(
    r"\b(and then|then|after that|afterwards|first\b.*\bthen|step \d|steps?:|followed by|once that's done)\b", re.I)
NUMBERED = re.compile(r"(?:^|\s)(?:1[.)]|\(1\))\s.*(?:\s|^)(?:2[.)]|\(2\))\s", re.S)
SHELLISH = re.compile(r"(\$\(|`|;\s*rm\b|\brm\s+-|\bsudo\b|\|\s*sh\b|curl[^|]*\|\s*(ba)?sh|&&|>\s*/)", re.I)

# which engine's effect is consequential (needs /approve before it runs)
CONSEQUENTIAL = {"followup": "send_message"}
PENDING, RUNNING, DONE, FAILED, BLOCKED, DECLINED, WAITING = (
    "pending", "running", "done", "failed", "blocked", "declined", "waiting_approval")


def looks_multi_step(text):
    t = (text or "").strip()
    if len(t) < 12:
        return False
    if re.match(r"^(hi|hey|hello|yo|thanks|thank you|ok|okay|good (morning|evening))\b", t, re.I):
        return False
    if len(t) < 240 and not MULTI_MARKERS.search(t) and not NUMBERED.search(t):
        return False
    return bool(MULTI_MARKERS.search(t) or NUMBERED.search(t))


def validate_plan(steps):
    """Return (ok, reason). Every step must name a registered engine and carry no shell-like text."""
    if not isinstance(steps, list) or not (1 <= len(steps) <= planner.MAX_STEPS):
        return False, "plan must have 1 to 5 steps"
    for i, s in enumerate(steps, 1):
        if not isinstance(s, dict):
            return False, f"step {i} is malformed"
        eng = s.get("engine")
        if eng not in orchestrator.ENGINES:
            return False, f"step {i} uses an unregistered tool"
        inp = s.get("input") if isinstance(s.get("input"), dict) else {}
        blob = json.dumps(inp, ensure_ascii=False)
        if SHELLISH.search(blob):
            return False, f"step {i} contains a shell-like action and was rejected"
    return True, ""


def _load():
    try:
        with open(PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(d):
    tmp = PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
    os.replace(tmp, PATH)


def _audit(action, task_id, outcome):
    # ids and state names only: no message text, prompts, replies or keys
    audit.record("bot", action, f"task {task_id}", outcome)


def start(chat_id, text, chat_fn=None):
    """Plan and store a task. Returns (task_id or None, reason)."""
    steps = planner.plan(text, chat_fn=chat_fn)
    ok, reason = validate_plan(steps)
    if not ok:
        return None, reason
    task_id = uuid.uuid4().hex[:8]
    rec = {"id": task_id, "chat_id": chat_id, "status": "planned", "created_at": time.time(),
           "goal": text[:200], "steps": []}
    for i, s in enumerate(steps, 1):
        rec["steps"].append({"step_id": f"{task_id}-{i}", "n": i, "engine": s["engine"],
                             "input": s.get("input") or {}, "status": PENDING,
                             "output_kind": orchestrator.ENGINES[s["engine"]][2], "error": None, "approval_id": None})
    with _LOCK:
        d = _load()
        d[task_id] = rec
        _save(d)
    _audit("task_created", task_id, f"{len(steps)} steps")
    return task_id, ""


def get(task_id):
    with _LOCK:
        d = _load()
        return json.loads(json.dumps(d.get(task_id))) if d.get(task_id) else None


def _set(task_id, fn):
    with _LOCK:
        d = _load()
        rec = d.get(task_id)
        if rec is None:
            return None
        fn(rec)
        _save(d)
        return rec


def _next_todo(rec):
    """First step that is not done. Returns (index, step) or (None, None)."""
    for i, s in enumerate(rec["steps"]):
        if s["status"] != DONE:
            return i, s
    return None, None


def progress(rec):
    done = sum(1 for s in rec["steps"] if s["status"] == DONE)
    return done, len(rec["steps"])


def run(task_id, runner=None):
    """Run pending steps in order until done, paused for approval, or stopped. Returns the record."""
    rec = get(task_id)
    if rec is None:
        return None
    if rec["status"] in ("completed", "failed", "stopped"):
        return rec
    _set(task_id, lambda r: r.__setitem__("status", "running"))
    while True:
        rec = get(task_id)
        idx, step = _next_todo(rec)
        if step is None:
            _set(task_id, lambda r: r.__setitem__("status", "completed"))
            _audit("task_completed", task_id, "ok")
            return get(task_id)
        # a step left "running" means the process stopped mid-call: its side effect may have
        # happened, so it is reported and NOT repeated (no duplicate consequential action)
        if step["status"] == RUNNING:
            def interrupt(r, i=idx):
                r["steps"][i]["status"] = FAILED
                r["steps"][i]["error"] = "interrupted before it finished; not repeated"
                r["status"] = "failed"
            _set(task_id, interrupt)
            _audit("step_failed", task_id, f"step {step['n']} interrupted")
            _audit("task_failed", task_id, "interrupted")
            return get(task_id)
        # block dependents of a failed or declined step (sequential: any earlier non-done step blocks)
        if step["status"] in (FAILED, DECLINED, BLOCKED):
            _set(task_id, lambda r: r.__setitem__("status", "stopped"))
            _audit("task_stopped", task_id, step["status"])
            return get(task_id)
        if step["status"] == WAITING:
            return get(task_id)  # paused until /approve or /deny
        action = CONSEQUENTIAL.get(step["engine"])
        if action and permissions.needs_approval(action) and step["status"] != "approved":
            from . import approvals
            rec_a = approvals.queue(rec["chat_id"], action, {"task": task_id, "step": step["step_id"]},
                                    summary=f"task {task_id} step {step['n']}")
            def pause(r, sid=step["step_id"], aid=rec_a["id"]):
                s = r["steps"][idx]
                s["status"] = WAITING
                s["approval_id"] = aid
                r["status"] = "waiting_approval"
            _set(task_id, pause)
            _audit("approval_requested", task_id, f"step {step['n']}")
            return get(task_id)
        # mark running BEFORE the call: a crash mid-step is reported, never silently repeated
        def mark(r, i=idx):
            r["steps"][i]["status"] = RUNNING
        _set(task_id, mark)
        try:
            run_fn = runner or orchestrator.run_step
            out = run_fn({"engine": step["engine"], "input": step["input"]})
            def ok(r, i=idx, o=out):
                r["steps"][i]["status"] = DONE
                r["steps"][i]["error"] = None
            _set(task_id, ok)
            _audit("step_done", task_id, f"step {step['n']}")
        except budget.BudgetExceeded as e:
            def stop(r, i=idx):
                r["steps"][i]["status"] = FAILED
                r["steps"][i]["error"] = "budget"
            _set(task_id, stop)
            _audit("step_failed", task_id, f"step {step['n']} budget")
            _set(task_id, lambda r: r.__setitem__("status", "failed"))
            _audit("task_failed", task_id, "budget")
            return get(task_id)
        except Exception as e:
            def fail(r, i=idx, msg=str(e)[:120]):
                r["steps"][i]["status"] = FAILED
                r["steps"][i]["error"] = msg
            _set(task_id, fail)
            _audit("step_failed", task_id, f"step {step['n']}")
            _set(task_id, lambda r: r.__setitem__("status", "failed"))
            _audit("task_failed", task_id, f"step {step['n']}")
            return get(task_id)


def resolve_approval(task_id, approved):
    """Called when the owner approves or denies the step that is waiting. Returns the record."""
    aid = [None]
    def fn(r):
        for s in r["steps"]:
            if s["status"] == WAITING:
                s["status"] = "approved" if approved else DECLINED
                aid[0] = s.get("approval_id")
                break
    rec = _set(task_id, fn)
    if aid[0] and not approved:
        from . import approvals
        approvals.reject(rec["chat_id"], aid[0])
    if rec is None:
        return None
    _audit("approval_granted" if approved else "approval_denied", task_id, "")
    if not approved:
        _set(task_id, lambda r: r.__setitem__("status", "stopped"))
        return get(task_id)
    return run(task_id)


def task_for_approval(approval_id, chat_id):
    """Task id whose waiting step holds this approval id, for the same chat only."""
    with _LOCK:
        for tid, rec in _load().items():
            if rec.get("chat_id") != chat_id or rec.get("status") != "waiting_approval":
                continue
            for s in rec["steps"]:
                if s["status"] == WAITING and s.get("approval_id") == approval_id:
                    return tid
    return None


def summary(rec):
    """Plain text summary. Never claims success unless every step is done."""
    done, total = progress(rec)
    lines = []
    for s in rec["steps"]:
        mark = {DONE: "done", FAILED: "failed", BLOCKED: "not run (blocked)", DECLINED: "declined",
                WAITING: "waiting for your approval", "approved": "approved, running", PENDING: "not run",
                RUNNING: "interrupted (not repeated)"}.get(s["status"], s["status"])
        lines.append(f"step {s['n']} ({s['engine']}): {mark}")
    if rec["status"] == "completed":
        head = f"done: all {total} steps finished."
    elif rec["status"] == "waiting_approval":
        head = f"step {done + 1} of {total} needs your approval. send /approve to run it or /deny to stop."
    elif rec["status"] == "failed":
        head = f"stopped at a failed step: {done} of {total} steps finished."
    elif rec["status"] == "stopped":
        head = f"stopped: {done} of {total} steps finished."
    else:
        head = f"step {done + 1} of {total} running."
    return head + "\n" + "\n".join(lines)
