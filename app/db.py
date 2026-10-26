import sqlite3, os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(BASE, "crm.db")

def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init():
    with conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS leads(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL, business TEXT, contact TEXT,
            status TEXT DEFAULT 'open', notes TEXT,
            next_followup TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")

def add_lead(name, business="", contact="", notes="", next_followup=None):
    with conn() as c:
        cur = c.execute("INSERT INTO leads(name,business,contact,notes,next_followup) VALUES(?,?,?,?,?)",
                        (name, business, contact, notes, next_followup))
        return cur.lastrowid

def list_leads():
    with conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM leads ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'won' THEN 1 ELSE 2 END, next_followup IS NULL, next_followup")]

def get_lead(lid):
    with conn() as c:
        r = c.execute("SELECT * FROM leads WHERE id=?", (lid,)).fetchone()
        return dict(r) if r else None

def update_lead(lid, **kw):
    allowed = {k: v for k, v in kw.items() if k in ("name","business","contact","status","notes","next_followup")}
    sets = ", ".join(f"{k}=?" for k in allowed)
    with conn() as c:
        c.execute(f"UPDATE leads SET {sets} WHERE id=?", (*allowed.values(), lid))
    return get_lead(lid)

def summary():
    leads = list_leads()
    from collections import Counter
    by_status = Counter(l["status"] for l in leads)
    today = __import__("datetime").date.today().isoformat()
    overdue = [l for l in leads if l["next_followup"] and l["next_followup"] < today and l["status"] == "open"]
    return {"total": len(leads), "by_status": dict(by_status), "overdue": overdue}

init()
