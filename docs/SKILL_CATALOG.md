# Royce AI skill catalog

Status key: implemented = working code in the repo today (module named). planned = not built yet. blocked = needs an external account, key or decision from the owner.

Scope note: the capability list in the phase 1 brief was cut off after item 21 (the message ends at "21.…"). Items 22 to 104 are not mapped here because their names were not received. Send the full list and this file gets extended in place. Nothing below is invented beyond the visible items.

## 1. Core agent and intelligence

| # | Capability | Status | Maps to | Note |
|---|---|---|---|---|
| 1 | Task planning and decomposition | implemented (phase 1) | app/agent/planner.py | LLM plan of 1 to 5 steps, falls back to one llm step on any error. Not yet wired into the bot chat path. |
| 2 | Intelligent model routing | implemented (phase 1) | app/agent/router.py | capability tags per provider, in-memory health, used by app/llm.py. |
| 3 | Gemini-primary / OpenRouter automatic failover | implemented | app/llm.py, app/agent/router.py, app/config.py | failover existed before; now routed through the router with health demotion. Label differs slightly: failover was already live, phase 1 added health tracking. |
| 4 | Automatic retry and error recovery | implemented | app/agent/orchestrator.py, app/telegram_bot.py (tasks.json retries) | orchestrator retries up to 2 times with backoff. Bot retries unavailable tasks every 10 min (existing). |
| 5 | Result verification | implemented (phase 1) | app/agent/orchestrator.py, app/content.py _validate | orchestrator checks each step's output shape and non-emptiness. content.py already validated 7 days. |
| 6 | Persistent memory and retrieval | implemented (phase 1) | app/agent/memory.py | json store, keyword search, bot saves explicit facts. Vector/semantic retrieval is not included. |
| 7 | Skill discovery and loading | implemented | app/skills.py, skills/*.md | 6 marketing playbooks, max 2 injected per reply by keyword match. |
| 8 | Context and token management | implemented (partial) | app/telegram_bot.py (HIST_MAX=20), app/llm.py (max_tokens 2500) | history is trimmed to 20 turns. No real token counting. Label differs: this is trimming, not token budgeting. |
| 9 | Permission and safety control | implemented (phase 1) | app/agent/permissions.py, app/agent/approvals.py | action classes; send_message/publish/delete need /approve. |
| 10 | Natural-language command interpretation | implemented (partial) | app/telegram_bot.py handle_chat | regex intent routing plus an LLM task classifier. Not a general intent model. |
| 11 | Conversation-to-action execution | implemented | app/telegram_bot.py (run_new_task, run_task), app/agent/orchestrator.py | bot turns chat into tasks. orchestrator runs multi-step plans (not yet wired to the bot). |
| 12 | Progress and status reporting | implemented (partial) | app/telegram_bot.py (/tasks, "on it" acks) | /tasks lists open, done and unavailable items. No live progress for long jobs. |
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
