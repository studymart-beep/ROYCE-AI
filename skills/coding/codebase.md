---
name: codebase
description: How to inspect an unfamiliar repo fast: tree, entrypoints, data flow, where state lives.
---
# Codebase inspection

Goal: answer "what does this do and where" before touching anything.

1. Tree first. List top-level dirs and key files, skip vendored and generated dirs (.venv, node_modules, __pycache__, .git, dist, build). Note the language and the package layout (app/, tests/, skills/, docs/).
2. Entrypoints. Find what starts the process: `__main__`, `main.py`, `bot` loops, `if __name__ == "__main__"`, Procfile, Dockerfile, package.json scripts. Each entrypoint tells you one runtime.
3. Data flow. Follow one real request from entrypoint to storage: input parser -> router/planner -> executor -> persistence (db, json files) -> output. Name each hop with file and function.
4. State. List every place state lives: database file, JSON files, env vars, in-memory caches, background threads. State is where bugs hide.
5. Config and secrets. Read config loaders and .env handling by key name only. Never print values.
6. Tests. Check tests/ to see what is covered and what the test runner command is.
7. Report. Give the user: purpose in one line, entrypoints, the request path, state locations, and open questions. Cite file paths, not guesses.

Rules: read before concluding; quote a line number or function name for each claim; if a file is missing or unreadable, say so.
