import json
import pytest
from fastapi.testclient import TestClient

from app import conversations, main
from app.agent import approvals, audit, budget, chatplan, memory

TOKEN = "test-owner-token-123"
OWNER = {"x-workspace-token": TOKEN}


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", TOKEN)
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(chatplan, "PATH", str(tmp_path / "chat_tasks.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(memory, "PATH", str(tmp_path / "memory.json"), raising=False)
    budget.reset()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def _no_llm(monkeypatch, reply="hello from royce"):
    from app import workspace_api
    monkeypatch.setattr(workspace_api, "_llm_chat", lambda msgs, **kw: reply)
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda text: "sys")


def _fake_provider(monkeypatch, reply="step output text"):
    """Stub only the HTTP call: the real router, budget and verification still run."""
    from app import llm
    from app.agent import router
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: reply)
    monkeypatch.setattr(router, "is_configured", lambda pid: True, raising=False)


# --- owner gate ---------------------------------------------------------------

def test_workspace_routes_require_owner_token(client, monkeypatch):
    _no_llm(monkeypatch)
    assert client.get("/api/ws/conversations").status_code == 401
    assert client.get("/api/ws/conversations", headers={"x-workspace-token": "wrong"}).status_code == 401
    assert client.post("/api/ws/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/ws/conversations", headers=OWNER).status_code == 200


def test_bearer_token_also_accepted(client):
    assert client.get("/api/ws/conversations", headers={"Authorization": f"Bearer {TOKEN}"}).status_code == 200


def test_unset_token_fails_closed(client, monkeypatch):
    monkeypatch.delenv("WORKSPACE_TOKEN", raising=False)
    assert client.get("/api/ws/conversations", headers=OWNER).status_code == 503


# --- chat -----------------------------------------------------------------------

def test_chat_direct_answer_is_stored_and_titled(client, monkeypatch):
    _no_llm(monkeypatch, "here is your hook")
    r = client.post("/api/ws/chat", json={"message": "write a hook for noise earbuds"}, headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert body["reply"] == "here is your hook"
    assert body["task_id"] is None and body["status"] == "completed"
    conv = client.get(f"/api/ws/conversations/{body['conversation_id']}", headers=OWNER).json()
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conv["title"] == "write a hook for noise earbuds"


def test_second_message_reuses_conversation(client, monkeypatch):
    _no_llm(monkeypatch)
    cid = client.post("/api/ws/chat", json={"message": "one"}, headers=OWNER).json()["conversation_id"]
    client.post("/api/ws/chat", json={"message": "two", "conversation_id": cid}, headers=OWNER)
    assert len(client.get(f"/api/ws/conversations/{cid}", headers=OWNER).json()["messages"]) == 4
    assert len(client.get("/api/ws/conversations", headers=OWNER).json()) == 1


def test_empty_message_rejected(client, monkeypatch):
    _no_llm(monkeypatch)
    assert client.post("/api/ws/chat", json={"message": ""}, headers=OWNER).status_code == 422


def test_provider_failure_stores_nothing(client, monkeypatch):
    from app import workspace_api

    def boom(msgs, **kw):
        raise RuntimeError("all providers failed: timeout")
    monkeypatch.setattr(workspace_api, "_llm_chat", boom)
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda t: "sys")
    r = client.post("/api/ws/chat", json={"message": "hi"}, headers=OWNER)
    assert r.status_code == 503
    assert client.get("/api/ws/conversations", headers=OWNER).json() == []


def test_chat_shares_budget_bucket(client, monkeypatch):
    from app import workspace_api
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda t: "sys")
    _fake_provider(monkeypatch)
    monkeypatch.setattr(budget, "CAP", 2)
    assert client.post("/api/ws/chat", json={"message": "a"}, headers=OWNER).status_code == 200
    assert client.post("/api/ws/chat", json={"message": "b"}, headers=OWNER).status_code == 200
    r = client.post("/api/ws/chat", json={"message": "c"}, headers=OWNER)
    assert r.status_code == 429
    assert r.json()["detail"]["error"] == "budget"


# --- multi-step tasks -------------------------------------------------------------

def test_multi_step_task_created_and_status_polled(client, monkeypatch):
    from app import workspace_api
    steps = [{"engine": "llm", "input": {"prompt": "write a hook"}}, {"engine": "llm", "input": {"prompt": "check it"}}]
    monkeypatch.setattr(workspace_api.chatplan, "looks_multi_step", lambda t: True)
    monkeypatch.setattr(chatplan.planner, "plan", lambda text, chat_fn=None: steps)
    _fake_provider(monkeypatch, "done text here")
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda t: "sys")
    r = client.post("/api/ws/chat", json={"message": "first write a hook, then check it"}, headers=OWNER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["task_id"]
    t = client.get(f"/api/ws/tasks/{body['task_id']}", headers=OWNER).json()
    assert t["steps_total"] == 2
    assert t["steps_done"] == 2
    assert t["status"] == "completed"
    assert t["current_step"] is None


def test_task_status_unknown_id_404(client):
    assert client.get("/api/ws/tasks/nope", headers=OWNER).status_code == 404


def test_task_of_another_chat_is_not_exposed(client, monkeypatch):
    monkeypatch.setattr(chatplan.planner, "plan", lambda text, chat_fn=None: [{"engine": "llm", "input": {"prompt": "x"}}])
    tid, _ = chatplan.start(999, "first write a hook then check it")
    assert client.get(f"/api/ws/tasks/{tid}", headers=OWNER).status_code == 404
    assert all(t["id"] != tid for t in client.get("/api/ws/tasks", headers=OWNER).json())


# --- approvals ----------------------------------------------------------------------

def _pending_approval(chat_id=None):
    approvals.register_executor("send_message", lambda payload: "sent")
    return approvals.queue(chat_id if chat_id is not None else "web", "send_message", {"to": "x"},
                           summary="send hook to x")["id"]


def test_approvals_list_and_approve(client):
    rid = _pending_approval()
    items = client.get("/api/ws/approvals", headers=OWNER).json()
    assert items[0]["id"] == rid and items[0]["status"] == "pending"
    r = client.post(f"/api/ws/approvals/{rid}/approve", headers=OWNER)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert client.get("/api/ws/approvals", headers=OWNER).json()[0]["status"] == "done"


def test_approval_cannot_be_approved_twice(client):
    rid = _pending_approval()
    client.post(f"/api/ws/approvals/{rid}/approve", headers=OWNER)
    r = client.post(f"/api/ws/approvals/{rid}/approve", headers=OWNER)
    assert r.status_code == 409


def test_reject_marks_rejected(client):
    rid = _pending_approval()
    assert client.post(f"/api/ws/approvals/{rid}/reject", headers=OWNER).status_code == 200
    assert client.get("/api/ws/approvals", headers=OWNER).json()[0]["status"] == "rejected"
    assert client.post(f"/api/ws/approvals/{rid}/approve", headers=OWNER).status_code == 409


def test_approval_endpoints_require_token(client):
    rid = _pending_approval()
    assert client.post(f"/api/ws/approvals/{rid}/approve").status_code == 401
    assert client.post(f"/api/ws/approvals/{rid}/reject").status_code == 401


def test_approval_not_found(client):
    assert client.post("/api/ws/approvals/9999/approve", headers=OWNER).status_code == 404


# --- memory -----------------------------------------------------------------------

def test_memory_save_list_delete(client):
    memory.save("brand_color", "royce uses orange and forest green", scope="project")
    listed = client.get("/api/ws/memory", headers=OWNER).json()
    assert any(m["key"] == "brand_color" for m in listed)
    assert client.get("/api/ws/memory?q=forest", headers=OWNER).json()
    assert client.get("/api/ws/memory?q=zzzz", headers=OWNER).json() == []
    assert client.delete("/api/ws/memory/brand_color", headers=OWNER).status_code == 200
    assert client.get("/api/ws/memory", headers=OWNER).json() == []
    assert client.delete("/api/ws/memory/brand_color", headers=OWNER).status_code == 404


def test_memory_delete_requires_token(client):
    memory.save("k1", "x", scope="project")
    assert client.delete("/api/ws/memory/k1").status_code == 401


# --- status ----------------------------------------------------------------------

def test_status_shape_and_no_keys(client, monkeypatch):
    from app.agent import validate
    monkeypatch.setattr(validate, "check_all", lambda **kw: {p: {"state": "not_configured", "detail": "", "checked_at": 0}
                                                              for p in validate.PROVIDERS})
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSECRETVALUE123456789012345678")
    r = client.get("/api/ws/status", headers=OWNER)
    assert r.status_code == 200
    body = r.json()
    assert set(body["providers"]) >= {"gemini", "openrouter"}
    assert "budget" in body and "used_this_hour" in body["budget"]
    assert "AIzaSECRET" not in r.text
