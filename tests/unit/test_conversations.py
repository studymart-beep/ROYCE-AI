from app import conversations


def test_empty_store_lists_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    assert conversations.list_all() == []
    assert conversations.get("nope") is None


def test_exchange_creates_titled_conversation(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    conv = conversations.append_exchange(None, "  hook   for earbuds  ", "a reply")
    assert conv["title"] == "hook for earbuds"
    assert [m["role"] for m in conv["messages"]] == ["user", "assistant"]
    assert conversations.history(conv["id"]) == [{"role": "user", "content": "  hook   for earbuds  "},
                                                 {"role": "assistant", "content": "a reply"}]


def test_long_title_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    conv = conversations.append_exchange(None, "x" * 200, "r")
    assert len(conv["title"]) <= conversations.TITLE_MAX


def test_search_matches_title_and_message_text(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    conversations.append_exchange(None, "ad script for NOISE", "sure")
    conversations.append_exchange(None, "monthly report", "the lagos numbers")
    assert [c["title"] for c in conversations.list_all("noise")] == ["ad script for NOISE"]
    assert [c["title"] for c in conversations.list_all("lagos")] == ["monthly report"]
    assert len(conversations.list_all("zzz")) == 0


def test_unknown_conversation_id_creates_new(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    conv = conversations.append_exchange("missing", "hi", "hey")
    assert conv["id"] != "missing"
