"""Telegram is ops only: private chat from anyone but the owner gets a pointer, the owner gets ops commands
and no ai chat or generators. Group behaviour is covered elsewhere (test_tggroups) and must stay unchanged."""
import pytest

from app import telegram_bot as tb
from app import leadbrain


OWNER = leadbrain.OWNER_CHAT_ID
POINTER = "all ai chat lives in the desktop app or pwa. im on telegram for group ops and alerts only"


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(tb, "send", lambda cid, text: out.append((cid, text)))
    monkeypatch.setattr(tb, "api", lambda method, **kw: {"ok": True, "result": {}})
    monkeypatch.setattr(tb.audit, "record", lambda *a, **k: None)
    return out


def _private(text, sender):
    return {"update_id": 1, "message": {
        "message_id": 1, "chat": {"id": sender, "type": "private"},
        "from": {"id": sender, "first_name": "t"}, "text": text}}


def test_non_owner_private_text_gets_pointer_and_nothing_else(sent, monkeypatch):
    called = []
    monkeypatch.setattr(tb, "_handle_private_ops", lambda *a: called.append(a))
    tb.handle_update(_private("write me a script for noise earbuds", 999))
    assert sent == [(999, POINTER)]
    assert called == []


def test_non_owner_slash_script_gets_pointer(sent):
    tb.handle_update(_private("/script earbuds | lagos commuters | tiktok", 999))
    assert sent == [(999, POINTER)]


def test_owner_script_in_private_is_unknown_not_a_generator(sent, monkeypatch):
    # the generator must be gone, so /script falls through to the unknown-command reply
    monkeypatch.setattr(tb, "generate_script_package", lambda *a, **k: (_ for _ in ()).throw(AssertionError("called")), raising=False)
    tb.handle_update(_private("/script earbuds | lagos | tiktok", OWNER))
    assert len(sent) == 1
    cid, text = sent[0]
    assert cid == OWNER
    assert "unknown command" in text
    assert "desktop app" in text


def test_owner_generators_are_gone_in_private(sent):
    for cmd in ("/doctor", "/content hook", "/image a red shoe", "/voice hi"):
        sent.clear()
        tb.handle_update(_private(cmd, OWNER))
        assert len(sent) == 1, cmd
        assert "unknown command" in sent[0][1], cmd


def test_owner_free_text_is_pointer_not_ai_chat(sent, monkeypatch):
    monkeypatch.setattr(tb, "chat_text", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ai called")))
    tb.handle_update(_private("how are the ads doing", OWNER))
    assert sent == [(OWNER, POINTER)]


def test_owner_start_shows_ops_list_only(sent):
    tb.handle_update(_private("/start", OWNER))
    assert len(sent) == 1
    text = sent[0][1]
    for cmd in ("/leads", "/addlead", "/report", "/tasks", "/approve", "/deny", "/health", "/send"):
        if cmd == "/send":
            assert "tell group" in text
        else:
            assert cmd in text, cmd
    for gone in ("/script", "/doctor", "/content", "/image", "/voice"):
        assert gone not in text, gone


def test_owner_leads_and_report_still_work(sent, monkeypatch):
    monkeypatch.setattr(tb.db, "list_leads", lambda: [])
    monkeypatch.setattr(tb.db, "summary", lambda: {"total": 0, "by_status": {}, "overdue": []})
    tb.handle_update(_private("/leads", OWNER))
    tb.handle_update(_private("/report", OWNER))
    assert "no leads yet" in sent[0][1]
    assert "0 leads" in sent[1][1]


def test_group_update_still_routed_to_group_handler(monkeypatch):
    from app import tggroups
    seen = []
    monkeypatch.setattr(tggroups, "is_group", lambda chat: True)
    monkeypatch.setattr(tb, "handle_group_update", lambda msg, chat, edited: seen.append(chat))
    tb.handle_update({"message": {"chat": {"id": -100, "type": "supergroup"}, "text": "hi"}})
    assert seen and seen[0]["id"] == -100


def test_menu_button_reset_is_called_on_setup(monkeypatch):
    calls = []
    monkeypatch.setattr(tb, "get_env", lambda: None)
    monkeypatch.setattr(tb, "api", lambda method, **kw: calls.append((method, kw)) or {"result": {"username": "x"}})
    from app.agent import chatplan, jobs, audit
    monkeypatch.setattr(chatplan, "recover_on_start", lambda: 0)
    monkeypatch.setattr(jobs, "start", lambda: None)
    monkeypatch.setattr(audit, "record", lambda *a, **k: None)
    tb._setup_bot()
    assert ("setChatMenuButton", {"menu_button": {"type": "default"}}) in calls
