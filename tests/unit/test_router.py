import pytest
from app.agent import router


@pytest.fixture(autouse=True)
def clean():
    router.reset_health()
    yield
    router.reset_health()


def both(p):
    return True


def test_gemini_primary_when_both_configured():
    assert router.pick_order("chat", "gemini", configured=both) == ["gemini", "openrouter"]


def test_preferred_openrouter_goes_first():
    assert router.pick_order("chat", "openrouter", configured=both) == ["openrouter", "gemini"]


def test_unconfigured_provider_is_skipped():
    assert router.pick_order("chat", "gemini", configured=lambda p: p == "openrouter") == ["openrouter"]


def test_no_provider_configured_gives_empty_order():
    assert router.pick_order("chat", "gemini", configured=lambda p: False) == []


def test_call_fails_over_to_second_provider():
    calls = []
    def fn(pid):
        calls.append(pid)
        if pid == "gemini":
            raise RuntimeError("503 overloaded")
        return "hello from openrouter"
    out = router.call(fn, capability="chat", preferred="gemini", configured=both, log=lambda *a: None)
    assert out == "hello from openrouter"
    assert calls == ["gemini", "openrouter"]


def test_failed_provider_is_demoted_after_error():
    router.record_error("gemini", "boom", now=1000.0)
    assert router.is_healthy("gemini", now=1010.0) is False
    order = router.pick_order("chat", "gemini", configured=both, now=1010.0)
    assert order == ["openrouter", "gemini"]


def test_demoted_provider_recovers_after_cooldown():
    router.record_error("gemini", "boom", now=1000.0)
    assert router.is_healthy("gemini", now=1000.0 + router.COOLDOWN + 1) is True


def test_success_after_error_restores_health():
    router.record_error("gemini", "boom", now=1000.0)
    router.record_ok("gemini", now=1005.0)
    assert router.is_healthy("gemini", now=1006.0) is True


def test_all_providers_fail_raises_with_last_error():
    def fn(pid):
        raise RuntimeError(f"{pid} down")
    with pytest.raises(RuntimeError) as e:
        router.call(fn, capability="chat", preferred="gemini", configured=both, log=lambda *a: None)
    assert "all providers failed" in str(e.value)
    assert "openrouter down" in str(e.value)


def test_no_provider_raises_clear_error():
    with pytest.raises(RuntimeError, match="no llm provider configured"):
        router.call(lambda pid: "x", capability="chat", configured=lambda p: False, log=lambda *a: None)


def test_vision_capability_is_routed():
    assert router.pick_order("vision", "gemini", configured=both) == ["gemini", "openrouter"]


def test_health_error_text_is_truncated():
    router.record_error("gemini", "x" * 1000, now=1.0)
    assert len(router.health("gemini")["last_error"]) <= 200
