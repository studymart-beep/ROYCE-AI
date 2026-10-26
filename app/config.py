import json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import DATA_DIR
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Gemini model cascade, tried in order on retryable errors (429/503/timeout/network) before
# the router fails over to the next provider. Override with GEMINI_MODELS (comma-separated).
# GEMINI_MODEL, when set, is always the first entry.
GEMINI_MODELS_DEFAULT = ["gemini-2.5-flash", "gemini-2.5-flash-lite", "gemini-2.0-flash", "gemini-2.5-pro"]


def parse_model_list(raw):
    """Comma-separated model names: trimmed, empties dropped, duplicates removed, order kept."""
    out = []
    for part in (raw or "").split(","):
        m = part.strip()
        if m and m not in out:
            out.append(m)
    return out


def gemini_models():
    """The live gemini cascade, read from the environment on each call."""
    models = parse_model_list(os.environ.get("GEMINI_MODELS", "")) or list(GEMINI_MODELS_DEFAULT)
    single = (os.environ.get("GEMINI_MODEL", "") or "").strip()
    if single:
        models = [single] + [m for m in models if m != single]
    return models


PROVIDERS = {
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "model": gemini_models()[0],  # first cascade entry, kept for single-model callers
    },
    "openrouter": {
        "base": "https://openrouter.ai/api/v1/",
        "key_env": "OPENROUTER_API_KEY",
        "model": os.environ.get("OPENROUTER_MODEL", "openrouter/auto"),
    },
}
DEFAULT_PROVIDER = "gemini"

def load_settings():
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(d):
    with open(SETTINGS_PATH, "w") as f:
        json.dump(d, f, indent=2)

def provider_status():
    out = {}
    for pid, p in PROVIDERS.items():
        out[pid] = {"configured": bool(os.environ.get(p["key_env"])), "model": p["model"]}
    out["default"] = load_settings().get("default_provider", DEFAULT_PROVIDER)
    return out
