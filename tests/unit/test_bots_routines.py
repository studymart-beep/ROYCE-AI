"""Bots and routines: store CRUD, template backing, and the owner-gated endpoints."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TOKEN = "test-token-123"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", TOKEN)
    from app import bots, routines
    monkeypatch.setattr(bots, "PATH", str(tmp_path / "bots.json"))
    monkeypatch.setattr(routines, "PATH", str(tmp_path / "routines.json"))
    from app.workspace_api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, headers={"x-workspace-token": TOKEN})


def test_every_template_is_backed_by_a_real_skill_file():
    from app import bots, skills
    names = {s["name"] for s in skills.list_skills()}
    for t in bots.templates():
        assert t["key"] in names
        assert skills.get_skill(t["key"])
    assert len(bots.templates()) == 6  # the picker lists the 6 specialist templates; coordinator is reserved


def test_bot_create_list_delete(client):
    r = client.post("/api/ws/bots", json={"name": "  Ad   scripts  ", "template": "direct-response"})
    assert r.status_code == 200
    bot = r.json()
    assert bot["name"] == "Ad scripts" and bot["template"] == "direct-response"
    ids = [b["id"] for b in client.get("/api/ws/bots").json()["bots"]]
    assert ids[1:] == [bot["id"]]  # the Chief of Staff is always seeded and pinned first
    assert client.delete(f"/api/ws/bots/{bot['id']}").status_code == 200
    assert [b["template"] for b in client.get("/api/ws/bots").json()["bots"]] == ["coordinator"]
    assert client.delete(f"/api/ws/bots/{bot['id']}").status_code == 404


def test_bot_rejects_unknown_template_and_blank_name(client):
    assert client.post("/api/ws/bots", json={"name": "x", "template": "made-up"}).status_code == 400
    assert client.post("/api/ws/bots", json={"name": "   "}).status_code == 400


def test_bot_persona_pins_the_skill(client):
    from app import bots
    bot = bots.create("Closer", "negotiation-closing")
    block = bots.persona_block(bot)
    assert "Closer" in block and "playbook" in block.lower()
    assert bots.persona_block(bots.create("Plain")) .startswith('You are working')


def test_routine_crud_and_system_jobs_are_read_only_and_flagged(client):
    r = client.post("/api/ws/routines", json={"name": "Check NOISE retry", "schedule": "Nov 24 9am", "note": "retry ugc"})
    assert r.status_code == 200 and r.json()["runs_automatically"] is False
    body = client.get("/api/ws/routines").json()
    assert [u["name"] for u in body["user"]] == ["Check NOISE retry"]
    assert {j["id"] for j in body["system"]} == {"self-check", "backup"}
    rid = body["user"][0]["id"]
    assert client.delete(f"/api/ws/routines/{rid}").status_code == 200
    assert client.get("/api/ws/routines").json()["user"] == []


def test_bots_endpoints_require_owner(client):
    from fastapi.testclient import TestClient as TC
    anon = TC(client.app)
    assert anon.get("/api/ws/bots").status_code == 401
    assert anon.get("/api/ws/routines").status_code == 401
