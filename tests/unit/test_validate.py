import socket
import urllib.error
import pytest
from app.agent import validate, router, jobs

SECRET = "AIzaSyTESTSECRETVALUE_123456"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    validate.reset()
    router.reset_health()
    yield
    validate.reset()
    router.reset_health()


def http(code):
    return urllib.error.HTTPError("http://x", code, "err", None, None)


def test_missing_key_is_not_configured_and_makes_no_call(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    calls = []
    res = validate.check("gemini", opener=lambda p, k: calls.append(p) or 200, force=True)
    assert res["state"] == "not_configured"
    assert calls == []


def test_valid_key_is_validated(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    res = validate.check("gemini", opener=lambda p, k: 200, force=True)
    assert res["state"] == "validated"
    assert router.is_healthy("gemini") is True


def test_invalid_creds_are_invalid_and_skipped_by_router(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    def opener(p, k):
        raise http(401)
    res = validate.check("gemini", opener=opener, force=True)
    assert res["state"] == "invalid"
    assert router.health("gemini")["invalid"] is True
    assert router.is_healthy("gemini") is False


def test_rate_limit_is_unavailable_not_invalid(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    def opener(p, k):
        raise http(429)
    res = validate.check("gemini", opener=opener, force=True)
    assert res["state"] == "unavailable"
    assert "not proven invalid" in res["detail"]
    assert router.health("gemini").get("invalid") in (False, None)


def test_timeout_is_unavailable_not_invalid(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET)
    def opener(p, k):
        raise socket.timeout("timed out")
    res = validate.check("openrouter", opener=opener, force=True)
    assert res["state"] == "unavailable"
    assert router.health("openrouter").get("invalid") in (False, None)


def test_outage_5xx_is_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    def opener(p, k):
        raise http(503)
    assert validate.check("gemini", opener=opener, force=True)["state"] == "unavailable"


def test_network_error_is_unavailable(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    def opener(p, k):
        raise urllib.error.URLError("dns failure")
    assert validate.check("gemini", opener=opener, force=True)["state"] == "unavailable"


def test_result_is_cached_for_ttl(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    calls = []
    def opener(p, k):
        calls.append(1)
        return 200
    validate.check("gemini", now=1000.0, opener=opener)
    validate.check("gemini", now=1001.0, opener=opener)
    assert len(calls) == 1
    validate.check("gemini", now=1000.0 + validate.CACHE_TTL + 1, opener=opener)
    assert len(calls) == 2


def test_cache_does_not_outlive_removed_key(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    validate.check("gemini", now=1000.0, opener=lambda p, k: 200)
    monkeypatch.delenv("GEMINI_API_KEY")
    assert validate.check("gemini", now=1001.0, opener=lambda p, k: 200)["state"] == "not_configured"


def test_keys_never_appear_in_health_output(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", SECRET)
    monkeypatch.setenv("OPENROUTER_API_KEY", SECRET + "x")
    def opener(p, k):
        raise http(401)
    monkeypatch.setattr(validate, "_request", opener)
    text = jobs.format_status(result=None, probe=lambda: validate.check_all(force=True))
    assert SECRET not in text and "TESTSECRET" not in text
    assert "invalid" in text


def test_health_shows_every_state(monkeypatch):
    fake = {
        "gemini": {"state": "validated", "detail": validate.STATE_TEXT["validated"]},
        "openrouter": {"state": "unavailable", "detail": validate.STATE_TEXT["unavailable"]},
    }
    text = jobs.format_status(result=None, probe=lambda: fake)
    assert "validated (live check passed)" in text
    assert "unavailable" in text
    assert "configured, healthy" not in text  # the old overstated claim is gone
