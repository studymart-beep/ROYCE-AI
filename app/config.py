import json, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from .paths import DATA_DIR
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

PROVIDERS = {
    "gemini": {
        "base": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
        "model": os.environ.get("GEMINI_MODEL", "gemini-3.8-flash"),
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
