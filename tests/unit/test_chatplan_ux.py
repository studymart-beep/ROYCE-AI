import json
import pytest
from app.agent import chatplan, budget, audit, approvals


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(chatplan, "PATH", str(tmp_path / "chat_tasks.json"))
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    chatplan.STOP_EVENT.clear()
    budget.reset()
    yield
    chatplan.STOP_EVENT.clear()


def make(n):
    steps = [{"engine": "llm", "input": {"prompt": f"p{i}"}} for i in range(n)]
    tid, _ = chatplan.start(1, "a then b then c", chat_fn=lambda m: json.dumps({"steps": steps}))
    return tid


def test_progress_reported_per_step():
    tid = make(3)
    msgs = []
    chatplan.run(tid, runner=lambda s: "ok", on_progress=msgs.append)
    assert msgs == ["step 1 of 3 done .", "step 2 of 3 done .", "step 3 of 3 done"]


def test_progress_send_failure_does_not_break_task():
    tid = make(2)
    def boom(t):
        raise RuntimeError("telegram down")
    rec = chatplan.run(tid, runner=lambda s: "ok", on_progress=boom)
    assert rec["status"] == "completed"


def test_shutdown_stops_between_steps_not_mid_call():
    tid = make(3)
    calls = []
    def runner(s):
        calls.append(1)
        chatplan.STOP_EVENT.set()  # signal arrives during step 1
        return "ok"
    rec = chatplan.run(tid, runner=runner)
    assert len(calls) == 1  # step 1 finished, step 2 never started
    assert rec["status"] == "stopped"
    assert rec["steps"][0]["status"] == "done"


def test_restart_recovery_marks_running_failed_and_never_repeats():
    tid = make(2)
    d = json.load(open(chatplan.PATH))
    d[tid]["status"] = "running"
    d[tid]["steps"][0]["status"] = "running"
    json.dump(d, open(chatplan.PATH, "w"))
    assert chatplan.recover_on_start() == 1
    rec = chatplan.get(tid)
    assert rec["status"] == "failed"
    assert rec["steps"][0]["status"] == "failed"
    assert "not repeated" in rec["steps"][0]["error"]
    calls = []
    chatplan.run(tid, runner=lambda s: calls.append(1) or "ok")
    assert calls == []


def test_failed_summary_is_plain_and_never_claims_success():
    tid = make(2)
    def runner(s):
        raise RuntimeError("upstream 503")
    rec = chatplan.run(tid, runner=runner)
    text = chatplan.summary(rec)
    assert "done: all" not in text
    assert "stopped at a failed step" in text
    assert "503" not in text  # internal error text stays out of the user summary


def test_failure_summary_has_no_dashes():
    tid = make(1)
    rec = chatplan.run(tid, runner=lambda s: (_ for _ in ()).throw(RuntimeError("x")))
    text = chatplan.summary(rec)
    assert "—" not in text and "–" not in text
