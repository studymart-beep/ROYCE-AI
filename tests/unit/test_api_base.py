"""Optional API base override for a separate static host. Reads files and calls the app via TestClient.
No network, no provider calls. Default behaviour (no override) must stay same-origin."""
import os
import re

from fastapi.testclient import TestClient

from app import main

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "app", "static")


def _client():
    return TestClient(main.app)


def test_config_js_served_as_javascript_and_no_override_by_default():
    r = _client().get("/static/config.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]
    # the shipped placeholder sets no base: default stays same-origin
    assert "__ROYCE_API_BASE__ =" not in r.text.split("*/", 1)[-1]


def test_config_js_route_serves_file_when_present(tmp_path, monkeypatch):
    cfg = os.path.join(STATIC, "config.js")
    original = open(cfg, encoding="utf-8").read() if os.path.isfile(cfg) else None
    try:
        with open(cfg, "w", encoding="utf-8") as f:
            f.write('window.__ROYCE_API_BASE__ = "https://royce-ai.onrender.com";\n')
        r = _client().get("/static/config.js")
        assert r.status_code == 200
        assert 'window.__ROYCE_API_BASE__ = "https://royce-ai.onrender.com"' in r.text
    finally:
        if original is None:
            os.remove(cfg)
        else:
            with open(cfg, "w", encoding="utf-8") as f:
                f.write(original)


def test_pages_load_config_then_override_before_app_scripts(client=None):
    c = _client()
    for path, app_js in (("/workspace", "/static/workspace.js"), ("/m", "/static/mobile.js")):
        body = c.get(path).text
        i_cfg = body.index('src="/static/config.js"')
        i_api = body.index('src="/static/api-base.js"')
        i_app = body.index('src="' + app_js + '"')
        assert i_cfg < i_api < i_app, path


def test_api_base_script_accepts_only_plain_origin():
    src = open(os.path.join(STATIC, "api-base.js"), encoding="utf-8").read()
    assert 'var base = "";' in src  # default same-origin
    assert "https?:" in src  # only http(s) origins
    assert "*" not in re.findall(r'"\*"', src)


def test_frontend_uses_base_prefix_and_default_is_relative():
    ws = open(os.path.join(STATIC, "workspace.js"), encoding="utf-8").read()
    mb = open(os.path.join(STATIC, "mobile.js"), encoding="utf-8").read()
    assert 'fetch((window.ROYCE_API_BASE || "") + "/api/ws" + path' in ws
    assert 'var API = (window.ROYCE_API_BASE || "") + "/api/ws";' in mb
    assert 'fetch((window.ROYCE_API_BASE || "") + "/readyz"' in mb
    # no hardcoded absolute origins in the frontend
    for name in ("workspace.js", "mobile.js", "api-base.js", "sw.js"):
        text = open(os.path.join(STATIC, name), encoding="utf-8").read()
        assert "https://royce-ai" not in text, name


def test_cors_allows_only_listed_origin_and_never_credentials():
    # the vercel origin is allowed only when listed in ALLOWED_ORIGINS; '*' is dropped
    origins = main.allowed_origins("https://royce-test.vercel.app/, *, https://royce-ai.onrender.com")
    assert "https://royce-test.vercel.app" in origins
    assert "https://royce-ai.onrender.com" in origins
    assert "*" not in origins
    assert "https://evil.example" not in origins


def test_cors_live_preflight_reflects_listed_origin_only():
    c = _client()
    ok = c.options("/api/ws/status", headers={"Origin": "http://127.0.0.1:8000",
                                               "Access-Control-Request-Method": "GET"})
    assert ok.headers.get("access-control-allow-origin") == "http://127.0.0.1:8000"
    bad = c.options("/api/ws/status", headers={"Origin": "https://evil.example",
                                                "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in {k.lower() for k in bad.headers.keys()}
    assert bad.headers.get("access-control-allow-origin") != "*"
    assert "access-control-allow-credentials" not in {k.lower() for k in ok.headers.keys()}


def test_main_cors_config_has_no_wildcard_credentials():
    src = open(os.path.join(ROOT, "app", "main.py"), encoding="utf-8").read()
    assert "allow_credentials=False" in src
    assert 'allow_origins=["*"]' not in src
