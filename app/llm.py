import json, os, urllib.request
from .config import PROVIDERS, DEFAULT_PROVIDER, load_settings
from .style import VOICE_RULES

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

ORDER = ["gemini", "openrouter"]  # kept for backward compatibility; the router decides the real order

def chat_text(messages, temperature=0.8, timeout=20, provider=None):
    """Text chat through the model router (health + capability aware, gemini primary by default)."""
    from .agent import router
    s = load_settings()
    pref = provider or s.get("default_provider", DEFAULT_PROVIDER)
    def _one(pid):
        return _post_chat(pid, messages, temperature, timeout).strip()
    return router.call(_one, capability="chat", preferred=pref)

def chat_vision(messages, image_bytes, mime="image/jpeg", temperature=0.5, timeout=60):
    """Send one image with a text prompt through the same OpenAI-compatible endpoints, routed by capability 'vision'.
    messages: system/history list; the last user message text is attached to the image."""
    import base64
    from .agent import router
    b64 = base64.b64encode(image_bytes).decode()
    url = f"data:{mime};base64,{b64}"
    s = load_settings()
    pref = s.get("default_provider", DEFAULT_PROVIDER)
    prompt_text = messages[-1]["content"]
    head = messages[:-1]
    def _one(pid):
        model_msgs = head + [{"role": "user", "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": url}},
        ]}]
        return _post_chat(pid, model_msgs, temperature, timeout).strip()
    return router.call(_one, capability="vision", preferred=pref)

SCRIPT_SYSTEM = (
    "You are Royce AI, the ad-creative engine for Royce Marketing Agency (Lagos, Nigeria). "
    "Given a product, audience and platform, return STRICT JSON with keys: "
    "hooks (array of 8 punchy scroll-stopping hook lines), "
    "script (array of scene objects with 'beat', 'visual', 'voiceover'), "
    "captions (array of 5 platform captions with hashtags). "
    "No markdown, no text outside JSON. Match the platform's culture and the audience's language. "
    + VOICE_RULES
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
