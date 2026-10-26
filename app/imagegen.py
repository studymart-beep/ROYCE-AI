"""Image generation for Royce AI.

generate(prompt) -> bytes (PNG/JPEG). Uses OpenRouter chat completions with the
image modality. Model is IMAGE_MODEL in .env, default google/gemini-2.5-flash-image-preview.
Raises ImageError with the name of the service that failed. Nothing is faked.
"""
import base64, json, os, re, urllib.request, urllib.error

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "google/gemini-2.5-flash-image"


class ImageError(Exception):
    pass


def _key():
    return os.environ.get("OPENROUTER_API_KEY", "")


def model():
    return os.environ.get("IMAGE_MODEL", DEFAULT_MODEL)


def _decode_data_url(url):
    m = re.match(r"data:image/[a-zA-Z0-9.+-]+;base64,(.*)$", url or "", re.S)
    if not m:
        return None
    return base64.b64decode(m.group(1))


def generate(prompt, timeout=90):
    prompt = (prompt or "").strip()
    if not prompt:
        raise ImageError("image generation needs a prompt")
    if not _key():
        raise ImageError("image generation (openrouter): OPENROUTER_API_KEY is not set")
    from .agent import budget
    budget.consume()  # shared budget: reserved before the call, failures count too
    body = json.dumps({
        "model": model(),
        "messages": [{"role": "user", "content": prompt}],
        "modalities": ["image", "text"],
    }).encode()
    req = urllib.request.Request(BASE_URL, data=body, headers={
        "Authorization": "Bearer " + _key(),
        "Content-Type": "application/json",
        "User-Agent": "RoyceAI/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        raise ImageError(f"image generation (openrouter, {model()}) failed: HTTP {e.code} {detail}")
    except Exception as e:
        raise ImageError(f"image generation (openrouter, {model()}) failed: {e}")

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError):
        raise ImageError(f"image generation (openrouter, {model()}) returned no message")

    for img in msg.get("images") or []:
        url = (img.get("image_url") or {}).get("url") or img.get("url")
        raw = _decode_data_url(url)
        if raw:
            return raw
    content = msg.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") == "image_url":
                raw = _decode_data_url((part.get("image_url") or {}).get("url"))
                if raw:
                    return raw
    raise ImageError(f"image generation (openrouter, {model()}) returned no image for that prompt")
