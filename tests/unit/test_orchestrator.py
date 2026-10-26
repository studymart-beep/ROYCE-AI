import pytest
from app.agent import orchestrator as o
from app.agent import planner


def no_sleep(_):
    pass


def test_missing_input_is_rejected_without_calling_engine(monkeypatch):
    called = []
    monkeypatch.setitem(o.ENGINES, "script", (["product", "audience"], lambda i: called.append(i) or {"a": 1}, "dict"))
    with pytest.raises(o.StepError, match="missing input: audience"):
        o.run_step({"engine": "script", "input": {"product": "NOISE"}}, sleep=no_sleep)
    assert called == []


def test_unknown_engine_is_refused():
    with pytest.raises(o.StepError, match="unknown engine"):
        o.run_step({"engine": "nope", "input": {}}, sleep=no_sleep)


def test_empty_text_fails_verification(monkeypatch):
    monkeypatch.setitem(o.ENGINES, "llm", (["prompt"], lambda i: "   ", "text"))
    with pytest.raises(o.StepError, match="empty text"):
        o.run_step({"engine": "llm", "input": {"prompt": "hi"}}, sleep=no_sleep)


def test_wrong_shape_fails_verification(monkeypatch):
    monkeypatch.setitem(o.ENGINES, "content", (["client", "industry", "platform"], lambda i: ["not", "a", "dict"], "dict"))
    with pytest.raises(o.StepError, match="expected a non-empty object"):
        o.run_step({"engine": "content", "input": {"client": "a", "industry": "b", "platform": "c"}}, sleep=no_sleep)


def test_transient_error_retries_then_succeeds(monkeypatch):
    attempts = {"n": 0}
    sleeps = []
    def flaky(i):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise ConnectionError("timeout")
        return "ok text"
    monkeypatch.setitem(o.ENGINES, "llm", (["prompt"], flaky, "text"))
    out = o.run_step({"engine": "llm", "input": {"prompt": "x"}}, sleep=sleeps.append)
    assert out == "ok text"
    assert attempts["n"] == 3
    assert sleeps == [1.0, 2.0]  # exponential backoff


def test_retries_are_bounded_to_two(monkeypatch):
    attempts = {"n": 0}
    def always(i):
        attempts["n"] += 1
        raise ConnectionError("down")
    monkeypatch.setitem(o.ENGINES, "llm", (["prompt"], always, "text"))
    with pytest.raises(o.StepError, match="failed after 3 attempts"):
        o.run_step({"engine": "llm", "input": {"prompt": "x"}}, sleep=no_sleep)
    assert attempts["n"] == 3  # 1 try + 2 retries


def test_validation_error_is_not_retried(monkeypatch):
    attempts = {"n": 0}
    def bad(i):
        attempts["n"] += 1
        raise o.StepError("lead not found")
    monkeypatch.setitem(o.ENGINES, "followup", (["lead"], bad, "text"))
    with pytest.raises(o.StepError):
        o.run_step({"engine": "followup", "input": {"lead": "x"}}, sleep=no_sleep)
    assert attempts["n"] == 1


def test_run_plan_stops_at_first_failure(monkeypatch):
    ran = []
    monkeypatch.setitem(o.ENGINES, "llm", (["prompt"], lambda i: ran.append(i["prompt"]) or "done text", "text"))
    steps = [{"engine": "llm", "input": {"prompt": "one"}},
             {"engine": "llm", "input": {}},  # missing prompt
             {"engine": "llm", "input": {"prompt": "three"}}]
    res = o.run_plan(steps, sleep=no_sleep)
    assert res["ok"] is False
    assert "step 2" in res["error"]
    assert ran == ["one"]
    assert len(res["results"]) == 1


def test_planner_falls_back_to_single_step_on_bad_json():
    steps = planner.plan("make hooks for NOISE", chat_fn=lambda m: "not json at all")
    assert steps == [{"engine": "llm", "input": {"prompt": "make hooks for NOISE"}}]


def test_planner_falls_back_when_chat_raises():
    def boom(m):
        raise RuntimeError("all providers failed")
    steps = planner.plan("hi", chat_fn=boom)
    assert len(steps) == 1 and steps[0]["engine"] == "llm"


def test_planner_accepts_valid_plan_and_drops_unknown_engine_to_fallback():
    good = '{"steps": [{"engine": "script", "input": {"product": "NOISE", "audience": "commuters"}}, {"engine": "llm", "input": {"prompt": "summarise"}}]}'
    steps = planner.plan("make a script then summarise", chat_fn=lambda m: good)
    assert [s["engine"] for s in steps] == ["script", "llm"]
    bad = '{"steps": [{"engine": "launch_missiles", "input": {}}]}'
    steps = planner.plan("x", chat_fn=lambda m: bad)
    assert steps[0]["engine"] == "llm"


def test_planner_caps_at_five_steps():
    six = '{"steps": [' + ",".join(['{"engine": "llm", "input": {"prompt": "p"}}'] * 6) + ']}'
    steps = planner.plan("x", chat_fn=lambda m: six)
    assert len(steps) == 1  # over the cap is an error, so single-step fallback
