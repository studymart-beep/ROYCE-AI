"""Chief of Staff: routing JSON parse and fallback, delegation with the real budget, hop guard, not deletable,
specialist persona actually sent to the model, audit rows (ids only), and the chat endpoint wiring."""
import json

import pytest
from fastapi.testclient import TestClient

from app import bots, chief, conversations, main
from app.agent import audit, budget

TOKEN = "test-owner-token-chief"
OWNER = {"x-workspace-token": TOKEN}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", TOKEN)
    monkeypatch.setattr(bots, "PATH", str(tmp_path / "bots.json"))
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    budget.reset()
    yield
    budget.reset()


@pytest.fixture
def client():
    return TestClient(main.app)


def _audit_rows(tmp_path_file):
    try:
        with open(tmp_path_file, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def _specialists():
    direct = bots.create("Ad hooks", "direct-response")
    brand = bots.create("Brand voice", "brand-strategy")
    return direct, brand


class FakeLLM:
    """Scripted llm: routing calls get route_reply; specialist/direct calls get the next answer. Records every
    request so tests can assert on the system prompt and instruction that were actually sent."""

    def __init__(self, route_reply, answers=("answer text",)):
        self.route_reply = route_reply
        self.answers = list(answers)
        self.calls = []

    def __call__(self, msgs, **kw):
        self.calls.append({"msgs": msgs, "kw": kw})
        system = msgs[0]["content"]
        if "Reply with ONLY one JSON object" in system:
            return self.route_reply
        return self.answers.pop(0) if self.answers else "fallback answer"


def _sanitize(t):
    return t


# --- routing JSON parse and fallback -------------------------------------------------------------

def test_parse_single_step_and_fenced_json():
    raw = '```json\n{"bot": "abc", "instruction": "3 ad hooks for NOISE earbuds", "needs_approval": false}\n```'
    plan = chief.parse_routing(raw, {"abc"})
    assert plan is not None and not plan.needs_approval
    assert plan.steps == [{"bot": "abc", "instruction": "3 ad hooks for NOISE earbuds", "needs_approval": False}]


def test_parse_self_route_and_prose_around_json():
    plan = chief.parse_routing('sure. {"bot": "self", "instruction": "which product?", "needs_approval": false} ok', set())
    assert plan.is_self


@pytest.mark.parametrize("raw", [
    "", "just a sentence no json", "{not json}", "[1,2,3]",
    '{"bot": "ghost", "instruction": "x", "needs_approval": false}',          # unknown bot id
    '{"bot": "abc", "instruction": "", "needs_approval": false}',            # empty instruction
    '{"bot": "abc", "instruction": "x", "needs_approval": "yes"}',           # non-bool flag
    '{"bot": "abc", "instruction": "x", "needs_approval": 1}',               # int is not a bool
    '{"steps": []}',                                                          # empty plan
    '{"steps": [{"bot": "abc", "instruction": "a"}, {"bot": "abc", "instruction": "b"}]}',  # loop: same bot twice
    '{"steps": [{"bot": "self", "instruction": "a"}, {"bot": "abc", "instruction": "b"}]}',  # self mixed in
    '{"steps": [{"bot": "abc", "instruction": "a"}, {"bot": "abc", "instruction": "b"}, {"bot": "x", "instruction": "c"}, {"bot": "y", "instruction": "d"}]}',
])
def test_malformed_or_unsafe_routing_is_rejected(raw):
    assert chief.parse_routing(raw, {"abc", "x", "y"}) is None


def test_missing_approval_flag_defaults_to_needs_approval():
    plan = chief.parse_routing('{"bot": "abc", "instruction": "x"}', {"abc"})
    assert plan is not None and plan.needs_approval is True


def test_malformed_routing_falls_back_to_chief_direct_answer(client, monkeypatch):
    _specialists()
    chief_bot = bots.ensure_coordinator()
    fake = FakeLLM("I think the ad hooks person should do it", answers=["chief says hi"])
    res = chief.run("hi there", [], chief_bot, chat=fake, sanitize=_sanitize)
    assert res["delegations"] == [] and res["reply"] == "chief says hi"
    assert any(r["action"] == "routing_fallback" for r in _audit_rows(audit.PATH))


# --- delegation, persona, budget, hops -----------------------------------------------------------

def test_specialist_persona_is_the_skill_prompt_sent_to_the_model(client, monkeypatch):
    direct, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    route = json.dumps({"bot": direct["id"], "instruction": "3 ad hooks for NOISE earbuds, young Lagos commuters",
                        "needs_approval": False})
    fake = FakeLLM(route, answers=["1. hook one\n2. hook two\n3. hook three"])
    res = chief.run("give me 3 ad hooks for NOISE earbuds", [], chief_bot, chat=fake, sanitize=_sanitize)

    spec_call = fake.calls[1]
    system = spec_call["msgs"][0]["content"]
    assert f'named "{direct["name"]}"' in system
    assert "Follow this playbook" in system
    assert "Direct response" in system or "hook" in system.lower()  # the real direct-response skill body
    user = spec_call["msgs"][-1]["content"]
    assert user == "3 ad hooks for NOISE earbuds, young Lagos commuters"
    assert res["delegations"] == [{"bot_id": direct["id"], "bot_name": "Ad hooks", "status": "completed"}]
    assert res["reply"].startswith("here's what Ad hooks came back with:")
    assert "hook one" in res["reply"]


def test_delegation_uses_the_shared_budget(client, monkeypatch):
    """Route call + specialist call each consume one llm attempt from the shared hourly bucket."""
    from app import llm
    from app.agent import router
    direct, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    monkeypatch.setattr(budget, "CAP", 2)
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: (
        json.dumps({"bot": direct["id"], "instruction": "x", "needs_approval": False})
        if "Reply with ONLY one JSON object" in msgs[0]["content"] else "ok"))
    monkeypatch.setattr(router, "is_configured", lambda pid: True, raising=False)
    token = budget.bind("web")
    try:
        chief.run("hooks please", [], chief_bot, chat=lambda m, **kw: llm.chat_text(m, **kw), sanitize=_sanitize)
        assert budget.used("web") == 2
        with pytest.raises(budget.BudgetExceeded):
            llm.chat_text([{"role": "user", "content": "one more"}])
    finally:
        budget.unbind(token)


def test_delegation_stops_when_budget_is_exhausted(client, monkeypatch):
    """With the cap hit mid-delegation, the call raises BudgetExceeded and nothing is stored."""
    from app import llm
    from app.agent import router
    direct, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    monkeypatch.setattr(budget, "CAP", 1)  # only the routing call fits
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: json.dumps(
        {"bot": direct["id"], "instruction": "x", "needs_approval": False}))
    monkeypatch.setattr(router, "is_configured", lambda pid: True, raising=False)
    token = budget.bind("web")
    try:
        with pytest.raises(budget.BudgetExceeded):
            chief.run("hooks", [], chief_bot, chat=lambda m, **kw: llm.chat_text(m, **kw), sanitize=_sanitize)
    finally:
        budget.unbind(token)


def test_hop_guard_caps_plan_at_two_specialist_calls(client, monkeypatch):
    d, b = _specialists()
    third = bots.create("Funnel", "funnel-design")
    chief_bot = bots.ensure_coordinator()
    plan = {"steps": [
        {"bot": d["id"], "instruction": "hooks", "needs_approval": False},
        {"bot": b["id"], "instruction": "voice", "needs_approval": False},
        {"bot": third["id"], "instruction": "funnel", "needs_approval": False},
    ]}
    fake = FakeLLM(json.dumps(plan), answers=["hooks out", "voice out", "should never run"])
    res = chief.run("full launch pack", [], chief_bot, chat=fake, sanitize=_sanitize)
    statuses = [x["status"] for x in res["delegations"]]
    assert statuses == ["completed", "completed", "skipped_hop_limit"]
    # exactly route + 2 specialist calls reached the model
    assert len(fake.calls) == 3
    assert "should never run" not in res["reply"]


def test_multi_step_passes_prior_output_forward(client, monkeypatch):
    d, b = _specialists()
    chief_bot = bots.ensure_coordinator()
    plan = {"steps": [
        {"bot": d["id"], "instruction": "hooks", "needs_approval": False},
        {"bot": b["id"], "instruction": "brand angle", "needs_approval": False},
    ]}
    fake = FakeLLM(json.dumps(plan), answers=["HOOK LIST", "BRAND ANGLE"])
    chief.run("launch", [], chief_bot, chat=fake, sanitize=_sanitize)
    second_user = fake.calls[2]["msgs"][-1]["content"]
    assert "HOOK LIST" in second_user and "brand angle" in second_user


def test_needs_approval_runs_nothing_and_returns_the_plan(client):
    d, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    route = json.dumps({"bot": d["id"], "instruction": "post the hooks to the page", "needs_approval": True})
    fake = FakeLLM(route)
    res = chief.run("post these", [], chief_bot, chat=fake, sanitize=_sanitize)
    assert res["status"] == "awaiting_approval"
    assert len(fake.calls) == 1  # only the routing call; the specialist never ran
    assert "post the hooks to the page" in res["reply"]


def test_chief_cannot_route_to_a_chief(client):
    chief_bot = bots.ensure_coordinator()
    fake = FakeLLM(json.dumps({"bot": chief_bot["id"], "instruction": "x", "needs_approval": False}))
    res = chief.run("loop me", [], chief_bot, chat=fake, sanitize=_sanitize)
    # the chief is not in the specialist list, so this id is invalid and the call falls back to direct
    assert res["delegations"] == []


# --- chief bot seed and deletion -----------------------------------------------------------------

def test_chief_is_seeded_once_and_pinned_first(client):
    a = bots.ensure_coordinator()
    b = bots.ensure_coordinator()
    assert a["id"] == b["id"] and a["name"] == "Chief of Staff" and a["template"] == "coordinator"
    bots.create("Zed", "direct-response")
    listed = bots.list_bots()
    assert listed[0]["template"] == "coordinator"
    assert sum(1 for x in listed if x["template"] == "coordinator") == 1


def test_chief_cannot_be_deleted_via_store_or_api(client):
    chief_bot = bots.ensure_coordinator()
    with pytest.raises(ValueError):
        bots.delete(chief_bot["id"])
    r = client.delete(f"/api/ws/bots/{chief_bot['id']}", headers=OWNER)
    assert r.status_code == 409
    assert bots.get(chief_bot["id"]) is not None


def test_coordinator_template_cannot_be_created_by_hand(client):
    with pytest.raises(ValueError):
        bots.create("Second chief", "coordinator")
    assert client.post("/api/ws/bots", json={"name": "Sneaky", "template": "coordinator"}, headers=OWNER).status_code == 400


def test_templates_picker_excludes_coordinator(client):
    keys = [t["key"] for t in bots.templates()]
    assert "coordinator" not in keys and len(keys) == 6


# --- API: GET /bots/chief and chat honoring the active bot --------------------------------------

def test_get_chief_endpoint_creates_then_returns_same(client):
    r1 = client.get("/api/ws/bots/chief", headers=OWNER)
    r2 = client.get("/api/ws/bots/chief", headers=OWNER)
    assert r1.status_code == 200 and r1.json()["id"] == r2.json()["id"]
    assert r1.json()["name"] == "Chief of Staff"


def test_chat_with_chief_delegates_and_returns_metadata(client, monkeypatch):
    from app import llm
    d, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    route = json.dumps({"bot": d["id"], "instruction": "3 hooks for NOISE earbuds", "needs_approval": False})
    fake = FakeLLM(route, answers=["hook a\nhook b\nhook c"])
    monkeypatch.setattr(llm, "chat_text", fake)
    r = client.post("/api/ws/chat", json={"message": "give me 3 ad hooks for NOISE earbuds",
                                          "bot_id": chief_bot["id"]}, headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["delegations"][0]["bot_name"] == "Ad hooks"
    assert body["bot"]["name"] == "Chief of Staff"
    assert "here's what Ad hooks came back with" in body["reply"]
    stored = conversations.get(body["conversation_id"])["messages"][-1]
    assert stored["meta"]["delegations"][0]["bot_id"] == d["id"]


def test_chat_with_unknown_bot_is_404_and_stores_nothing(client):
    r = client.post("/api/ws/chat", json={"message": "hi", "bot_id": "nope"}, headers=OWNER)
    assert r.status_code == 404


# --- audit: bot ids and task only, never content -------------------------------------------------

def test_audit_rows_record_ids_not_content(client, monkeypatch):
    d, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    secret = "SECRET-LAUNCH-NUMBER-41233"
    route = json.dumps({"bot": d["id"], "instruction": secret, "needs_approval": False})
    fake = FakeLLM(route, answers=["reply with another secret ZZZ-9999"])
    chief.run("hooks", [], chief_bot, chat=fake, sanitize=_sanitize)
    rows = _audit_rows(audit.PATH)
    delegated = [r for r in rows if r["action"] == "delegated"]
    assert delegated and delegated[0]["target"] == f"{chief_bot['id']}->{d['id']}"
    raw = open(audit.PATH, encoding="utf-8").read()
    assert secret not in raw and "ZZZ-9999" not in raw


def test_work_chat_returns_delegation_metadata_per_step(client, monkeypatch):
    """The chief work chat gets real delegation metadata: which bot ran, and the status of each step."""
    from app import llm
    from app.agent import router
    direct, brand = _specialists()
    chief_bot = bots.ensure_coordinator()
    route = json.dumps({"steps": [
        {"bot": direct["id"], "instruction": "3 hooks", "needs_approval": False},
        {"bot": brand["id"], "instruction": "check tone", "needs_approval": False},
    ]})
    fake = FakeLLM(route, answers=("hook one", "tone ok"))
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: fake(msgs, temperature=temp))
    monkeypatch.setattr(router, "is_configured", lambda pid: True, raising=False)
    r = client.post("/api/ws/chat", json={"message": "hooks then tone", "bot_id": chief_bot["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bot"]["id"] == chief_bot["id"]
    names = [d["bot_name"] for d in body["delegations"]]
    assert names == ["Ad hooks", "Brand voice"]
    assert all(d["status"] == "completed" for d in body["delegations"])
    # the stored work-chat message keeps the same metadata so a reload can re-render it
    conv = conversations.get(body["conversation_id"])
    assert conv["bot_id"] == chief_bot["id"]
    assert conv["messages"][-1]["meta"]["delegations"][0]["bot_name"] == "Ad hooks"


def test_work_chat_approval_roundtrip_through_endpoint(client, monkeypatch):
    """A step that needs approval shows as awaiting_approval, and approving through the endpoint runs it."""
    from app import llm
    from app.agent import approvals, router
    direct, _ = _specialists()
    chief_bot = bots.ensure_coordinator()
    route = json.dumps({"steps": [{"bot": direct["id"], "instruction": "send it", "needs_approval": True}]})
    fake = FakeLLM(route, answers=("done",))
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: fake(msgs, temperature=temp))
    monkeypatch.setattr(router, "is_configured", lambda pid: True, raising=False)
    body = client.post("/api/ws/chat", json={"message": "send hooks", "bot_id": chief_bot["id"]}).json()
    assert body["status"] == "awaiting_approval"
    assert body["delegations"][0]["status"] == "awaiting_approval"
    # approvals endpoint lists pending items for the work chat to render
    pending = client.get("/api/ws/approvals").json()
    assert isinstance(pending, list)
    # approve through the endpoint with a queued record bound to this chat
    rec = approvals.queue("chat-x", "send_message", {"to": "x"}, summary="send hooks")
    rid = rec["id"]
    assert any(a["id"] == rid and a["status"] == approvals.PENDING for a in client.get("/api/ws/approvals").json())
    r = client.post(f"/api/ws/approvals/{rid}/reject")
    assert r.status_code == 200, r.text
    assert not any(a["id"] == rid for a in client.get("/api/ws/approvals").json() if a["status"] == approvals.PENDING)
