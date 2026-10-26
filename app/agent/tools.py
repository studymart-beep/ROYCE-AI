"""Safe tool registry for Royce AI.

Every tool is a named entry with a schema (allowed argument names and types), a validator,
and a fixed implementation. There is no shell access: commands are built from argument
lists that this module owns, never from user text. The bot can ask a tool to run only by
name, and unknown names are refused.

Tools:
  run_tests        pytest in the repo, timeout 300s, output captured and truncated
  git_status       git status --short --branch
  git_log          git log --oneline, last N commits (N 1 to 50)
  git_diff         git diff, optionally of one path inside the repo (stat or patch)
  db_backup        copy crm.db to backups/crm-TIMESTAMP.db, keep the newest 10
  health_check     GET /healthz on a local port (default 8000)
"""
import datetime
import os
import re
import shutil
import subprocess
import urllib.request

from . import audit

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ..paths import DATA_DIR
DB_PATH = os.path.join(DATA_DIR, "crm.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
BACKUP_KEEP = 10
TEST_TIMEOUT = 300
GIT_TIMEOUT = 20
OUTPUT_CAP = 4000
REFS_RE = re.compile(r"^[A-Za-z0-9._/-]{1,80}$")


class ToolError(Exception):
    pass


def _trim(s):
    s = s or ""
    return s if len(s) <= OUTPUT_CAP else s[-OUTPUT_CAP:]


def _python():
    venv = os.path.join(BASE, ".venv", "bin", "python")
    return venv if os.path.exists(venv) else "python3"


def _git(args):
    # argument list only; shell=False so nothing in args is interpreted
    try:
        r = subprocess.run(["git", "-C", BASE] + list(args), capture_output=True, text=True,
                           timeout=GIT_TIMEOUT, shell=False)
    except subprocess.TimeoutExpired:
        raise ToolError("git timed out")
    if r.returncode != 0:
        raise ToolError(_trim((r.stderr or r.stdout or "git failed").strip())[:300])
    return _trim(r.stdout)


def _check_int(name, v, lo, hi, default):
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, int):
        raise ToolError(f"{name} must be an integer")
    if not lo <= v <= hi:
        raise ToolError(f"{name} must be between {lo} and {hi}")
    return v


def _check_path(v):
    if v is None or v == "":
        return None
    if not isinstance(v, str) or not REFS_RE.match(v) or ".." in v or v.startswith("/"):
        raise ToolError("path must be a relative path inside the repo")
    full = os.path.realpath(os.path.join(BASE, v))
    if not full.startswith(os.path.realpath(BASE) + os.sep):
        raise ToolError("path is outside the repo")
    return v


def run_tests(args=None):
    # no arguments accepted from chat: the test target is always the unit suite
    if args:
        raise ToolError("run_tests takes no arguments")
    try:
        r = subprocess.run([_python(), "-m", "pytest", "-q", "tests"], cwd=BASE,
                           capture_output=True, text=True, timeout=TEST_TIMEOUT, shell=False)
        out = _trim((r.stdout or "") + (r.stderr or ""))
        return {"exit_code": r.returncode, "output": out, "passed": r.returncode == 0}
    except subprocess.TimeoutExpired:
        return {"exit_code": None, "output": f"tests timed out after {TEST_TIMEOUT}s", "passed": False}


def git_status(args=None):
    if args:
        raise ToolError("git_status takes no arguments")
    return {"output": _git(["status", "--short", "--branch"])}


def git_log(args=None):
    args = args or {}
    n = _check_int("n", args.get("n"), 1, 50, 10)
    return {"output": _git(["log", "--oneline", f"-{n}"])}


def git_diff(args=None):
    args = args or {}
    path = _check_path(args.get("path"))
    stat = bool(args.get("stat", True))
    cmd = ["diff", "--stat"] if stat else ["diff"]
    if path:
        cmd += ["--", path]
    out = _git(cmd)
    return {"output": out or "(no changes)"}


def db_backup(args=None):
    if args:
        raise ToolError("db_backup takes no arguments")
    if not os.path.exists(DB_PATH):
        raise ToolError("crm.db not found")
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = os.path.join(BACKUP_DIR, f"crm-{stamp}.db")
    shutil.copy2(DB_PATH, dest)
    removed = rotate_backups(BACKUP_DIR, BACKUP_KEEP)
    return {"file": dest, "removed": removed}


def rotate_backups(folder, keep):
    """Delete all but the newest `keep` crm-*.db files. Returns the names removed."""
    files = sorted(f for f in os.listdir(folder) if re.match(r"^crm-.*\.db$", f))
    extra = files[:-keep] if keep > 0 else files
    for f in extra:
        os.remove(os.path.join(folder, f))
    return extra


def health_check(args=None):
    args = args or {}
    port = _check_int("port", args.get("port"), 1, 65535, 8000)
    url = f"http://127.0.0.1:{port}/healthz"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return {"status": r.status, "ok": r.status == 200}
    except Exception as e:
        return {"status": None, "ok": False, "error": str(e)[:200]}


# name -> (function, allowed argument names)
REGISTRY = {
    "run_tests": (run_tests, ()),
    "git_status": (git_status, ()),
    "git_log": (git_log, ("n",)),
    "git_diff": (git_diff, ("path", "stat")),
    "db_backup": (db_backup, ()),
    "health_check": (health_check, ("port",)),
}


def run(name, args=None, actor="tool"):
    """Run a registered tool by name. Unknown names and unknown argument keys are refused."""
    if name not in REGISTRY:
        raise ToolError(f"unknown tool '{name}'")
    fn, allowed = REGISTRY[name]
    args = args or {}
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    extra = set(args) - set(allowed)
    if extra:
        raise ToolError(f"unexpected argument(s): {', '.join(sorted(extra))}")
    try:
        result = fn(args if allowed else None)
        audit.record(actor, f"tool:{name}", "", "ok")
        return result
    except ToolError as e:
        audit.record(actor, f"tool:{name}", "", f"refused: {str(e)[:60]}")
        raise
