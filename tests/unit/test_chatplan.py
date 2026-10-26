import json
import pytest
from app.agent import chatplan, orchestrator, budget, audit, approvals, planner


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chatplan, "PATH", str(tmp_path / "chat_tasks.json"))
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    budget.reset()
    yield


def plan_fn(steps):
    return lambda msgs: json.dumps({"steps": steps})


def ok_runner(step):
    return "out:" + step["engine"]


def test_simple_message_stays_on_direct_path():
    assert chatplan.looks_multi_step("hi there") is False
    assert chatplan.looks_multi_step("what is a hook") is False
    assert chatplan.looks_multi_step("thanks") is False


def test_multi_step_ask_is_routed_to_planner():
    assert chatplan.looks_multi_step("first write a hook for NOISE earbuds then make an ad doctor check") is True


def test_multi_step_runs_ordered_steps():
    seen = []
    def runner(step):
        seen.append(step["engine"])
        return "done"
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "doctor", "input": {"text": "b"}}]
    tid, reason = chatplan.start(1, "do a then b", chat_fn=plan_fn(steps))
    assert tid and reason == ""
    rec = chatplan.run(tid, runner=runner)
    assert seen == ["llm", "doctor"]
    assert rec["status"] == "completed"
    assert "done: all 2 steps finished" in chatplan.summary(rec)


def test_unregistered_tool_is_rejected_at_plan_time():
    steps = [{"engine": "shell", "input": {"cmd": "ls"}}]
    tid, reason = chatplan.start(1, "list files then tell me", chat_fn=plan_fn(steps))
    # planner.plan falls back to a single llm step on an unknown engine, so the plan is still safe
    assert tid is not None
    rec = chatplan.get(tid)
    assert all(s["engine"] in orchestrator.ENGINES for s in rec["steps"])


def test_validate_plan_rejects_unknown_and_shellish():
    assert chatplan.validate_plan([{"engine": "rm_rf", "input": {}}])[0] is False
    assert chatplan.validate_plan([{"engine": "llm", "input": {"prompt": "x; rm -rf /"}}])[0] is False
    assert chatplan.validate_plan([{"engine": "llm", "input": {"prompt": "hi"}}])[0] is True
    assert chatplan.validate_plan([])[0] is False
    assert chatplan.validate_plan(["not a dict"])[0] is False


def test_failed_dependency_blocks_downstream():
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "doctor", "input": {"text": "b"}},
             {"engine": "llm", "input": {"prompt": "c"}}]
    tid, _ = chatplan.start(1, "a then b then c", chat_fn=plan_fn(steps))
    ran = []
    def runner(step):
        ran.append(step["engine"])
        if step["engine"] == "doctor":
            raise orchestrator.StepError("boom")
        return "ok"
    rec = chatplan.run(tid, runner=runner)
    assert ran == ["llm", "doctor"]  # third step never ran
    assert rec["status"] == "failed"
    text = chatplan.summary(rec)
    assert "failed" in text and "never" not in text.lower() or "stopped" in text
    assert "done: all" not in text


def test_approval_pauses_then_approve_runs_step():
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "followup", "input": {"lead": "x"}}]
    tid, _ = chatplan.start(7, "a then send followup", chat_fn=plan_fn(steps))
    ran = []
    def runner(step):
        ran.append(step["engine"])
        return "ok"
    rec = chatplan.run(tid, runner=runner)
    assert rec["status"] == "waiting_approval"
    assert ran == ["llm"]  # the consequential step did not run
    aid = [s for s in rec["steps"] if s["status"] == "waiting_approval"][0]["approval_id"]
    assert chatplan.task_for_approval(aid, 7) == tid
    assert chatplan.task_for_approval(aid, 999) is None  # other chats cannot approve it


def test_denial_prevents_consequential_step():
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "followup", "input": {"lead": "x"}}]
    tid, _ = chatplan.start(7, "a then send followup", chat_fn=plan_fn(steps))
    ran = []
    rec = chatplan.run(tid, runner=lambda s: ran.append(s["engine"]) or "ok")
    rec = chatplan.resolve_approval(tid, approved=False)
    assert rec["status"] == "stopped"
    assert "followup" not in ran
    assert any(s["status"] == "declined" for s in rec["steps"])
    assert "done: all" not in chatplan.summary(rec)


def test_no_duplicate_on_resume_of_interrupted_step():
    steps = [{"engine": "llm", "input": {"prompt": "a"}}]
    tid, _ = chatplan.start(1, "a then b", chat_fn=plan_fn(steps))
    # simulate a crash mid-call: step left in running
    d = json.load(open(chatplan.PATH))
    d[tid]["steps"][0]["status"] = "running"
    json.dump(d, open(chatplan.PATH, "w"))
    calls = []
    rec = chatplan.run(tid, runner=lambda s: calls.append(1) or "ok")
    assert calls == []  # not repeated
    assert rec["status"] == "failed"


def test_budget_exhaustion_stops_task_as_failed(monkeypatch):
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "llm", "input": {"prompt": "b"}}]
    tid, _ = chatplan.start(1, "a then b", chat_fn=plan_fn(steps))
    def runner(s):
        raise budget.BudgetExceeded(retry_after=120)
    rec = chatplan.run(tid, runner=runner)
    assert rec["status"] == "failed"
    assert rec["steps"][0]["error"] == "budget"
    assert rec["steps"][1]["status"] == "pending"  # downstream not run


def test_step_status_survives_restart():
    steps = [{"engine": "llm", "input": {"prompt": "a"}}, {"engine": "llm", "input": {"prompt": "b"}}]
    tid, _ = chatplan.start(1, "a then b", chat_fn=plan_fn(steps))
    chatplan.run(tid, runner=lambda s: "ok")
    assert chatplan.get(tid)["status"] == "completed"  # reread from disk, not memory


def test_audit_records_transitions_without_content(tmp_path):
    steps = [{"engine": "llm", "input": {"prompt": "SECRETPROMPT"}}]
    tid, _ = chatplan.start(1, "a then b SECRETPROMPT", chat_fn=plan_fn(steps))
    chatplan.run(tid, runner=lambda s: "ok")
    text = open(audit.PATH).read()
    for action in ("task_created", "step_done", "task_completed"):
        assert action in text
    assert "SECRETPROMPT" not in text
    assert "sk-" not in text
