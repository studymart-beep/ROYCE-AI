import os, sys, time
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import tgstore


@pytest.fixture
def db(tmp_path):
    return str(tmp_path / "tgstore.db")


def test_insert_is_idempotent_on_group_and_message(db):
    assert tgstore.save_message(-100, 1, 7, "ada", "hello there", ts=1000, path=db) == "inserted"
    assert tgstore.save_message(-100, 1, 7, "ada", "hello there", ts=1000, path=db) == "duplicate"
    assert tgstore.count(-100, path=db) == 1


def test_same_message_id_in_other_group_is_separate(db):
    tgstore.save_message(-100, 1, 7, "ada", "hi", ts=1000, path=db)
    tgstore.save_message(-200, 1, 7, "ada", "hi", ts=1000, path=db)
    assert tgstore.count(path=db) == 2


def test_edit_updates_in_place(db):
    tgstore.save_message(-100, 5, 7, "ada", "buy now", ts=1000, path=db)
    assert tgstore.save_message(-100, 5, 7, "ada", "buy later", ts=1000, edited=True, path=db) == "edited"
    assert tgstore.count(-100, path=db) == 1
    rows = tgstore.search(-100, "later", path=db)
    assert len(rows) == 1 and rows[0]["edited"] == 1
    assert tgstore.search(-100, "now", path=db) == []


def test_non_edit_resend_does_not_overwrite(db):
    tgstore.save_message(-100, 5, 7, "ada", "first", ts=1000, path=db)
    assert tgstore.save_message(-100, 5, 7, "ada", "changed", ts=1000, path=db) == "duplicate"
    assert tgstore.search(-100, "first", path=db)


def test_empty_text_is_ignored(db):
    assert tgstore.save_message(-100, 9, 7, "ada", "   ", path=db) == "ignored"
    assert tgstore.count(path=db) == 0


def test_default_retention_is_720_hours(db):
    tgstore.save_message(-100, 1, 7, "ada", "x", ts=1000, path=db)
    assert tgstore.group_info(-100, path=db)["retention_hours"] == 720
    assert tgstore.group_info(-999, path=db)["retention_hours"] == 720  # unseen group gets default


def test_retention_cleanup_per_group(db):
    now = 10_000_000.0
    old = now - 800 * 3600      # older than 720h
    recent = now - 10 * 3600
    tgstore.save_message(-100, 1, 7, "ada", "old one", ts=old, path=db)
    tgstore.save_message(-100, 2, 7, "ada", "recent", ts=recent, path=db)
    tgstore.save_message(-200, 1, 7, "bo", "kept by longer window", ts=old, path=db)
    tgstore.set_retention(-200, 1000, path=db)
    deleted = tgstore.cleanup(now=now, path=db)
    assert deleted == {-100: 1}
    assert tgstore.count(-100, path=db) == 1
    assert tgstore.count(-200, path=db) == 1


def test_set_retention_rejects_bad_values(db):
    with pytest.raises(ValueError):
        tgstore.set_retention(-100, 0, path=db)
    assert tgstore.set_retention(-100, "48", path=db) == 48
    assert tgstore.group_info(-100, path=db)["retention_hours"] == 48


def test_search_is_literal_and_case_insensitive(db):
    tgstore.save_message(-100, 1, 7, "ada", "Price is 50% off", ts=1000, path=db)
    tgstore.save_message(-100, 2, 7, "bo", "price is 5000", ts=1001, path=db)
    hits = tgstore.search(-100, "50%", path=db)
    assert [h["message_id"] for h in hits] == [1]
    assert tgstore.search(-100, "PRICE", path=db)[0]["message_id"] == 2
    assert tgstore.search(-100, "   ", path=db) == []


def test_search_percent_and_underscore_are_not_wildcards(db):
    tgstore.save_message(-100, 1, 7, "ada", "abc", ts=1000, path=db)
    assert tgstore.search(-100, "%", path=db) == []
    assert tgstore.search(-100, "_", path=db) == []


def test_messages_since_and_delete_user(db):
    tgstore.save_message(-100, 1, 7, "ada", "a", ts=1000, path=db)
    tgstore.save_message(-100, 2, 8, "bo", "b", ts=2000, path=db)
    assert [m["text"] for m in tgstore.messages_since(-100, 1500, path=db)] == ["b"]
    assert tgstore.delete_user_messages(-100, 7, path=db) == 1
    assert tgstore.count(-100, path=db) == 1
    assert tgstore.delete_user_messages(-100, 7, path=db) == 0


def test_store_persists_across_connections(db):
    tgstore.save_message(-100, 1, 7, "ada", "persist me", ts=time.time(), path=db)
    assert tgstore.count(-100, path=db) == 1
