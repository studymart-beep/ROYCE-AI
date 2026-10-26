"""Where mutable data files (JSON stores, crm.db, backups, logs) live.

Default is the repo root, exactly as before. Set ROYCE_DATA_DIR to a persistent disk path
(for example a Render disk mount) to keep state across deploys. Code files never move.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.abspath(os.environ.get("ROYCE_DATA_DIR", "").strip() or BASE)
os.makedirs(DATA_DIR, exist_ok=True)
