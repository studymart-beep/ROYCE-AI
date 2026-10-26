"""Owner-approved sends into groups and channels. Fakes only, no network."""
import pytest

from app import tgsend, tgstore
from app.agent import approvals, audit

OWNER = 7531626960


@pytest.fixture
def env(tmp_path, monkeypatch):
    sp = str(tmp_path / "store.db")
    ap = str(tmp_path / "approvals.json")
    al = str(tmp_path / "audit.jsonl")
    monkeypatch.setattr(tgstore, "PATH", sp)
    monkeypatch.setattr(approvals, "PATH", ap)
    monkeypatch.setattr(audit, "PATH", al)
    sent = []
    tgsend.set_sender(lambda cid, text: (sent.append((cid, text)), {"ok": True})[1])
    # a stored group and a channel the bot is in
    tgstore.set_chat_info(-100, "Royce Team", "supergroup", path=sp)
    tgstore.set_chat_info(-200, "Royce Updates", "channel", path=sp)
    return {"store": sp, "sent": sent, "audit": al}


def test_parse_forms():
    assert tgsend.parse("tell the group Royce Team: hello all") == ("group", "Royce Team", "hello all")
    assert tgsend.parse("post to the channel Royce Updates: drop friday") == ("channel", "Royce Updates", "drop friday")
    assert tgsend.parse("post this to the channel: x") is None
    assert tgsend.parse("hello there") is None


def test_queue_shows_exact_text_and_sends_nothing_until_approved(env):
    ok, reply = tgsend.queue_send(OWNER, "group", "Royce Team", "hello all", path=env["store"])
    assert ok and "hello all" in reply and "/approve" in reply
    assert env["sent"] == []  # approval required: nothing sent yet
    pend = approvals.pending(OWNER)
    assert len(pend) == 1 and pend[0]["payload"] == {"group_id": -100, "text": "hello all"}


def test_approve_sends_to_stored_chat_id_only(env):
    tgsend.queue_send(OWNER, "channel", "Royce Updates", "drop friday", path=env["store"])
    rid = approvals.pending(OWNER)[0]["id"]
    ok, msg = approvals.approve(OWNER, rid)
    assert ok, msg
    assert env["sent"] == [(-200, "drop friday")]


def test_refusal_without_approval_and_other_chat_cannot_approve(env):
    tgsend.queue_send(OWNER, "group", "Royce Team", "hello", path=env["store"])
    rid = approvals.pending(OWNER)[0]["id"]
    ok, _ = approvals.approve(12345, rid)  # a different chat
    assert not ok and env["sent"] == []


def test_unknown_or_ambiguous_target_refused(env):
    ok, reply = tgsend.queue_send(OWNER, "group", "Nobody", "hi", path=env["store"])
    assert not ok and "no group named" in reply
    assert approvals.pending(OWNER) == []


def test_audit_logs_ids_not_content(env):
    tgsend.queue_send(OWNER, "group", "Royce Team", "secret launch text", path=env["store"])
    rid = approvals.pending(OWNER)[0]["id"]
    approvals.approve(OWNER, rid)
    raw = open(env["audit"]).read()
    assert "secret launch text" not in raw
    assert "send_queued" in raw and "group_send" in raw


def test_empty_or_long_text_refused(env):
    ok, _ = tgsend.queue_send(OWNER, "group", "Royce Team", "", path=env["store"])
    assert not ok
    ok, _ = tgsend.queue_send(OWNER, "group", "Royce Team", "x" * 1001, path=env["store"])
    assert not ok
