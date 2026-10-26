"""Local proof server for the Chief of Staff browser check. Stubs the LLM so no provider key is used.
Run: WORKSPACE_TOKEN=<token> ROYCE_TELEGRAM_IN_PROCESS=0 python scripts/chief_proof_server.py
The stub answers routing calls with a fixed specialist route and specialist calls with a fixed hook list."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import llm  # noqa: E402

_ADS = {"direct-response": None}


def _stub(msgs, **kw):
    system = msgs[0]["content"]
    if "Reply with ONLY one JSON object" in system:
        from app import bots
        spec = next(b for b in bots.list_bots() if b.get("template") == "direct-response")
        return json.dumps({"bot": spec["id"], "instruction": msgs[-1]["content"], "needs_approval": False})
    return ("1. Hear the grind? NOISE earbuds hear it for you.\n"
            "2. Your commute, your playlist, zero noise. NOISE.\n"
            "3. Lagos traffic is loud enough. Get NOISE earbuds.")


llm.chat_text = _stub
import app.workspace_api as _wa  # noqa: E402
_wa.chat_text = _stub  # _bot_chat imports chat_text from app.llm at call time

import uvicorn  # noqa: E402
from app.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8011")))
