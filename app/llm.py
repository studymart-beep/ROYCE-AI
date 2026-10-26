import json, os, urllib.request
from .config import PROVIDERS, DEFAULT_PROVIDER, load_settings

def _key(pid):
    return os.environ.get(PROVIDERS[pid]["key_env"], "")

def _post_chat(pid, messages, temperature=0.8, timeout=20):
    p = PROVIDERS[pid]
    body = json.dumps({"model": p["model"], "messages": messages,
                       "temperature": temperature, "max_tokens": 2500}).encode()
    req = urllib.request.Request(p["base"] + "chat/completions", data=body,
        headers={"Authorization": "Bearer " + _key(pid), "Content-Type": "application/json", "User-Agent": "RoyceAI/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = json.loads(r.read())
    return data["choices"][0]["message"]["content"]

ORDER = ["gemini", "openrouter"]

def chat_text(messages, temperature=0.8, timeout=20, provider=None):
    s = load_settings()
    pref = provider or s.get("default_provider", DEFAULT_PROVIDER)
    order = [pref] + [p for p in ORDER if p != pref]
    last = None
    for pid in order:
        if not _key(pid):
            continue
        try:
            text = _post_chat(pid, messages, temperature, timeout).strip()
            print(f"[llm] served by {pid} ({PROVIDERS[pid]['model']})", flush=True)
            return text
        except Exception as e:
            last = e
            print(f"[llm] {pid} failed: {e}", flush=True)
    raise RuntimeError(f"all providers failed: {last}")

SCRIPT_SYSTEM = (
    "You are Royce AI, the ad-creative engine for Royce Marketing Agency (Lagos, Nigeria). "
    "Given a product, audience and platform, return STRICT JSON with keys: "
    "hooks (array of 8 punchy scroll-stopping hook lines), "
    "script (array of scene objects with 'beat', 'visual', 'voiceover'), "
    "captions (array of 5 platform captions with hashtags). "
    "No markdown, no text outside JSON. Match the platform's culture and the audience's language."
)

def generate_script_package(product, audience, platform, temperature=0.9, provider=None):
    user = f"Product: {product}\nAudience: {audience}\nPlatform: {platform}\nGenerate the ad package JSON."
    raw = chat_text([{"role": "system", "content": SCRIPT_SYSTEM},
                     {"role": "user", "content": user}],
                    temperature=temperature, provider=provider)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw.strip())
