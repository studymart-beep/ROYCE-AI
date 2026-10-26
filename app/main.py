import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from . import db

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, "app", "static")
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(_app):
    # Telegram bot runs in-process (one poll thread, guarded). Startup never waits on Telegram:
    # the thread does the getMe check and retries with backoff. Shutdown stops and joins it.
    from . import bot_runner
    try:
        bot_runner.start_bot_thread()
    except Exception as e:
        print(f"[bot] could not start telegram thread: {type(e).__name__}: {e}", flush=True)
    try:
        yield
    finally:
        bot_runner.stop_bot_thread(timeout=10)


app = FastAPI(title="Royce AI", lifespan=_lifespan)

DEFAULT_ORIGINS = ["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:1420", "http://tauri.localhost", "tauri://localhost"]


def allowed_origins(raw=None):
    """Origins from ALLOWED_ORIGINS (comma-separated). Never a wildcard: '*' is dropped with the rest.
    Unset means the localhost defaults. Auth is a header token, not cookies, so credentials stay off."""
    raw = os.environ.get("ALLOWED_ORIGINS", "") if raw is None else raw
    if not raw.strip():
        return list(DEFAULT_ORIGINS)
    out = []
    for item in raw.split(","):
        o = item.strip().rstrip("/")
        if o and o != "*" and o not in out:
            out.append(o)
    return out


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["content-type", "authorization", "x-workspace-token"],
    max_age=600,
)

@app.get("/healthz")
def healthz(): return {"ok": True}

@app.get("/readyz")
def readyz():
    from .agent import router
    state = router.readiness()
    ready = any(v["configured"] for v in state.values())
    body = {"ready": ready, "providers": {k: {"configured": v["configured"], "healthy": v["healthy"]} for k, v in state.items()}}
    return JSONResponse(body, status_code=200 if ready else 503)

@app.get("/workspace")
def workspace_page(): return FileResponse(os.path.join(STATIC, "workspace.html"))
@app.get("/m")
def mobile_page(): return FileResponse(os.path.join(STATIC, "mobile.html"), headers={"Cache-Control": "no-cache"})
@app.get("/static/config.js")
def static_config():
    # optional runtime override for a separate static host; empty file = same-origin default
    path = os.path.join(STATIC, "config.js")
    if os.path.isfile(path):
        return FileResponse(path, media_type="application/javascript", headers={"Cache-Control": "no-cache"})
    return Response("/* no api base override: same-origin */\n", media_type="application/javascript",
                    headers={"Cache-Control": "no-cache"})
@app.get("/offline")
def offline_page(): return FileResponse(os.path.join(STATIC, "offline.html"))
@app.get("/sw.js")
def service_worker():
    # served from the root so its scope covers /workspace and /m; never cached by the browser
    return FileResponse(os.path.join(STATIC, "sw.js"), media_type="application/javascript",
                        headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})
from .workspace_api import router as workspace_router
app.include_router(workspace_router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
