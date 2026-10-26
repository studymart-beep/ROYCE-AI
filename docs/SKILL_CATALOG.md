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
| 15 | Codebase understanding | planned | none | phase 6 candidate. |
| 16 | Autonomous coding | planned | none | blocked on a decision: autonomous commits to a production repo need the owner's explicit approval. |
| 17 | Debugging and root-cause analysis | planned | none | |
| 18 | Test generation and execution | planned | tests/unit/ (phase 1 baseline) | the phase 1 test suite exists; automated generation does not. |
| 19 | Code review | planned | none | |
| 20 | Git and version control | planned | git used manually in the repo | no agent-driven git yet. |
| 21 | (cut off in the brief) | unknown | none | name not received. |

## Items 22 to 104

Not mapped. The brief did not include their names. Send the full list to extend this file.

## Blocked right now

None of items 1 to 21 are blocked on a missing external account. Item 16 is blocked on an owner decision about autonomous commits.
