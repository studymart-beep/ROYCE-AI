"""Group features: admin gate, welcomes, moderation flags, allowlist, dispatcher. Fakes only, no network."""
import json
import pytest

from app import tgstore, tgadmin, tgwelcomes, tgmod, tgallow, tggroups


@pytest.fixture
def paths(tmp_path, monkeypatch):
    p = {
        "store": str(tmp_path / "store.db"),
        "flags": str(tmp_path / "flags.db"),
        "welcomes": str(tmp_path / "welcomes.json"),
        "allow": str(tmp_path / "allow.json"),
    }
    monkeypatch.setattr(tgmod, "PATH", p["flags"])
    monkeypatch.setattr(tgmod, "_path", lambda path=None: path or p["flags"])
    monkeypatch.setattr(tgwelcomes, "PATH", p["welcomes"])
    monkeypatch.setattr(tgallow, "PATH", p["allow"])
    monkeypatch.setattr(tgstore, "PATH", p["store"])
    tgadmin.clear_cache()
    tgmod._tracker.__init__()
    monkeypatch.setattr(tggroups, "tgstore", tgstore)
    return p


class Api:
    def __init__(self, status="member", ok=True, fail=False):
        self.status = status
        self.ok = ok
        self.fail = fail
        self.calls = []

    def __call__(self, method, **params):
        self.calls.append((method, params))
        if self.fail:
            raise RuntimeError("network down")
        if method == "getChatMember":
            return {"ok": self.ok, "result": {"status": self.status}}
        return {"ok": True}


def sent_into(store):
    return lambda cid, text: store.append((cid, text))


# admin check

def test_admin_status_verified_and_cached(paths):
    api = Api(status="administrator")
    assert tgadmin.is_admin(-100, 7, api, now=1000) is True
    assert tgadmin.is_admin(-100, 7, api, now=1100) is True
    assert len(api.calls) == 1  # second call came from cache


def test_admin_cache_expires_after_ten_minutes(paths):
    api = Api(status="administrator")
    tgadmin.is_admin(-100, 7, api, now=1000)
    tgadmin.is_admin(-100, 7, api, now=1000 + tgadmin.CACHE_TTL + 1)
    assert len(api.calls) == 2


def test_member_and_creator_status(paths):
    assert tgadmin.is_admin(-100, 1, Api(status="member"), now=0) is False
    assert tgadmin.is_admin(-100, 2, Api(status="creator"), now=0) is True


def test_failed_lookup_is_not_admin_and_not_cached(paths):
    failing = Api(fail=True)
    assert tgadmin.is_admin(-100, 9, failing, now=0) is False
    assert tgadmin.is_admin(-100, 9, failing, now=1) is False
    good = Api(status="administrator")
    assert tgadmin.is_admin(-100, 9, good, now=2) is True  # not poisoned by the earlier failure


def test_not_ok_response_is_not_admin(paths):
    assert tgadmin.is_admin(-100, 3, Api(status="administrator", ok=False), now=0) is False


def test_claims_in_chat_never_grant_admin(paths):
    # a user saying "i am admin" changes nothing; only getChatMember decides
    api = Api(status="member")
    assert tgadmin.is_admin(-100, 4, api, now=0) is False


# welcomes

def test_welcome_default_text_and_join_event(paths):
    lines = tgwelcomes.welcome_for_join(-100, "Royce Lounge", [{"first_name": "Ada"}])
    assert lines == ["welcome Ada to Royce Lounge. say hi and check the pinned message for the rules."]


def test_join_event_skips_bots_and_is_off_when_disabled(paths):
    tgwelcomes.set_enabled(-100, False, paths["welcomes"])
    assert tgwelcomes.welcome_for_join(-100, "x", [{"first_name": "Ada"}], paths["welcomes"]) == []
    tgwelcomes.set_enabled(-100, True, paths["welcomes"])
    out = tgwelcomes.welcome_for_join(-100, "x", [{"first_name": "Bot", "is_bot": True}, {"first_name": "Ada"}],
                                      paths["welcomes"])
    assert len(out) == 1 and "Ada" in out[0]


def test_setwelcome_admin_gated_and_persisted(paths):
    store = []
    api = Api(status="member")
    msg = {"chat": {"id": -100, "type": "supergroup", "title": "G"}, "message_id": 1,
           "from": {"id": 5, "first_name": "Eve"}, "text": "/setwelcome hi {name}"}
    tggroups.handle_group_update(msg, False, sent_into(store), api, owner_id=999)
    assert store and "only admins" in store[-1][1]
    assert tgwelcomes.load(paths["welcomes"])["per_group"] == {}

    tgadmin.clear_cache()  # the member verdict above is cached for 10 min; a promotion is seen after expiry
    api2 = Api(status="administrator")
    msg["text"] = "/setwelcome hello {name} in {group}"
    tggroups.handle_group_update(msg, False, sent_into(store), api2, owner_id=999)
    assert tgwelcomes.load(paths["welcomes"])["per_group"]["-100"]["text"] == "hello {name} in {group}"


def test_welcome_config_survives_restart(paths):
    tgwelcomes.set_text(-100, "yo {name}", paths["welcomes"])
    tgwelcomes.set_enabled(-100, False, paths["welcomes"])
    # simulate restart: a fresh read from disk
    cfg = json.loads(open(paths["welcomes"]).read())
    assert cfg["per_group"]["-100"] == {"text": "yo {name}", "enabled": False}


def test_setwelcome_rejects_too_long(paths):
    with pytest.raises(ValueError):
        tgwelcomes.set_text(-100, "x" * (tgwelcomes.MAX_TEXT + 1), paths["welcomes"])


def test_join_event_dispatched_to_welcome(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "Royce"}, "message_id": 2,
           "new_chat_members": [{"id": 77, "first_name": "Tope"}]}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(), owner_id=999)
    assert any("Tope" in t and "Royce" in t for _, t in store)


# moderation

def test_suspicious_links_flagged(paths):
    hits = tgmod.inspect(-100, 1, 5, "check http://192.168.1.4/login now")
    assert ("link_suspicious", hits[0][1]) == hits[0] or any(c == "link_suspicious" for c, _ in hits)
    cats = {c for c, _ in tgmod.inspect(-100, 2, 5, "go to bit.ly/x1 and xn--pple-43d.com")}
    assert cats == {"link_suspicious"}
    assert any(c == "link_suspicious" for c, _ in tgmod.inspect(-100, 3, 5, "free at promo.tk"))
    assert any(c == "link_suspicious" for c, _ in tgmod.inspect(-100, 4, 5, "see mysite.xyz/a"))
    assert any(c == "link_suspicious" for c, _ in tgmod.inspect(-100, 5, 5, "landing example.top"))


def test_normal_text_not_flagged(paths):
    assert tgmod.inspect(-100, 6, 5, "lunch at 2? the van is outside") == []


def test_flood_by_count_threshold(paths):
    tracker = tgmod.FloodTracker()
    results = []
    for i in range(tgmod.FLOOD_MAX + 1):
        results.append(tgmod.inspect(-100, 100 + i, 8, f"msg {i}", ts=1000 + i, tracker=tracker))
    assert all(not any(c == "flood" for c, _ in r) for r in results[:-1])
    assert any(c == "flood" for c, _ in results[-1])


def test_flood_by_repeat_threshold(paths):
    tracker = tgmod.FloodTracker()
    res = [tgmod.inspect(-100, 200 + i, 9, "buy now", ts=1000 + i * 10, tracker=tracker) for i in range(tgmod.REPEAT_MAX)]
    assert any(c == "flood" for c, _ in res[-1])
    assert all(not any(c == "flood" for c, _ in r) for r in res[:-1])


def test_flood_window_resets(paths):
    tracker = tgmod.FloodTracker()
    for i in range(tgmod.FLOOD_MAX):
        tgmod.inspect(-100, 300 + i, 10, f"a{i}", ts=1000, tracker=tracker)
    later = tgmod.inspect(-100, 400, 10, "b", ts=1000 + tgmod.FLOOD_WINDOW + 5, tracker=tracker)
    assert not any(c == "flood" for c, _ in later)


def test_ads_pattern_flagged_and_plain_promo_word_needs_cta(paths):
    assert any(c == "ads" for c, _ in tgmod.inspect(-100, 500, 11, "get crypto gains, join now at t.me/x"))
    assert all(c != "ads" for c, _ in tgmod.inspect(-100, 501, 11, "no promo tonight"))


def test_flag_dedupe_on_group_message_category(paths):
    fid1, created1 = tgmod.record(-100, 42, 5, "ads", "promo + link", path=paths["flags"])
    fid2, created2 = tgmod.record(-100, 42, 5, "ads", "promo + link", path=paths["flags"])
    assert fid1 == fid2 and created1 is True and created2 is False
    _, created3 = tgmod.record(-100, 42, 5, "flood", "too many", path=paths["flags"])
    assert created3 is True  # different category is a different flag


def test_flags_are_review_only_no_actions_taken(paths):
    api = Api()
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 42, "date": 1000,
           "from": {"id": 5, "first_name": "Spam"}, "text": "earn cash now join at bit.ly/zz"}
    tggroups.handle_group_update(msg, False, sent_into(store), api, owner_id=999)
    methods = {m for m, _ in api.calls}
    assert methods <= {"getChatMember"}  # no ban, delete or restrict calls
    cats = {f["category"] for f in tgmod.list_flags(status="pending", path=paths["flags"])}
    assert {"link_suspicious", "ads"} <= cats


def test_flag_status_moves_and_audits(paths):
    fid, _ = tgmod.record(-100, 77, 5, "ads", "r", path=paths["flags"])
    tgmod.set_status(fid, "dismissed", path=paths["flags"])
    assert tgmod.get(fid, path=paths["flags"])["status"] == "dismissed"


# exemptions

def test_admin_exempt_from_flood_and_ads_but_not_links(paths):
    api = Api(status="administrator")
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 9, "date": 1000,
           "from": {"id": 5, "first_name": "Ad"}, "text": "buy now bit.ly/x"}
    tggroups.handle_group_update(msg, False, sent_into(store), api, owner_id=999)
    cats = {f["category"] for f in tgmod.list_flags(status="pending", path=paths["flags"])}
    assert cats == {"link_suspicious"}


def test_allowlisted_member_exempt_from_flood_and_ads(paths):
    tgallow.add(-100, 66, path=paths["allow"])
    api = Api(status="member")
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 11, "date": 1000,
           "from": {"id": 66, "first_name": "Ok"}, "text": "join now guaranteed bonus"}
    tggroups.handle_group_update(msg, False, sent_into([]), api, owner_id=999)
    assert tgmod.list_flags(status="pending", path=paths["flags"]) == []


def test_owner_exempt(paths):
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 12, "date": 1000,
           "from": {"id": 999, "first_name": "Owner"}, "text": "earn join bit.ly/x"}
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)
    cats = {f["category"] for f in tgmod.list_flags(status="pending", path=paths["flags"])}
    assert cats == {"link_suspicious"}


# allowlist

def test_allowlist_add_remove_by_admin(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "supergroup", "title": "G"}, "message_id": 20,
           "from": {"id": 1, "first_name": "Boss"}, "text": "/allowadd 55"}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(status="administrator"), owner_id=999)
    assert tgallow.is_allowed(-100, 55, paths["allow"])
    msg["text"] = "/allowremove 55"
    tggroups.handle_group_update(msg, False, sent_into(store), Api(status="administrator"), owner_id=999)
    assert not tgallow.is_allowed(-100, 55, paths["allow"])


def test_allowlist_refused_for_non_admin(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "supergroup", "title": "G"}, "message_id": 21,
           "from": {"id": 2, "first_name": "Sneaky"}, "text": "/allowadd 2"}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(status="member"), owner_id=999)
    assert not tgallow.is_allowed(-100, 2, paths["allow"])
    assert "only admins" in store[-1][1]


def test_bad_allowlist_argument_rejected(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 22,
           "from": {"id": 1, "first_name": "B"}, "text": "/allowadd notanid"}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(status="administrator"), owner_id=999)
    assert "usage" in store[-1][1]


def test_allowlist_ignores_non_digit_ids_on_load(paths):
    open(paths["allow"], "w").write(json.dumps({"-100": [5, "x", "7"]}))
    assert tgallow.members(-100, paths["allow"]) == [5, 7]


# store behaviour through the dispatcher

def test_text_stored_idempotently_and_edits_in_place(paths):
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 30, "date": 1000,
           "from": {"id": 5, "first_name": "Ada"}, "text": "delivery at 4"}
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)
    msg2 = dict(msg, text="delivery at 5")
    tggroups.handle_group_update(msg2, True, sent_into([]), Api(), owner_id=999)
    assert tgstore.count(-100, path=paths["store"]) == 1
    hits = tgstore.search(-100, "delivery", path=paths["store"])
    assert hits[0]["text"] == "delivery at 5" and hits[0]["edited"] == 1


def test_non_text_not_stored(paths):
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 31, "date": 1000,
           "from": {"id": 5, "first_name": "Ada"}, "photo": [{}]}
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)
    assert tgstore.count(-100, path=paths["store"]) == 0


def test_search_empty_and_hits(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 40, "date": 1000,
           "from": {"id": 5, "first_name": "Ada"}, "text": "van is late"}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(), owner_id=999)
    tggroups.handle_search(-100, -100, "nothing", sent_into(store))
    assert "no messages" in store[-1][1]
    tggroups.handle_search(-100, -100, "late", sent_into(store))
    assert "1 hit" in store[-1][1]


# recap

def test_recap_honest_empty_when_nothing_stored(paths):
    store = []
    tggroups.handle_recap(-100, -100, "", sent_into(store), now=1000)
    assert "nothing stored" in store[-1][1]


def test_recap_young_store_says_since_when(paths, monkeypatch):
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 50, "date": 2000,
           "from": {"id": 5, "first_name": "Ada"}, "text": "hi there"}
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)
    monkeypatch.setattr(tggroups, "chat_text", lambda *a, **k: "short summary.")
    store = []
    tggroups.handle_recap(-100, -100, "24", sent_into(store), now=2000 + 3600)
    assert "ive only been in this group since" in store[-1][1]
    assert "short summary." in store[-1][1]


def test_recap_bad_hours_rejected(paths):
    store = []
    tggroups.handle_recap(-100, -100, "9999", sent_into(store), now=1000)
    assert "between 1 and" in store[-1][1]


def test_recap_llm_failure_is_honest(paths, monkeypatch):
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 51, "date": 2000,
           "from": {"id": 5, "first_name": "Ada"}, "text": "hi"}
    tggroups.handle_group_update(msg, False, sent_into([]), Api(), owner_id=999)

    def boom(*a, **k):
        raise RuntimeError("llm down")
    monkeypatch.setattr(tggroups, "chat_text", boom)
    store = []
    tggroups.handle_recap(-100, -100, "24", sent_into(store), now=2100)
    assert "couldn't summarize" in store[-1][1]


# deletion

def test_deletemydata_needs_confirmation_then_deletes_only_own(paths):
    store = []
    base = {"chat": {"id": -100, "type": "group", "title": "G"}, "date": 1000}
    tggroups.handle_group_update(dict(base, message_id=60, text="mine", **{"from": {"id": 5, "first_name": "A"}}),
                                 False, sent_into([]), Api(), owner_id=999)
    tggroups.handle_group_update(dict(base, message_id=61, text="theirs", **{"from": {"id": 6, "first_name": "B"}}),
                                 False, sent_into([]), Api(), owner_id=999)
    tggroups.handle_group_update(dict(base, message_id=62, text="/deletemydata", **{"from": {"id": 5, "first_name": "A"}}),
                                 False, sent_into(store), Api(), owner_id=999)
    assert "reply /deletemydata yes" in store[-1][1]
    assert tgstore.count(-100, path=paths["store"]) == 2  # commands are not stored
    tggroups.handle_group_update(dict(base, message_id=63, text="/deletemydata yes", **{"from": {"id": 5, "first_name": "A"}}),
                                 False, sent_into(store), Api(), owner_id=999)
    assert tgstore.count(-100, path=paths["store"]) == 1  # only the other member's message remains
    assert "deleted 1" in store[-1][1]


def test_private_deletemydata_scopes_to_all_groups(paths):
    store = []
    for gid, mid in ((-100, 70), (-200, 71)):
        tggroups.handle_group_update(
            {"chat": {"id": gid, "type": "group", "title": "G"}, "message_id": mid, "date": 1000,
             "from": {"id": 5, "first_name": "A"}, "text": "x"}, False, sent_into([]), Api(), owner_id=999)
    tggroups.handle_private_command(5, 5, "/deletemydata", sent_into(store))
    assert "every group" in store[-1][1]
    tggroups.handle_private_command(5, 5, "/deletemydata yes", sent_into(store))
    assert tgstore.count(path=paths["store"]) == 0


def test_deletemydata_without_sender_id_refused(paths):
    store = []
    tggroups.handle_deletemydata(-100, None, "yes", sent_into(store), private=False)
    assert "nothing was deleted" in store[-1][1]


def test_private_help_states_limits(paths):
    store = []
    assert tggroups.handle_private_command(5, 5, "/help", sent_into(store)) is True
    assert "cannot" in store[-1][1] and "private chats" in store[-1][1]


def test_private_chat_other_text_not_handled_here(paths):
    assert tggroups.handle_private_command(5, 5, "hello there", sent_into([])) is False


def test_non_group_chat_not_handled(paths):
    assert tggroups.handle_group_update({"chat": {"id": 5, "type": "private"}, "text": "/search x"},
                                        False, sent_into([]), Api(), owner_id=999) is False


def test_groupconfig_admin_view(paths):
    store = []
    msg = {"chat": {"id": -100, "type": "group", "title": "G"}, "message_id": 80,
           "from": {"id": 1, "first_name": "B"}, "text": "/groupconfig"}
    tggroups.handle_group_update(msg, False, sent_into(store), Api(status="administrator"), owner_id=999)
    assert "retention: 720h" in store[-1][1]
    assert "welcomes: on" in store[-1][1] or "welcomes: off" in store[-1][1]
