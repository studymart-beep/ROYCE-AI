"""Chat attachments for the web workspace: validate, store, serve, and extract text.

Files land under DATA_DIR/uploads/<conversation_id>/<uuid>-<sanitized-name>. Clients only ever get a
served url back (/uploads/<cid>/<file>); chat requests refer to attachments by that url, and the server
re-resolves it to a file inside the uploads root, so a client cannot point the model at any other path.

Images (png, jpg, webp; max 10 MB) go to the model as vision parts through llm.chat_vision.
Documents (pdf, txt, csv, docx; max 20 MB) contribute extracted text to the prompt. Preview is
honest about what it could not read: a pdf needs pypdf, a docx needs python-docx, and when either is
missing the card says so and the file stays downloadable.
"""
import os, re, uuid
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .paths import DATA_DIR

ROOT = os.path.join(DATA_DIR, "uploads")
IMAGE_MAX = 10 * 1024 * 1024
DOC_MAX = 20 * 1024 * 1024
DOC_TEXT_CAP = 20000
MAX_ATTACHMENTS = 4
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}
DOC_TYPES = {".pdf": "application/pdf", ".txt": "text/plain", ".csv": "text/csv", ".docx": DOCX_MIME}
ALLOWED_EXT = set(IMAGE_TYPES) | set(DOC_TYPES)

_CID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_FILE_RE = re.compile(r"^([0-9a-f]{32})-([A-Za-z0-9._-]{1,140})$")
_URL_RE = re.compile(r"^/uploads/([A-Za-z0-9_-]{1,64})/([0-9a-f]{32}-[A-Za-z0-9._-]{1,140})$")

router = APIRouter()


def sanitize_name(name):
    base = os.path.basename((name or "").replace("\\", "/")).strip()
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).lstrip(".")
    base = base[:120] or "file"
    return base


def _magic_ok(kind, ext, head):
    if kind == "image":
        if ext == ".png":
            return head[:8] == b"\x89PNG\r\n\x1a\n"
        if ext in (".jpg", ".jpeg"):
            return head[:3] == b"\xff\xd8\xff"
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if ext == ".pdf":
        return head[:4] == b"%PDF"
    if ext == ".docx":
        return head[:4] == b"PK\x03\x04"
    return b"\x00" not in head  # txt/csv: plain text only


def _text_ok(data):
    try:
        data.decode("utf-8-sig")
        return True
    except UnicodeDecodeError:
        return False


def store(conv_id, filename, data_stream, content_type=""):
    """Validate one upload and write it. Raises HTTPException with a clear code on any failure."""
    cid = conv_id if conv_id and _CID_RE.match(conv_id) else "unassigned"
    if conv_id and not _CID_RE.match(conv_id):
        raise HTTPException(400, {"error": "bad_conversation", "message": "bad conversation id"})
    ext = os.path.splitext((filename or "").lower())[1]
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, {"error": "bad_type", "message": "that file type is not supported. use png, jpg, webp, pdf, txt, csv or docx."})
    kind = "image" if ext in IMAGE_TYPES else "file"
    limit = IMAGE_MAX if kind == "image" else DOC_MAX
    data = data_stream.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, {"error": "too_large", "message": f"{'images' if kind == 'image' else 'files'} must be {limit // (1024 * 1024)} MB or smaller"})
    if not data:
        raise HTTPException(400, {"error": "empty", "message": "the file is empty"})
    if not _magic_ok(kind, ext, data[:16]):
        raise HTTPException(415, {"error": "bad_type", "message": "the file contents do not match its type"})
    if ext in (".txt", ".csv") and not _text_ok(data):
        raise HTTPException(415, {"error": "bad_type", "message": "text files must be utf-8"})
    name = sanitize_name(filename)
    if not name.lower().endswith(ext):
        name = name.rsplit(".", 1)[0][:100] + ext
    fname = uuid.uuid4().hex + "-" + name
    folder = os.path.join(ROOT, cid)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, fname), "wb") as f:
        f.write(data)
    mime = IMAGE_TYPES.get(ext) or DOC_TYPES[ext]
    return {"url": f"/uploads/{cid}/{fname}", "name": name, "type": mime, "size": len(data),
            "kind": kind}


def resolve(url):
    """Map a served url back to a real file inside the uploads root. None when it is not one of ours."""
    m = _URL_RE.match(url or "")
    if not m:
        return None
    cid, fname = m.group(1), m.group(2)
    root = os.path.realpath(ROOT)
    path = os.path.realpath(os.path.join(root, cid, fname))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        return None
    name = _FILE_RE.match(fname).group(2)
    ext = os.path.splitext(name.lower())[1]
    kind = "image" if ext in IMAGE_TYPES else "file"
    return {"path": path, "url": url, "name": name, "type": IMAGE_TYPES.get(ext) or DOC_TYPES.get(ext, "application/octet-stream"),
            "size": os.path.getsize(path), "kind": kind}


def extract_text(info):
    """Return (text, note). text is None when nothing could be read; note says why, in plain words."""
    ext = os.path.splitext(info["name"].lower())[1]
    try:
        if ext in (".txt", ".csv"):
            with open(info["path"], "rb") as f:
                text = f.read().decode("utf-8-sig", errors="replace")
            return text[:DOC_TEXT_CAP], ""
        if ext == ".pdf":
            try:
                from pypdf import PdfReader
            except ImportError:
                return None, "preview unavailable, download to view"
            reader = PdfReader(info["path"])
            text = "\n".join((p.extract_text() or "") for p in reader.pages).strip()
            if not text:
                return None, "no selectable text in this pdf, download to view"
            return text[:DOC_TEXT_CAP], ""
        if ext == ".docx":
            try:
                import docx
            except ImportError:
                return None, "preview unavailable, download to view"
            text = "\n".join(p.text for p in docx.Document(info["path"]).paragraphs).strip()
            if not text:
                return None, "no text found in this document"
            return text[:DOC_TEXT_CAP], ""
    except Exception:
        return None, "couldn't read this file, download to view"
    return None, "preview unavailable, download to view"


def split_refs(refs):
    images = [r for r in refs if r["kind"] == "image"]
    docs = [r for r in refs if r["kind"] != "image"]
    return images, docs


def serialize(refs):
    """The stored form on a conversation message: what the UI needs to re-render the attachment."""
    return [{"url": r["url"], "name": r["name"], "type": r["type"], "size": r["size"], "kind": r["kind"]} for r in refs]


@router.get("/uploads/{cid}/{fname}")
def serve_upload(cid: str, fname: str):
    url = f"/uploads/{cid}/{fname}"
    info = resolve(url)
    if info is None:
        raise HTTPException(404, "no such upload")
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"}
    if info["kind"] == "image":
        return FileResponse(info["path"], media_type=info["type"], headers=headers)
    return FileResponse(info["path"], media_type=info["type"], filename=info["name"], headers=headers)
