import os
import pytest
from app.agent import tools, audit


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(tools, "BACKUP_DIR", str(tmp_path / "backups"))
    db = tmp_path / "crm.db"
    db.write_bytes(b"sqlite-bytes")
    monkeypatch.setattr(tools, "DB_PATH", str(db))
    return tmp_path


def test_unknown_tool_is_refused(sandbox):
    with pytest.raises(tools.ToolError, match="unknown tool"):
        tools.run("rm_rf", {})


def test_shell_text_is_never_a_tool(sandbox):
    with pytest.raises(tools.ToolError):
        tools.run("git_status; rm -rf /", {})


def test_unexpected_argument_is_refused(sandbox):
    with pytest.raises(tools.ToolError, match="unexpected"):
        tools.run("git_log", {"n": 3, "cmd": "rm -rf /"})


def test_run_tests_rejects_arguments(sandbox):
    with pytest.raises(tools.ToolError):
        tools.run("run_tests", {"args": "-k x"})


def test_git_log_range_validated(sandbox):
    with pytest.raises(tools.ToolError):
        tools.run("git_log", {"n": 0})
    with pytest.raises(tools.ToolError):
        tools.run("git_log", {"n": 51})
    with pytest.raises(tools.ToolError):
        tools.run("git_log", {"n": "5"})
    with pytest.raises(tools.ToolError):
        tools.run("git_log", {"n": True})


def test_git_diff_path_must_be_inside_repo():
    for bad in ["../secret", "/etc/passwd", "a/../../b", "x;rm", "a b"]:
        with pytest.raises(tools.ToolError):
            tools._check_path(bad)


def test_git_diff_accepts_plain_relative_path():
    assert tools._check_path("app/agent/tools.py") == "app/agent/tools.py"
    assert tools._check_path(None) is None


def test_health_check_port_validated(sandbox):
    with pytest.raises(tools.ToolError):
        tools.run("health_check", {"port": 0})
    with pytest.raises(tools.ToolError):
        tools.run("health_check", {"port": 70000})


def test_health_check_unreachable_reports_not_ok(sandbox):
    out = tools.run("health_check", {"port": 1})
    assert out["ok"] is False


def test_refused_call_is_audited(sandbox):
    with pytest.raises(tools.ToolError):
        tools.run("git_log", {"n": 999})
    events = audit.read(limit=5)
    assert events and events[-1]["action"] == "tool:git_log"
    assert events[-1]["outcome"].startswith("refused")


def test_successful_tool_call_is_audited(sandbox):
    tools.run("db_backup")
    actions = [e["action"] for e in audit.read(limit=10)]
    assert "tool:db_backup" in actions


def test_backup_copies_db_with_timestamp(sandbox):
    out = tools.run("db_backup")
    assert os.path.exists(out["file"])
    assert os.path.basename(out["file"]).startswith("crm-")
    with open(out["file"], "rb") as f:
        assert f.read() == b"sqlite-bytes"


def test_backup_rotation_keeps_newest_ten(sandbox):
    folder = sandbox / "backups"
    folder.mkdir()
    names = [f"crm-202601{i:02d}-000000-000000.db" for i in range(1, 13)]
    for n in names:
        (folder / n).write_bytes(b"x")
    (folder / "notes.txt").write_text("keep me")
    removed = tools.rotate_backups(str(folder), 10)
    left = sorted(p for p in os.listdir(folder) if p.startswith("crm-"))
    assert removed == names[:2]
    assert left == names[2:]
    assert (folder / "notes.txt").exists()


def test_backup_rotation_runs_on_each_backup(sandbox, monkeypatch):
    monkeypatch.setattr(tools, "BACKUP_KEEP", 3)
    for _ in range(5):
        tools.db_backup()
    count = len([p for p in os.listdir(sandbox / "backups") if p.startswith("crm-")])
    assert count == 3


def test_backup_fails_cleanly_without_db(sandbox, monkeypatch):
    monkeypatch.setattr(tools, "DB_PATH", str(sandbox / "missing.db"))
    with pytest.raises(tools.ToolError, match="not found"):
        tools.db_backup()


def test_git_status_runs_real_git():
    out = tools.run("git_status")
    assert "output" in out and isinstance(out["output"], str)
