"""Library endpoints: tool toggles honored at dispatch, skills read/write, messaging toggles, usage."""
import os
import pytest
from fastapi.testclient import TestClient

from app.agent import audit, tools


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(tools, "TOGGLE_PATH", str(tmp_path / "tools_config.json"))
    monkeypatch.setattr(tools, "BACKUP_DIR", str(tmp_path / "backups"))
    db = tmp_path / "crm.db"
    db.write_bytes(b"x")
    monkeypatch.setattr(tools, "DB_PATH", str(db))
    return tmp_path


def test_every_tool_is_on_by_default(sandbox):
    assert all(row["enabled"] for row in tools.describe())


def test_switched_off_tool_is_refused_at_dispatch(sandbox, monkeypatch):
    called = []
    monkeypatch.setitem(tools.REGISTRY, "git_status", (lambda a: called.append(1) or {"ok": True}, ()))
    tools.set_enabled("git_status", False)
    with pytest.raises(tools.ToolError, match="switched off"):
        tools.run("git_status")
    assert called == []
    tools.set_enabled("git_status", True)
    assert tools.run("git_status") == {"ok": True}
    assert called == [1]


def test_toggle_persists_and_unknown_name_refused(sandbox):
    tools.set_enabled("db_backup", False)
    assert tools.enabled("db_backup") is False
    with pytest.raises(tools.ToolError):
        tools.set_enabled("rm_rf", False)


def test_toggle_endpoint_requires_owner_and_persists(sandbox, monkeypatch):
    from app.main import app
    monkeypatch.setenv("WORKSPACE_TOKEN", "tok-123")
    c = TestClient(app)
    assert c.get("/api/ws/tools").status_code == 401
    h = {"x-workspace-token": "tok-123"}
    r = c.put("/api/ws/tools/health_check", json={"enabled": False}, headers=h)
    assert r.status_code == 200 and r.json()["enabled"] is False
    rows = {t["name"]: t for t in c.get("/api/ws/tools", headers=h).json()["tools"]}
    assert rows["health_check"]["enabled"] is False
    assert c.put("/api/ws/tools/nope", json={"enabled": True}, headers=h).status_code == 404


def test_skill_save_writes_file_and_rejects_traversal(sandbox, monkeypatch, tmp_path):
    from app import skills
    sdir = tmp_path / "skills"
    sdir.mkdir()
    (sdir / "brand-strategy.md").write_text("---\nname: brand-strategy\ndescription: old\n---\nold body\n")
    monkeypatch.setattr(skills, "SKILLS_DIR", str(sdir))
    assert skills.save_skill("brand-strategy", "new body", "fresh desc") == "brand-strategy"
    text = (sdir / "brand-strategy.md").read_text()
    assert "description: fresh desc" in text and "new body" in text
    with pytest.raises(ValueError):
        skills.save_skill("../etc", "x")
    with pytest.raises(ValueError):
        skills.save_skill("missing-one", "x")


def test_usage_and_messaging_endpoints_need_owner(sandbox, monkeypatch):
    from app.main import app
    monkeypatch.setenv("WORKSPACE_TOKEN", "tok-123")
    c = TestClient(app)
    h = {"x-workspace-token": "tok-123"}
    assert c.get("/api/ws/usage").status_code == 401
    u = c.get("/api/ws/usage", headers=h)
    assert u.status_code == 200 and "remaining_this_hour" in u.json()
    assert c.get("/api/ws/messaging", headers=h).status_code == 200
    assert c.put("/api/ws/messaging/999", json={"precautions_on": True}, headers=h).status_code == 404
