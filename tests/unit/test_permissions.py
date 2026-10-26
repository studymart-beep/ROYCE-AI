import pytest
from app.agent import permissions, approvals, memory, budget


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(approvals, "_EXECUTORS", {})
    return tmp_path


def test_chat_and_generate_need_no_approval():
    assert permissions.needs_approval("chat") is False
    assert permissions.needs_approval("generate") is False


def test_send_publish_delete_need_approval():
    for c in ("send_message", "publish", "delete"):
        assert permissions.needs_approval(c) is True


def test_unknown_class_defaults_to_approval():
    assert permissions.level("something_new") == "approval"


def test_auto_actions_cannot_be_queued(store):
    with pytest.raises(permissions.PermissionError_):
        approvals.queue(1, "chat", {"x": 1})


def test_send_is_queued_and_not_executed_until_approved(store):
    ran = []
    approvals.register_executor("send_message", lambda p: ran.append(p) or "sent")
    rec = approvals.queue(42, "send_message", {"to": "a"}, summary="send to a")
    assert ran == []
    assert [r["id"] for r in approvals.pending(42)] == [rec["id"]]
    ok, msg = approvals.approve(42, rec["id"])
    assert ok is True and ran == [{"to": "a"}]
    assert approvals.pending(42) == []


def test_other_chat_cannot_approve(store):
    ran = []
    approvals.register_executor("send_message", lambda p: ran.append(p))
    rec = approvals.queue(42, "send_message", {"to": "a"})
    ok, msg = approvals.approve(99, rec["id"])
    assert ok is False and ran == []


def test_approve_twice_does_not_run_twice(store):
    ran = []
    approvals.register_executor("send_message", lambda p: ran.append(p))
    rec = approvals.queue(42, "send_message", {"to": "a"})
    approvals.approve(42, rec["id"])
    ok, msg = approvals.approve(42, rec["id"])
    assert ok is False and len(ran) == 1


def test_no_executor_refuses_and_says_nothing_sent(store):
    rec = approvals.queue(42, "publish", {"post": "x"})
    ok, msg = approvals.approve(42, rec["id"])
    assert ok is False and "nothing was sent" in msg


def test_failing_executor_marks_failed(store):
    def boom(p):
        raise RuntimeError("telegram 500")
    approvals.register_executor("delete", boom)
    rec = approvals.queue(42, "delete", {"id": 1})
    ok, msg = approvals.approve(42, rec["id"])
    assert ok is False and "failed" in msg


def test_reject_removes_from_pending(store):
    rec = approvals.queue(42, "publish", {"post": "x"})
    assert approvals.reject(42, rec["id"]) is True
    assert approvals.pending(42) == []


@pytest.fixture
def mem(tmp_path, monkeypatch):
    monkeypatch.setattr(memory, "PATH", str(tmp_path / "memory.json"))
    return tmp_path


def test_memory_save_and_search_ranks_best_match(mem):
    memory.save("client-noise", "client NOISE is an earbuds brand for Lagos commuters")
    memory.save("tone", "owner prefers short lowercase replies")
    memory.save("cpc-target", "CPC target is 150 naira")
    hits = memory.search("noise earbuds")
    assert hits[0]["key"] == "client-noise"
    assert all(h["key"] != "tone" for h in hits)


def test_key_match_outweighs_text_match(mem):
    memory.save("noise", "a brand note")
    memory.save("other", "noise appears here in text only")
    assert memory.search("noise")[0]["key"] == "noise"


def test_memory_forget(mem):
    memory.save("k1", "some fact")
    assert memory.forget("k1") is True
    assert memory.search("fact") == []
    assert memory.forget("k1") is False


def test_chat_scope_search_excludes_other_chats(mem):
    memory.save("a", "secret pricing for chat one", scope="chat:1")
    memory.save("b", "pricing for chat two", scope="chat:2")
    hits = memory.search("pricing", scope="chat:2")
    assert [h["key"] for h in hits] == ["b"]


def test_memory_rejects_bad_scope(mem):
    with pytest.raises(ValueError):
        memory.save("k", "text", scope="everyone")


def test_detect_only_explicit_statements(mem):
    assert memory.detect("how are the ads doing") == []
    assert memory.detect("remember that our CPC target is 150 naira")[0][1] == "our CPC target is 150 naira"
    assert memory.detect("my client NOISE is an earbuds brand")[0][0] == "client-noise"
    assert memory.detect("we use Canva for designs")[0][0].startswith("tool-")


def test_learn_saves_and_block_is_returned(mem):
    memory.learn("remember that we price reels at 80k")
    blk = memory.block("reels price")
    assert "reels at 80k" in blk


def test_budget_caps_at_thirty_per_hour(monkeypatch):
    budget.reset()
    for _ in range(30):
        budget.consume(now=1000.0, chat_id=7)
    with pytest.raises(budget.BudgetExceeded):
        budget.consume(now=1001.0, chat_id=7)
    budget.consume(now=1000.0, chat_id=8)  # other chats unaffected
    budget.consume(now=1000.0 + 3601, chat_id=7)  # window rolled over
    budget.reset()
