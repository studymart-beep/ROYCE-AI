"""Voice replies: text-to-speech with provider abstraction and graceful failure.

tts(text) -> bytes (ogg opus, ready for Telegram sendVoice).
Providers, in order:
  1. openai_tts   needs OPENAI_API_KEY (skipped when absent)
  2. openrouter_tts  OpenRouter chat-completions with audio output (openai/gpt-audio-mini);
                     needs OpenRouter audio access and credits
If none works, tts() raises VoiceUnavailable with the message:
  "voice needs an openai key or openrouter audio model access"
"""
import base64, json, os, subprocess, tempfile, urllib.request

OPENAI_MODEL = os.environ.get("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
OPENAI_VOICE = os.environ.get("OPENAI_TTS_VOICE", "alloy")
OPENROUTER_AUDIO_MODEL = os.environ.get("OPENROUTER_AUDIO_MODEL", "openai/gpt-audio-mini")
OPENROUTER_VOICE = os.environ.get("OPENROUTER_TTS_VOICE", "alloy")
MAX_CHARS = 1200

NO_PROVIDER_MSG = "voice needs an openai key or openrouter audio model access"


class VoiceUnavailable(RuntimeError):
    pass


def _to_ogg_opus(audio_bytes, in_ext):
    """Convert mp3/wav/pcm bytes to ogg opus (Telegram voice note format) using ffmpeg."""
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "in." + in_ext)
        dst = os.path.join(d, "out.ogg")
        with open(src, "wb") as f:
            f.write(audio_bytes)
        args = ["ffmpeg", "-y", "-loglevel", "error"]
        if in_ext == "pcm":
            args += ["-f", "s16le", "-ar", "24000", "-ac", "1"]
        args += ["-i", src, "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", dst]
        r = subprocess.run(args, capture_output=True, timeout=60)
        if r.returncode != 0 or not os.path.exists(dst):
            raise VoiceUnavailable("could not convert the voice to telegram format: " + r.stderr.decode(errors="ignore")[:160])
        with open(dst, "rb") as f:
            return f.read()


def _openai_tts(text):
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        raise VoiceUnavailable("openai_tts skipped: no OPENAI_API_KEY")
    body = json.dumps({"model": OPENAI_MODEL, "input": text, "voice": OPENAI_VOICE, "response_format": "mp3"}).encode()
    req = urllib.request.Request("https://api.openai.com/v1/audio/speech", data=body,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return _to_ogg_opus(r.read(), "mp3")


def _openrouter_tts(text):
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if not key:
        raise VoiceUnavailable("openrouter_tts skipped: no OPENROUTER_API_KEY")
    body = json.dumps({
        "model": OPENROUTER_AUDIO_MODEL,
        "modalities": ["text", "audio"],
        "audio": {"voice": OPENROUTER_VOICE, "format": "pcm16"},
        "stream": True,
        "messages": [{"role": "system", "content": "Read the user's message aloud exactly, with no added words."},
                     {"role": "user", "content": text}],
    }).encode()
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body,
                                 headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    pcm = b""
    with urllib.request.urlopen(req, timeout=90) as r:
        for raw in r:
            line = raw.decode("utf-8", "ignore").strip()
            if not line.startswith("data:") or line == "data: [DONE]":
                continue
            try:
                delta = json.loads(line[5:])["choices"][0]["delta"]
            except Exception:
                continue
            audio = (delta.get("audio") or {}).get("data")
            if audio:
                pcm += base64.b64decode(audio)
    if not pcm:
        raise VoiceUnavailable("openrouter_tts returned no audio")
    return _to_ogg_opus(pcm, "pcm")


PROVIDERS = [("openai_tts", _openai_tts), ("openrouter_tts", _openrouter_tts)]


def tts(text):
    """Return ogg opus bytes for the text, or raise VoiceUnavailable with a clear message."""
    text = (text or "").strip()
    if not text:
        raise VoiceUnavailable("nothing to say")
    text = text[:MAX_CHARS]
    from .agent import budget
    budget.consume()  # shared budget: reserved before any provider call
    errors = []
    for name, fn in PROVIDERS:
        try:
            return fn(text)
        except VoiceUnavailable as e:
            errors.append(f"{name}: {e}")
        except Exception as e:
            errors.append(f"{name}: {str(e)[:120]}")
    print("[voice] unavailable:", " | ".join(errors))
    raise VoiceUnavailable(NO_PROVIDER_MSG)
