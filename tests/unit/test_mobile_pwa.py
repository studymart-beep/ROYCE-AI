"""Mobile PWA, service worker, CORS and auth wiring. These tests only read files and call the app
through TestClient. No network, no real provider calls."""
import json
import os
import re

import pytest
from fastapi.testclient import TestClient

from app import main

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
STATIC = os.path.join(ROOT, "app", "static")


@pytest.fixture()
def client():
    return TestClient(main.app)


def test_mobile_route_serves_app_shell(client):
    r = client.get("/m")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    body = r.text
    for marker in ('id="composer"', 'class="tabbar"', 'data-tab="chats"', 'data-tab="tasks"',
                   'data-tab="approvals"', 'data-tab="more"', "/static/mobile.js", "/static/mobile.css",
                   'viewport-fit=cover', 'rel="manifest"'):
        assert marker in body, marker


def test_mobile_assets_exist_and_are_served(client):
    for name in ("mobile.js", "mobile.css", "offline.html", "sw.js"):
        assert os.path.isfile(os.path.join(STATIC, name)), name
    assert client.get("/static/mobile.js").status_code == 200
    assert client.get("/static/mobile.css").status_code == 200


def test_mobile_js_uses_token_header_and_never_innerhtml():
    src = open(os.path.join(STATIC, "mobile.js"), encoding="utf-8").read()
    assert "x-workspace-token" in src
    assert ".innerHTML" not in src and "insertAdjacentHTML" not in src  # rendering stays textContent-only
    assert "/api/ws" in src


def test_manifest_standalone_with_icons(client):
    r = client.get("/static/manifest.json")
    assert r.status_code == 200
    m = r.json()
    assert m["name"] == "Royce AI"
    assert m["display"] == "standalone"
    assert m["theme_color"] == "#0D3B2E"
    assert m["background_color"] == "#F7F3E9"
    purposes = {(i["sizes"], i.get("purpose")) for i in m["icons"]}
    assert ("192x192", "any") in purposes
    assert ("512x512", "any") in purposes
    assert ("192x192", "maskable") in purposes
    assert ("512x512", "maskable") in purposes
    for icon in m["icons"]:
        assert os.path.isfile(os.path.join(ROOT, "app", icon["src"].lstrip("/"))), icon["src"]


def test_service_worker_served_at_root_with_scope_header(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.headers["service-worker-allowed"] == "/"
    assert "javascript" in r.headers["content-type"]


def test_service_worker_never_caches_api_routes():
    src = open(os.path.join(STATIC, "sw.js"), encoding="utf-8").read()
    shell = re.search(r"const SHELL = \[(.*?)\];", src, re.S)
    assert shell, "SHELL list missing"
    entries = re.findall(r'"([^"]+)"', shell.group(1))
    assert entries, "SHELL is empty"
    assert not any(e.startswith("/api") for e in entries), entries
    # /api is network-only: the fetch handler returns before the shell check
    assert 'url.pathname.startsWith("/api/")' in src
    assert "offline" in src and "you're offline" in src
    # the cache must never store API responses: only shell paths are put()
    assert "SHELL.includes(url.pathname)" in src


def test_offline_page_is_honest(client):
    r = client.get("/offline")
    assert r.status_code == 200
    assert "you're offline" in r.text
    assert "AI needs a connection" in r.text


def test_workspace_links_to_mobile(client):
    r = client.get("/workspace")
    assert r.status_code == 200
    assert 'href="/m"' in r.text


def test_cors_allowed_origin_passes(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://royce-ai.example.com")
    fresh = importlib_reload_main()
    c = TestClient(fresh.app)
    r = c.get("/healthz", headers={"Origin": "https://royce-ai.example.com"})
    assert r.headers.get("access-control-allow-origin") == "https://royce-ai.example.com"
    assert "access-control-allow-credentials" not in r.headers


def test_cors_disallowed_origin_blocked(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://royce-ai.example.com")
    fresh = importlib_reload_main()
    c = TestClient(fresh.app)
    r = c.get("/healthz", headers={"Origin": "https://evil.example.org"})
    assert "access-control-allow-origin" not in r.headers


def test_allowed_origins_parsing_never_wildcard(monkeypatch):
    from app.main import allowed_origins
    assert "*" not in allowed_origins("*")
    assert allowed_origins("*, https://a.example.com/ , ,https://b.example.com") == [
        "https://a.example.com", "https://b.example.com"]
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    defaults = allowed_origins()
    assert "http://localhost:8000" in defaults and "*" not in defaults


def test_cors_default_allows_localhost(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    fresh = importlib_reload_main()
    c = TestClient(fresh.app)
    r = c.get("/healthz", headers={"Origin": "http://localhost:8000"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:8000"


def test_chat_requires_token(client, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", "test-owner-token")
    r = client.post("/api/ws/chat", json={"message": "hi"})
    assert r.status_code == 401
    r = client.post("/api/ws/chat", json={"message": "hi"}, headers={"x-workspace-token": "wrong"})
    assert r.status_code == 401


def test_status_endpoint_requires_token(client, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", "test-owner-token")
    assert client.get("/api/ws/conversations").status_code == 401


def importlib_reload_main():
    import importlib
    return importlib.reload(main)


def test_mobile_api_paths_are_prefixed_with_api_ws():
    src = open(os.path.join(STATIC, "mobile.js"), encoding="utf-8").read()
    assert "fetch(API + path" in src
    # every api(...) call passes a path that starts with "/", so the prefix must be unconditional
    assert "path.indexOf" not in src


# (method, template path under /api/ws, fragment that mobile.js must contain)
MOBILE_CALLS = [
    ("GET", "/conversations", '"/conversations'),
    ("GET", "/conversations/{cid}", '"/conversations/"'),
    ("POST", "/chat", '"POST", "/chat"'),
    ("GET", "/tasks", '"GET", "/tasks"'),
    ("GET", "/approvals", '"GET", "/approvals"'),
    ("POST", "/approvals/{rid}/approve", '"approve"'),
    ("POST", "/approvals/{rid}/reject", '"reject"'),
    ("GET", "/memory", '"/memory"'),
    ("DELETE", "/memory/{key}", '"/memory/"'),
    ("GET", "/status", '"GET", "/status"'),
]


def test_mobile_calls_exist_in_backend_openapi(client):
    schema = client.get("/openapi.json").json()
    paths = schema["paths"]
    src = open(os.path.join(STATIC, "mobile.js"), encoding="utf-8").read()
    for method, tmpl, fragment in MOBILE_CALLS:
        full = "/api/ws" + tmpl
        assert full in paths, full
        assert method.lower() in paths[full], (method, full)
        assert fragment in src, fragment
