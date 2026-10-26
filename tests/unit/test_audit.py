import json
from app.agent import audit


def test_record_appends_one_line_with_fixed_fields(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit.record("owner", "task_created", "task:12", "ok", path=str(p))
    audit.record("bot", "task_completed", "task:12", "ok", path=str(p))
    lines = p.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert set(first) == {"ts", "actor", "action", "target", "outcome"}
    assert first["action"] == "task_created" and first["target"] == "task:12"


def test_append_keeps_existing_lines(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text('{"ts":"old","actor":"x","action":"a","target":"","outcome":"ok"}\n')
    audit.record("job", "backup_run", "crm-1.db", "ok", path=str(p))
    lines = p.read_text().splitlines()
    assert lines[0].startswith('{"ts":"old"')
    assert len(lines) == 2


def test_extra_fields_and_message_text_are_not_written(tmp_path):
    p = tmp_path / "audit.jsonl"
    # record() takes only fixed arguments; a message body cannot ride along
    audit.record("owner", "chat", "", "ok", path=str(p))
    raw = p.read_text()
    assert "message" not in raw and "text" not in raw.split('"action"')[0]


def test_secrets_are_redacted(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit.record("owner", "set_key", "api_key=AIzaSyDUMMYDUMMYDUMMYDUMMY123", "ok", path=str(p))
    raw = p.read_text()
    assert "AIzaSy" not in raw
    assert "[redacted]" in raw


def test_long_values_are_truncated_and_newlines_removed(tmp_path):
    p = tmp_path / "audit.jsonl"
    audit.record("owner", "a" * 500, "line1\nline2", "ok", path=str(p))
    e = json.loads(p.read_text().splitlines()[0])
    assert len(e["action"]) == audit.MAX_LEN
    assert "\n" not in e["target"]


def test_read_returns_last_events_and_skips_bad_lines(tmp_path):
    p = tmp_path / "audit.jsonl"
    for i in range(5):
        audit.record("job", f"step{i}", "", "ok", path=str(p))
    with open(p, "a") as f:
        f.write("not json\n")
    got = audit.read(path=str(p), limit=3)
    assert [e["action"] for e in got] == ["step2", "step3", "step4"]


def test_read_missing_file_is_empty(tmp_path):
    assert audit.read(path=str(tmp_path / "none.jsonl")) == []
