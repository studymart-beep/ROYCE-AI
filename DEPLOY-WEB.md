# Royce AI web app: deploy notes

The web app is plain static HTML, CSS and JS served by the FastAPI backend. It calls the backend
at relative `/api/ws/*` paths, and it has a single-page layout with no client-side router.

## Option A (recommended): one Render service, nothing new

The existing Render service (`royce-ai`, see `render.yaml`) already serves everything:

- https://royce-ai.onrender.com/workspace (desktop workspace)
- https://royce-ai.onrender.com/m (mobile PWA, bottom tabs)
- https://royce-ai.onrender.com/api/ws/* (the API, open access: no token)

Same origin means no CORS setup. There is no login and no token: the app loads straight in.

**OPEN ACCESS WARNING (owner decision):** anyone who has the URL can use the AI and read CRM and memory.
The obscurity of the URL is the only protection. Re-enable options for later: a token check on the router
in `app/workspace_api.py` (fail closed when WORKSPACE_TOKEN is unset), or Render's own protection or a
login proxy in front of the service.

Only if you also serve the frontend from another origin (option B) must you add that origin:
set `ALLOWED_ORIGINS` on Render to a comma-separated list, no trailing slash, no `*`, for example
`https://your-app.vercel.app`. Unset means the localhost defaults only.

## Option B (optional): separate static host on Vercel

Use this only if you want the frontend on its own domain. The backend stays on Render.

The frontend calls the API through `window.ROYCE_API_BASE`. It defaults to `""` (same origin).
`app/static/config.js` sets `window.__ROYCE_API_BASE__` before the app loads, so for a separate
origin you only need to change that one file.

Steps (owner's Vercel account, done by the owner):

1. In Vercel, click Add New Project and import the GitHub repo `studymart-beep/ROYCE-AI`.
   Framework preset: Other. Root directory: repo root. Build command: leave empty.
   Output directory: `app/static`. `vercel.json` already sets this and the rewrites.
2. Edit `app/static/config.js` in the repo to a single line:
   `window.__ROYCE_API_BASE__ = "https://royce-ai.onrender.com";`
   Commit and push. Vercel redeploys on push. This is the only file that differs per host.
3. Note the Vercel URL (for example `https://royce-ai-web.vercel.app`, shown after the first deploy).
4. On Render, open the `royce-ai` service, Environment, and set `ALLOWED_ORIGINS` to that exact
   origin, for example `https://royce-ai-web.vercel.app`. Add more origins comma-separated if
   needed, with no trailing slash. Save, and Render restarts the service.
5. Open `https://<vercel-url>/workspace` (or `/m`). There is no token prompt now, and check
   that the Sessions page loads. A CORS failure shows as a blocked request in the browser console,
   which means the origin in `ALLOWED_ORIGINS` does not match exactly.

Routing: the app is a single page per file. `/workspace` maps to `workspace.html`, `/m` maps to
`mobile.html`, and `/offline` to `offline.html` (see `vercel.json`). Navigation inside the
workspace uses the URL hash (`#bots`, `#library`, and so on), so refreshing a page never hits a
missing route. The static host needs no SPA fallback.

Limits of option B (honest):
- The service worker precaches `/static/...` shell paths. `vercel.json` rewrites those to the root
  files, but offline caching on the Vercel origin is untested. The app works online either way.
- Vercel was not deployed or tested by this repo. The steps are written from the config files
  and are unverified until the owner runs them.
- No token is sent. The API is open access, so anyone with the Vercel or Render URL can use it (see the warning above).

## Security checks (this repo)

- No tokens or keys are in `app/static/`. The workspace has no token prompt (open access). Protection is URL obscurity only.
- CORS uses an explicit origin list (`allowed_origins()` in `app/main.py`). `*` is dropped and
  `allow_credentials` is `False`, so the wildcard-plus-credentials combination cannot happen.
- Tests: `tests/unit/test_api_base.py` covers the config route, the default same-origin behavior,
  the listed-origin preflight, and the no-wildcard rule.
