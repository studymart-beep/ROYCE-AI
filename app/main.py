import os
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import db
from .scripts_engine import build_script_prompt, normalize_script
from .llm import generate_script_package
from .config import provider_status, load_settings, save_settings, DEFAULT_PROVIDER
from . import collectors
from . import workstore

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(BASE, "app", "static")
app = FastAPI(title="Royce AI")

@app.get("/")
def index(): return FileResponse(os.path.join(STATIC, "index.html"))
@app.get("/scripts")
def scripts_page(): return FileResponse(os.path.join(STATIC, "scripts.html"))
@app.get("/crm")
def crm_page(): return FileResponse(os.path.join(STATIC, "crm.html"))
@app.get("/reporting")
def reporting_page(): return FileResponse(os.path.join(STATIC, "reporting.html"))
@app.get("/connectors")
def connectors_page(): return FileResponse(os.path.join(STATIC, "connectors.html"))

@app.get("/api/settings")
def get_settings(): return provider_status()

@app.put("/api/settings")
async def put_settings(req: Request):
    d = await req.json()
    s = load_settings()
    if d.get("default_provider") in ("gemini", "openrouter"):
        s["default_provider"] = d["default_provider"]
        save_settings(s)
    return provider_status()

@app.post("/api/script")
async def api_script(req: Request):
    d = await req.json()
    product, audience = d.get("product","").strip(), d.get("audience","").strip()
    platform = (d.get("platform") or "instagram").strip().lower()
    if not product or not audience:
        return JSONResponse({"error": "product and audience required"}, status_code=400)
    pkg = generate_script_package(product, audience, platform, provider=d.get("provider"))
    workstore.add_package(pkg, product, audience, platform, source="mini app")
    return pkg

@app.get("/api/work")
def api_work(): return workstore.list_work()

@app.get("/api/leads")
def api_leads(): return db.list_leads()

@app.post("/api/leads")
async def api_add_lead(req: Request):
    d = await req.json()
    if not d.get("name"):
        return JSONResponse({"error": "name required"}, status_code=400)
    lid = db.add_lead(d["name"], d.get("business",""), d.get("contact",""), d.get("notes",""), d.get("next_followup"))
    return db.get_lead(lid)

@app.patch("/api/leads/{lid}")
async def api_update_lead(lid: int, req: Request):
    d = await req.json()
    return db.update_lead(lid, **d)

@app.get("/api/report/summary")
def api_report_summary(): return db.summary()

@app.get("/api/collectors")
def api_collectors(): return collectors.status()

@app.put("/api/collectors")
async def api_save_collector(req: Request):
    d = await req.json()
    name = (d.get("name") or "").strip().lower()
    if name not in collectors.COLLECTORS:
        return JSONResponse({"error": f"unknown collector '{name}'"}, status_code=400)
    try:
        collectors.get(name).configure(d.get("token", ""), d.get("account_id", ""))
    except collectors.CollectorError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    return collectors.status()

@app.post("/api/collectors/{name}/test")
def api_test_collector(name: str):
    try:
        return collectors.get(name).test()
    except collectors.CollectorError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

@app.post("/api/collectors/{name}/report")
async def api_collector_report(name: str, req: Request):
    d = await req.json() if (await req.body()) else {}
    preset = d.get("preset") or "last_7d"
    try:
        c = collectors.get(name)
        if not c.configured:
            return JSONResponse({"error": f"{name} is not configured yet"}, status_code=400)
        return {"preset": preset, "rows": c.fetch_insights(preset)}
    except collectors.CollectorError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

@app.post("/api/report/all")
async def api_report_all(req: Request):
    d = await req.json() if (await req.body()) else {}
    preset = d.get("preset") or "last_7d"
    rows, errors = collectors.fetch_all(preset)
    if not collectors.configured_names():
        return JSONResponse({"error": "no collectors configured", "configured": []}, status_code=400)
    return {"preset": preset, "rows": rows, "errors": errors, "configured": collectors.configured_names()}

@app.get("/api/gmail/summary")
def api_gmail_summary(days: int = 7):
    try:
        return collectors.get("gmail").summary(days=max(1, min(days, 60)))
    except collectors.CollectorError as e:
        return JSONResponse({"error": str(e)}, status_code=400)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
