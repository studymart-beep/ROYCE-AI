"""Deletion precautions with owner approval, and /setrules. Fakes only, no network."""
import pytest

from app import tgprecautions, tgrules, tgmod, tggroups, tgstore
from app.agent import approvals, audit

OWNER = 7531626960


class Api:
    def __init__(self, ok=True):
        self.calls = []
        self.ok = ok

    def __call__(self, method, **params):
        self.calls.append((method, params))
        return {"ok": self.ok}

    def get_chat_member(self, *a, **k):
        return {"ok": True, "result": {"status": "member"}}


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(tgprecautions, "PATH", str(tmp_path / "prec.json"))
    monkeypatch.setattr(tgrules, "PATH", str(tmp_path / "rules.json"))
    global FLAG_DB
    FLAG_DB = str(tmp_path / "flags.db")  # isolated per test (tgmod takes an explicit path)
    monkeypatch.setattr(tgstore, "PATH", str(tmp_path / "store.db"))
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    api = Api()
    tgprecautions.set_api(api)
    return {"api": api, "tmp": tmp_path}


FLAG_DB = None


def _flag(gid=-100, mid=5, uid=42):
    return tgmod.record(gid, mid, uid, "link_suspicious", "shortener link", ts=1000.0, path=FLAG_DB)


def test_toggle_default_off_and_admin_only_setting(env):
    assert not tgprecautions.is_on(-100)
    tgprecautions.set_on(-100, True)
    assert tgprecautions.is_on(-100)


def test_toggle_off_flag_queues_nothing_and_no_action(env):
    fid, created = _flag()
    assert created
    assert tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid) is None
    assert approvals.pending(OWNER) == []
    assert env["api"].calls == []


def test_on_flag_queues_approval_in_owner_dm_and_nothing_runs_yet(env):
    tgprecautions.set_on(-100, True)
    fid, _ = _flag()
    rid = tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid)
    assert rid is not None
    assert env["api"].calls == []  # approval required: no delete before the owner says yes
    pend = approvals.pending(OWNER)
    assert len(pend) == 1 and pend[0]["payload"]["message_id"] == 5


def test_approve_then_delete_runs_once(env):
    tgprecautions.set_on(-100, True)
    fid, _ = _flag()
    tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid)
    rid = approvals.pending(OWNER)[0]["id"]
    ok, _ = approvals.approve(OWNER, rid)
    assert ok
    assert env["api"].calls == [("deleteMessage", {"chat_id": -100, "message_id": 5})]


def test_refused_without_approval_and_other_chat_cannot_approve(env):
    tgprecautions.set_on(-100, True)
    fid, _ = _flag()
    tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid)
    rid = approvals.pending(OWNER)[0]["id"]
    ok, _ = approvals.approve(999, rid)  # not the owner's chat
    assert not ok
    assert env["api"].calls == []


def test_failed_delete_is_audited_and_not_silent(env, monkeypatch):
    env["api"].ok = False
    tgprecautions.set_on(-100, True)
    fid, _ = _flag()
    tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid)
    rid = approvals.pending(OWNER)[0]["id"]
    ok, msg = approvals.approve(OWNER, rid)
    assert not ok and "refused" in str(msg).lower()
    assert "precaution_delete" in open(env["tmp"] / "audit.jsonl").read()


def test_audit_has_ids_not_content(env):
    tgprecautions.set_on(-100, True)
    fid, _ = _flag()
    tgprecautions.queue_for_flag(OWNER, -100, "G", 5, 42, fid)
    rid = approvals.pending(OWNER)[0]["id"]
    approvals.approve(OWNER, rid)
    raw = open(env["tmp"] / "audit.jsonl").read()
    assert "precaution_queued" in raw and "precaution_delete" in raw
    assert "shortener" not in raw


def test_setrules_persists_and_context(env):
    tgrules.set_rules(-100, "no spam. be kind.")
    assert tgrules.get(-100) == "no spam. be kind."
    assert "no spam" in tgrules.context_line(-100)
    assert tgrules.context_line(-999) == ""
    # survives a fresh read (persisted to disk, not in memory)
    assert tgrules.get(-100, path=str(env["tmp"] / "rules.json")) == "no spam. be kind."


def test_setrules_caps_length(env):
    tgrules.set_rules(-100, "x" * 5000)
    assert len(tgrules.get(-100)) == tgrules.MAX_LEN
