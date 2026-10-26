import json
import os
import time
import pytest
from app.agent import audit, jobs, tools


@pytest.fixture
def paths(tmp_path, monkeypatch):
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(tools, "BACKUP_DIR", str(tmp_path / "backups"))
    import sqlite3
    db = tmp_path / "crm.db"
    sqlite3.connect(str(db)).close()  # a real, empty sqlite file
    monkeypatch.setattr(tools, "DB_PATH", str(db))
    monkeypatch.setattr(jobs, "BASE", str(tmp_path))
    return tmp_path


def test_self_check_writes_latest_only(paths, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    h = paths / "logs" / "health.json"
    jobs.self_check(health_path=str(h))
    first = json.loads(h.read_text())
    jobs.self_check(health_path=str(h))
    second = json.loads(h.read_text())
    assert second["checked_at"] >= first["checked_at"]
    assert set(second["checks"]) == {"llm_providers", "db", "disk"}


def test_self_check_reports_db_missing(paths, monkeypatch):
    monkeypatch.setattr(tools, "DB_PATH", str(paths / "nope.db"))
    res = jobs.self_check(health_path=str(paths / "h.json"))
    assert res["checks"]["db"]["ok"] is False
    assert res["ok"] is False


def test_self_check_reports_no_provider(paths, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    res = jobs.self_check(health_path=str(paths / "h.json"))
    assert res["checks"]["llm_providers"]["ok"] is False


def test_health_file_has_no_secret_values(paths, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSySECRETVALUE1234567890")
    jobs.self_check(health_path=str(paths / "h.json"))
    assert "SECRETVALUE" not in (paths / "h.json").read_text()


def test_self_check_is_audited(paths):
    jobs.self_check(health_path=str(paths / "h.json"))
    assert any(e["action"] == "self_check" for e in audit.read())


def test_latest_none_when_missing(paths):
    assert jobs.latest(str(paths / "none.json")) is None


def test_latest_reads_back(paths):
    jobs.self_check(health_path=str(paths / "h.json"))
    assert jobs.latest(str(paths / "h.json"))["checks"]["db"]["ok"] is True


def test_run_backup_creates_file_and_audits(paths):
    out = jobs.run_backup()
    assert out and os.path.exists(out["file"])
    assert any(e["action"] == "backup_run" and e["outcome"] == "ok" for e in audit.read())


def test_run_backup_failure_is_audited_not_raised(paths, monkeypatch):
    monkeypatch.setattr(tools, "DB_PATH", str(paths / "gone.db"))
    assert jobs.run_backup() is None
    assert any(e["action"] == "backup_run" and e["outcome"].startswith("failed") for e in audit.read())


def test_start_runs_jobs_and_stop_ends_thread(paths, monkeypatch):
    calls = {"check": 0, "backup": 0}
    monkeypatch.setattr(jobs, "self_check", lambda *a, **k: calls.__setitem__("check", calls["check"] + 1))
    monkeypatch.setattr(jobs, "run_backup", lambda: calls.__setitem__("backup", calls["backup"] + 1))
    monkeypatch.setattr(jobs, "TICK", 0.01)
    t = jobs.start(backup_every=0, check_every=0)
    time.sleep(0.2)
    jobs.stop()
    t.join(timeout=2)
    assert not t.is_alive()
    assert calls["check"] >= 1 and calls["backup"] >= 1


def test_start_twice_returns_same_thread(paths, monkeypatch):
    monkeypatch.setattr(jobs, "TICK", 0.01)
    t1 = jobs.start(backup_every=10 ** 9, check_every=10 ** 9)
    t2 = jobs.start(backup_every=10 ** 9, check_every=10 ** 9)
    jobs.stop()
    t1.join(timeout=2)
    assert t1 is t2


def test_format_status_before_first_check_is_plain(paths, monkeypatch):
    monkeypatch.setattr(jobs, "latest", lambda *a, **k: None)
    out = jobs.format_status()
    assert "not run yet" in out
    assert "gemini" in out and "openrouter" in out
    assert "{" not in out


def test_format_status_shows_latest_result(paths):
    result = {"checked_at": "2026-10-26T02:00:00+00:00", "ok": True,
              "checks": {"db": {"ok": True, "detail": "reachable"},
                         "disk": {"ok": True, "detail": {"free_mb": 5000}}},
              "last_backup": "crm-x.db"}
    out = jobs.format_status(result)
    assert "all good" in out and "database: reachable" in out and "5000 MB" in out
    assert "crm-x.db" in out
