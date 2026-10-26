# Royce AI skill catalog

Status key: implemented = working code in the repo today (module named). planned = not built yet. blocked = needs an external account, key or decision from the owner.

Scope note: the capability list in the phase 1 brief was cut off after item 21 (the message ends at "21.…"). Items 22 to 104 are not mapped here because their names were not received. Send the full list and this file gets extended in place. Nothing below is invented beyond the visible items.

## 1. Core agent and intelligence

| # | Capability | Status | Maps to | Note |
|---|---|---|---|---|
| 1 | Task planning and decomposition | implemented (phase 3) | app/agent/planner.py, app/agent/chatplan.py | LLM plan of 1 to 5 steps, validated against registered engines, run step by step from the chat path for explicit multi-step asks only. Simple messages stay on the direct chat path. |
| 2 | Intelligent model routing | implemented (phase 3) | app/agent/router.py | capability tags, cooling or invalid providers are skipped (not just demoted), retryable vs non-retryable errors classified, in-memory health. |
| 3 | Gemini-primary / OpenRouter automatic failover | implemented | app/llm.py, app/agent/router.py, app/config.py | failover existed before; now routed through the router with health demotion. Label differs slightly: failover was already live, phase 1 added health tracking. |
| 4 | Automatic retry and error recovery | implemented | app/agent/orchestrator.py, app/telegram_bot.py (tasks.json retries) | orchestrator retries up to 2 times with backoff. Bot retries unavailable tasks every 10 min (existing). |
| 5 | Result verification | implemented (phase 1) | app/agent/orchestrator.py, app/content.py _validate | orchestrator checks each step's output shape and non-emptiness. content.py already validated 7 days. |
| 6 | Persistent memory and retrieval | implemented (phase 1) | app/agent/memory.py | json store, keyword search, bot saves explicit facts. Vector/semantic retrieval is not included. |
| 7 | Skill discovery and loading | implemented | app/skills.py, skills/*.md | 6 marketing playbooks, max 2 injected per reply by keyword match. |
| 8 | Context and token management | implemented (partial) | app/telegram_bot.py (HIST_MAX=20), app/llm.py (max_tokens 2500) | history is trimmed to 20 turns. No real token counting. Label differs: this is trimming, not token budgeting. |
| 9 | Permission and safety control | implemented (phase 1) | app/agent/permissions.py, app/agent/approvals.py | action classes; send_message/publish/delete need /approve. |
| 10 | Natural-language command interpretation | implemented (partial) | app/telegram_bot.py handle_chat | regex intent routing plus an LLM task classifier. Not a general intent model. |
| 11 | Conversation-to-action execution | implemented | app/telegram_bot.py (run_new_task, run_task), app/agent/orchestrator.py | bot turns chat into tasks. orchestrator runs multi-step plans (not yet wired to the bot). |
| 12 | Progress and status reporting | implemented (phase 3, partial) | app/agent/chatplan.py, app/telegram_bot.py | per-step "step n of m done" for planned tasks, plain final summary, /tasks lists open items. Background jobs have no live progress. |
| 13 | Error explanation and recovery guidance | implemented (partial) | app/telegram_bot.py (error messages, "kept on my list") | errors are surfaced with the reason and the task is kept for retry. |
| 14 | User preference adaptation | planned | app/agent/memory.py (preferences saved by heuristic) | heuristic captures "I prefer" and similar. No learned preference model yet. |
| 15 | Codebase understanding | implemented (phase 2) | skills/coding/codebase.md, app/skills.py coding_block | playbook injected for code questions. Not an agent tool yet: the bot does not run repo scans itself. |
| 16 | Autonomous coding | planned | none | blocked on a decision: autonomous commits to a production repo need the owner's explicit approval. Phase 2 commits were made on the owner's direct instruction and are not autonomous. |
| 17 | Debugging and root-cause analysis | implemented (phase 2) | skills/coding/debugging.md, app/skills.py coding_block | reproduce, isolate, minimal fix, verify method injected for debug questions. No automatic debugger. |
| 18 | Test generation and execution | implemented (partial, phase 2) | app/agent/tools.py run_tests, tests/unit/ | run_tests runs the unit suite via a whitelisted pytest call (300 s cap). Test generation not built. |
| 19 | Code review | planned | none | |
| 20 | Git and version control | implemented (partial, phase 2, read-only) | app/agent/tools.py git_status, git_log, git_diff | read-only. No commit or push tool. |
| 21 | (cut off in the brief) | unknown | none | name not received. |

## Phase 2 engineering reliability (summary)

- Tools layer, whitelisted: app/agent/tools.py (run_tests, git_status, git_log, git_diff, db_backup, health_check). Unknown names and extra args are refused.
- Backups: db_backup copies crm.db to backups/crm-TIMESTAMP.db, keeps newest 10. Runs every 6 h from app/agent/jobs.py.
- Self-check: app/agent/jobs.py runs hourly (LLM providers configured, DB reachable, disk space), writes logs/health.json (latest only). /health in the bot shows it.
- Audit: app/agent/audit.py appends logs/audit.jsonl (actor, action, target, outcome; redacted; no message content).
- Coding skills: skills/coding/codebase.md, skills/coding/debugging.md.
- Gaps: no owner-approved autonomous commits; git push depends on sandbox credentials.

## Items 22 to 104

Not mapped. The brief did not include their names. Send the full list to extend this file.

## Blocked right now

None of items 1 to 21 are blocked on a missing external account. Item 16 is blocked on an owner decision about autonomous commits.

## Phase 3 reliability and chat planning (summary)
- A1 router cooldown: a provider in cooldown (120 s after a transient error) or marked invalid (auth rejected) is skipped, not called. Bad requests (400/422) fail at once with no failover. All-unavailable raises an error with the wait time. Status: implemented, unit tested. Health is in memory and resets on restart.
- A2 budget: one shared 30 per hour per chat, reserved before every provider attempt (failover, retries, image and voice included). Unbound work shares a system bucket with the same cap. Thread safe. Status: implemented, concurrency tested. Not persisted across restarts.
- A3 /health: states not_configured, validated, unavailable (429, timeout, outage: not proven invalid), invalid (401/403). Cheap GET of the model list, cached 5 minutes, keys never printed. Status: implemented, tested with mocked providers. No live check was run against the real providers in tests.
- B chat planning: explicit multi-step asks only (sequencing words or a numbered list). Plan validated, approval pauses the task, /deny stops it, a failed step blocks later steps, interrupted steps are never repeated. Status: implemented, tested. Limitation: the followup engine is marked consequential, but no outbound send executor exists yet, so an approved followup produces a draft only.
- C UX: per-step progress, plain-text failure summary, SIGTERM stops between steps, restart marks interrupted tasks failed. Status: implemented. Background jobs (jobs.py) are not part of the planner.

## Phase 4: desktop workspace (status)

- Desktop interface: implemented as a browser workspace at /workspace (sidebar with conversations, real chat through /api/ws/chat, planner task panel with polling, approvals with approve/reject, tasks, memory with forget, connections and provider/budget status, settings). Runs on the existing FastAPI server and shares the bot's modules (chatplan, approvals, memory, budget, audit). Gated by WORKSPACE_TOKEN. Status: implemented, tested at the API level.
- Installable app: implemented as a PWA (manifest plus service worker that caches only the static shell). Installed from Chrome or Edge on Windows via Install app.
- Native Windows executable: not built. No Electron or packaged desktop app. The desktop experience is the installed PWA window.

## Phase 4 extension: mobile PWA, desktop client, deployment prep (status)

- Mobile PWA at /m (app/static/mobile.html, mobile.css, mobile.js). Implemented, tested by TestClient
  and static checks. Not tested on a real phone here.
- Service worker at /sw.js: cache-first for the shell only, network-only for /api, offline page at
  /offline. Implemented, tested for scope and cache rules.
- Desktop Tauri v2 client (desktop/). Config and wiring validated as JSON and paths. Windows exe/msi
  NOT built (requires Windows).
- Persistence: ROYCE_DATA_DIR (app/paths.py). Implemented.
- CORS: ALLOWED_ORIGINS (main.py). Implemented, tested for allowed and disallowed origins.
- Deployment: render.yaml added. NOTHING DEPLOYED.
