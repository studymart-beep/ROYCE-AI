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


def test_bot_conversations_are_keyed_per_bot(tmp_path, monkeypatch):
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "c.json"))
    a = conversations.append_exchange(None, "hook for earbuds", "r1", bot_id="bot-a")
    b = conversations.append_exchange(None, "hook for earbuds", "r2", bot_id="bot-b")
    assert a["bot_id"] == "bot-a" and b["bot_id"] == "bot-b" and a["id"] != b["id"]
    # a message to bot-b never lands in bot-a's conversation
    again = conversations.append_exchange(a["id"], "more", "r3", bot_id="bot-b")
    assert again["id"] != a["id"]
    assert [c["id"] for c in conversations.list_all(bot_id="bot-a")] == [a["id"]]
    assert {c["id"] for c in conversations.list_all(bot_id="bot-b")} == {b["id"], again["id"]}


def test_plain_list_excludes_bot_conversations_and_legacy_rows_count_as_plain(tmp_path, monkeypatch):
    import json
    path = tmp_path / "c.json"
    monkeypatch.setattr(conversations, "PATH", str(path))
    # legacy row written before bot_id existed
    path.write_text(json.dumps({"old": {"id": "old", "title": "legacy chat", "created_at": 1, "updated_at": 1,
                                        "messages": []}}))
    conversations.append_exchange(None, "to a bot", "r", bot_id="bot-a")
    titles = [c["title"] for c in conversations.list_all()]
    assert titles == ["legacy chat"]
    assert conversations.list_all(scope_all=True)  # both still exist in the store
