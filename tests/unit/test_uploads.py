"""Chat attachments: upload validation, storage, served urls, traversal safety, vision path, doc text."""
import io
import os
import pytest
from fastapi.testclient import TestClient

from app import conversations, main, uploads
from app.agent import approvals, audit, budget, chatplan, memory

TOKEN = "test-token-123"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff" + b"\x00" * 64
WEBP = b"RIFF" + b"\x10\x00\x00\x00" + b"WEBP" + b"\x00" * 16
PDF = b"%PDF-1.4\n%test\n"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_TOKEN", TOKEN)
    monkeypatch.setattr(conversations, "PATH", str(tmp_path / "conversations.json"))
    monkeypatch.setattr(approvals, "PATH", str(tmp_path / "approvals.json"))
    monkeypatch.setattr(chatplan, "PATH", str(tmp_path / "chat_tasks.json"))
    monkeypatch.setattr(audit, "PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setattr(memory, "PATH", str(tmp_path / "memory.json"), raising=False)
    monkeypatch.setattr(uploads, "ROOT", str(tmp_path / "uploads"))
    budget.reset()
    yield


@pytest.fixture
def client():
    return TestClient(main.app)


def _up(client, name, data, mime="application/octet-stream", cid=None):
    fields = {"conversation_id": cid} if cid else {}
    return client.post("/api/ws/upload", files={"file": (name, io.BytesIO(data), mime)}, data=fields)


def test_upload_happy_path_png_returns_served_url(client):
    r = _up(client, "shot.png", PNG, "image/png", cid="conv1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"url", "name", "type", "size", "kind"}
    assert body["url"].startswith("/uploads/conv1/")
    assert body["name"] == "shot.png" and body["type"] == "image/png"
    assert body["size"] == len(PNG) and body["kind"] == "image"
    # the file is stored under the uploads root for that conversation
    info = uploads.resolve(body["url"])
    assert info is not None and os.path.isfile(info["path"])


def test_upload_pdf_and_csv_are_documents(client):
    pdf = _up(client, "brief.pdf", PDF, "application/pdf", cid="conv2").json()
    csv = _up(client, "leads.csv", b"name,score\nada,9\n", "text/csv", cid="conv2").json()
    assert pdf["kind"] == "file" and pdf["type"] == "application/pdf"
    assert csv["kind"] == "file" and csv["type"] == "text/csv"


def test_upload_webp_and_jpg_accepted(client):
    assert _up(client, "a.webp", WEBP, "image/webp", cid="c").status_code == 200
    assert _up(client, "a.jpg", JPG, "image/jpeg", cid="c").status_code == 200


def test_upload_oversize_image_rejected(client):
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (uploads.IMAGE_MAX)
    r = _up(client, "big.png", big, "image/png", cid="conv3")
    assert r.status_code == 413
    assert r.json()["detail"]["error"] == "too_large"


def test_upload_oversize_document_rejected(client):
    big = b"%PDF-" + b"0" * (uploads.DOC_MAX)
    r = _up(client, "big.pdf", big, "application/pdf", cid="conv3")
    assert r.status_code == 413


def test_upload_bad_extension_rejected(client):
    r = _up(client, "evil.exe", b"MZ\x90\x00", cid="conv4")
    assert r.status_code == 415
    assert r.json()["detail"]["error"] == "bad_type"


def test_upload_content_must_match_type(client):
    # an .png name with pdf bytes is refused
    r = _up(client, "fake.png", PDF, "image/png", cid="conv4")
    assert r.status_code == 415


def test_upload_empty_file_rejected(client):
    r = _up(client, "empty.txt", b"", "text/plain", cid="conv4")
    assert r.status_code == 400


def test_upload_name_is_sanitized(client):
    r = _up(client, "../../etc/my<script>.png", PNG, "image/png", cid="conv5")
    body = r.json()
    assert "/" not in body["name"] and "<" not in body["name"]
    assert body["url"].startswith("/uploads/conv5/")


def test_served_upload_downloads_with_nosniff(client):
    url = _up(client, "shot.png", PNG, "image/png", cid="conv6").json()["url"]
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.content == PNG


def test_get_upload_path_traversal_refused(client):
    """Traversal in the uploads GET route never reads outside the uploads root."""
    # a secret file next to the uploads root must not be reachable
    secret = os.path.join(os.path.dirname(uploads.ROOT), "secret.txt")
    with open(secret, "w") as f:
        f.write("do not serve")
    for bad in ["/uploads/../secret.txt", "/uploads/conv6/..%2F..%2Fsecret.txt",
                "/uploads/conv6/%2e%2e%2fsecret.txt", "/uploads/..%2fsecret.txt/x"]:
        r = client.get(bad)
        assert r.status_code in (400, 404), (bad, r.status_code)
        assert b"do not serve" not in r.content


def test_resolve_rejects_foreign_and_malformed_urls(tmp_path):
    assert uploads.resolve("/uploads/conv/../../etc/passwd") is None
    assert uploads.resolve("/etc/passwd") is None
    assert uploads.resolve("") is None
    assert uploads.resolve(None) is None


def test_extract_text_from_txt_and_csv(client):
    url = _up(client, "notes.txt", "hello brief\nline two".encode(), "text/plain", cid="c7").json()["url"]
    text, note = uploads.extract_text(uploads.resolve(url))
    assert "hello brief" in text and note == ""
    url2 = _up(client, "leads.csv", b"name,score\nada,9\n", "text/csv", cid="c7").json()["url"]
    text2, _ = uploads.extract_text(uploads.resolve(url2))
    assert "ada,9" in text2


def test_extract_text_cap(tmp_path):
    p = tmp_path / "long.txt"
    p.write_bytes(b"a" * (uploads.DOC_TEXT_CAP + 500))
    text, note = uploads.extract_text({"path": str(p), "name": "long.txt", "kind": "file"})
    assert len(text) == uploads.DOC_TEXT_CAP


def test_pdf_without_pypdf_is_honest(monkeypatch, tmp_path):
    import builtins
    real = builtins.__import__

    def fake(name, *a, **kw):
        if name == "pypdf":
            raise ImportError("no pypdf")
        return real(name, *a, **kw)
    monkeypatch.setattr(builtins, "__import__", fake)
    text, note = uploads.extract_text({"path": str(tmp_path / "x.pdf"), "name": "x.pdf", "kind": "file"})
    assert text is None and "download to view" in note


def test_pdf_text_extracted_via_pypdf(monkeypatch, tmp_path):
    """Doc text reaches the prompt: a pdf page's text comes back from pypdf when it is installed."""
    class Page:
        def extract_text(self):
            return "Q3 campaign brief: 3 hooks"

    class Reader:
        def __init__(self, path):
            self.pages = [Page()]
    import sys
    import types
    fake = types.ModuleType("pypdf")
    fake.PdfReader = Reader
    monkeypatch.setitem(sys.modules, "pypdf", fake)
    p = tmp_path / "brief.pdf"
    p.write_bytes(PDF)
    text, note = uploads.extract_text({"path": str(p), "name": "brief.pdf", "kind": "file"})
    assert "Q3 campaign brief" in text and note == ""


def test_chat_with_image_attachment_hits_vision_path(client, monkeypatch):
    """An image attachment goes to the model as an image part, via llm.chat_vision_many."""
    from app import llm
    from app import workspace_api
    seen = {}

    def fake_post(pid, msgs, temp, timeout):
        seen["msgs"] = msgs
        return "I see a red poster"

    def fake_vision(messages, images, temperature=0.5, timeout=60):
        seen["images"] = images
        seen["messages"] = messages
        return "I see a red poster"
    monkeypatch.setattr(llm, "chat_vision_many", fake_vision)
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda text: "sys")
    url = _up(client, "poster.png", PNG, "image/png", cid="chat1").json()["url"]
    r = client.post("/api/ws/chat", json={"message": "what do you see", "attachments": [url]})
    assert r.status_code == 200, r.text
    assert seen["images"] == [(PNG, "image/png")]
    last = seen["messages"][-1]
    assert last["role"] == "user" and "what do you see" in str(last["content"])
    assert "[attached image: poster.png]" in str(last["content"])


def test_llm_chat_vision_many_builds_image_parts(monkeypatch):
    """The real vision builder puts every image on the final user turn as an image_url part."""
    from app import llm
    from app.agent import router
    captured = {}

    def fake_call(fn, capability=None, preferred=None, before_attempt=None):
        captured["capability"] = capability
        return fn("gemini")
    monkeypatch.setattr(router, "call", fake_call)
    monkeypatch.setattr(llm, "_post_chat", lambda pid, msgs, temp, timeout: captured.setdefault("msgs", msgs) and "ok")
    out = llm.chat_vision_many([{"role": "system", "content": "s"}, {"role": "user", "content": "compare"}],
                               [(PNG, "image/png"), (JPG, "image/jpeg")])
    assert out == "ok" and captured["capability"] == "vision"
    parts = captured["msgs"][-1]["content"]
    urls = [p["image_url"]["url"] for p in parts if p.get("type") == "image_url"]
    assert len(urls) == 2
    assert urls[0].startswith("data:image/png;base64,") and urls[1].startswith("data:image/jpeg;base64,")


def test_chat_document_text_prepended_to_prompt(client, monkeypatch):
    from app import workspace_api
    seen = {}

    def fake_chat(msgs, **kw):
        seen["msgs"] = msgs
        return "ok"
    monkeypatch.setattr(workspace_api, "_llm_chat", fake_chat)
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda text: "sys")
    url = _up(client, "notes.txt", b"ROYCE_DOC_MARKER launch in lagos", "text/plain", cid="chat2").json()["url"]
    r = client.post("/api/ws/chat", json={"message": "summarize", "attachments": [url]})
    assert r.status_code == 200, r.text
    text = str(seen["msgs"][-1]["content"])
    assert "[file: notes.txt]" in text and "ROYCE_DOC_MARKER" in text
    assert text.index("ROYCE_DOC_MARKER") < text.index("summarize")


def test_chat_rejects_unknown_attachment_path(client, monkeypatch):
    from app import workspace_api
    monkeypatch.setattr(workspace_api, "_llm_chat", lambda msgs, **kw: "x")
    monkeypatch.setattr(workspace_api, "_system_prompt", lambda text: "sys")
    r = client.post("/api/ws/chat", json={"message": "hi", "attachments": ["/etc/passwd"]})
    assert r.status_code == 400
    assert r.json()["detail"]["error"] == "bad_attachment"


def test_chat_empty_message_without_attachment_still_422(client):
    assert client.post("/api/ws/chat", json={"message": ""}).status_code == 422
