# Royce AI

In-house AI for Royce Marketing Agency: ad scripts, CRM, campaign reporting, and a Telegram bot that talks like a colleague.

## Run

    pip install -r requirements.txt
    uvicorn app.main:app --port 8000
    python -m app.telegram_bot

Env keys in `.env`: GEMINI_API_KEY, OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, PUBLIC_URL.

## Collectors (live ad campaign data)

Collectors pull real campaign insights from ad platforms. Each one needs an access token and an account id. They are saved to `collectors.json` at the project root. That file is git-ignored and written with owner-only permissions. Tokens are never returned by the API; the reporting page only shows configured or not.

Meta Ads (Marketing API v21):
1. Go to Meta Business Settings > Users > System users, and create a system user (Admin).
2. Assign your ad account to that system user with at least view/analyze permission.
3. Click Generate token, pick your app, and tick `ads_read` (plus `business_management` if you use it). Copy the token.
4. The account id is the ad account number shown in Ads Manager, without the `act_` prefix.
5. Paste both on the connectors page, then press test.

TikTok Ads (TikTok Business API v1.3):
1. Create a developer app at TikTok for Business (business-api.tiktok.com developer portal) and get it approved for the Marketing API.
2. Grant the Reporting (read) scope, then authorize it against your advertiser account to get an access token.
3. The advertiser id is your TikTok Ads Manager advertiser id.
4. Paste both on the connectors page, then test.

Google Ads: coming. It needs an OAuth client (client id, secret, and refresh token) plus a Google Ads developer token. The collector is a stub that refuses with a clear "connect via OAuth first" message until that is built, so it never returns fake data.

API routes:
- GET /api/collectors: configured status per platform (no tokens)
- PUT /api/collectors with {name, token, account_id}: save
- POST /api/collectors/{name}/test: live check of the token
- POST /api/collectors/{name}/report with {preset}: normalized rows for one platform
- POST /api/report/all with {preset}: merged rows from every configured collector

Presets: last_7d (default), last_14d, last_30d, yesterday, today, this_month (TikTok only supports explicit dates; the collector maps these).

Telegram: ask for a report or campaign performance, and the bot pulls the rows from every configured collector, then has the LLM write a client-ready summary from those real numbers. If nothing is connected, it tells you which token to add.

## Mini app (v5, view-first)

The mini app is read-only: Generated work (every ad package, newest first, stored in work.json), CRM leads, the campaign report, and connectors. You do the work by telling Royce in the chat. Each generated package is recorded in work.json via GET /api/work.

## Task memory (never forget)

Work you give Royce in chat ("make...", "find...", "pull...") is written to a task list stored in `tasks.json` at the project root (git-ignored). Each task has an id, chat id, description, status (open, done, unavailable), created_at, and a note.

- Royce confirms the task, does it, and marks it done.
- If something is not available (for example an ad platform is not connected yet), the task is kept as unavailable. The bot re-checks unavailable tasks every 10 minutes. When the requirement is met, it messages you "the thing u asked for earlier is now available" with the original request, then does the work.
- Failed work is also kept and retried for about 24 hours.
- /tasks lists your tasks with status.

The task store lives in `app/tasks.py`, and the bot loop in `app/telegram_bot.py` runs the retry check.

## More connectors (gmail, facebook, x, instagram, linkedin)

Gmail (read-only, live): paste an app password and your gmail address on the connectors page. Create the app password at Google Account > Security > 2-Step Verification > App passwords. Royce logs in over IMAP (imap.gmail.com) and counts recent and unread mail and lists the most frequent unread senders. Ask in chat ("check my gmail inbox") or use the inbox summary. Gmail is an inbox connector, not an ad platform, so it is never used for campaign reports.

Facebook, X, Instagram, LinkedIn: the token is stored and the row shows connected. Live insights are coming soon, so these connectors pull no data and never return made-up numbers. Pasting a token now means the insights step only needs the code later.

Connectors that need tokens from you: Gmail app password; Facebook Page or Meta Graph token; X API bearer token; Instagram Graph API token; LinkedIn API token.

## Image understanding and generation

Understanding: send a photo to the bot. Royce downloads it through Telegram getFile and sends it to Gemini vision (image_url parts on the same OpenAI-compatible endpoint, with OpenRouter as failover). With no caption it describes the image. With a caption it uses the photo for the request, for example "make an ad like this".

Generation: /image <prompt>, or chat such as "generate an image of ...". app/imagegen.py calls OpenRouter with the modalities image and text, default model google/gemini-2.5-flash-image-preview (set IMAGE_MODEL in .env to change it). If the provider fails, Royce tells you which service failed and keeps the request on the task list to retry.

Tests: the task store is covered by a quick check (add, complete, unavailable lookups). The retry path was run against a stubbed connector before commit.

## Lead brain (follow-ups and nudges)

/followup <lead name or id> drafts a warm, short follow-up for that lead (Nigerian professional tone, max 3 sentences) using the lead's business, notes and status, then asks if you want it tweaked. Drafts come from app/leadbrain.py draft_followup(lead, context) through the same LLM failover.

Mini app: each lead row on the leads page has a "draft followup" link that calls GET /api/leads/{id}/followup and shows the draft inline.

Daily nudges: the bot checks at most once an hour. If any open lead has a next_followup due today or overdue, it sends one message listing them with a one-line reason each, at most once per Lagos day. The nudge goes to the last private chat that messaged the bot (falls back to the owner chat id). State lives in nudge_state.json (git-ignored).

## Ad doctor (teardowns)

/doctor <paste ad copy or campaign numbers> tears it down: what's weak, what to fix, and 3 specific changes. Copy goes in as ad_copy; numbers that mention ctr/cpc/spend/impressions/clicks are read as campaign_numbers. /doctor with nothing after it, or chat like "check my campaigns", pulls the last 7 days from connected collectors first and diagnoses those real rows. Chat detection also catches "roast this ad" and "why is my ctr bad". Logic is in app/doctor.py diagnose(text, kind). The reports page has a "diagnose" button that POSTs the current rows to POST /api/diagnose.

## Content engine (weekly calendars)

/content <client> | <industry> | <platform> builds a 7-day calendar (day, format, idea, hook, cta) with strict JSON from the LLM and saves it to generated work as type content_calendar. Chat like "content plan for X" asks for the industry and platform in one line. Logic is in app/content.py weekly_calendar(client, industry, platform). The generated work page shows calendars as cards next to ad packages.

## Voice replies (tts)

/voice <text> or chat like "reply in voice" / "say this in voice" makes a voice note (ogg opus, sendVoice). app/voice.py tts(text) tries openai_tts first (needs OPENAI_API_KEY, skipped when absent), then openrouter_tts (OpenRouter audio model, default openai/gpt-audio-mini, needs OpenRouter audio access and balance). If neither works, the bot replies "voice needs an openai key or openrouter audio model access" and keeps running. Override models with OPENAI_TTS_MODEL, OPENROUTER_AUDIO_MODEL, OPENAI_TTS_VOICE, OPENROUTER_TTS_VOICE.

## Skills (marketing playbooks)

Six markdown playbooks live in skills/ (direct-response, brand-strategy, social-growth, funnel-design, reporting-storytelling, negotiation-closing). Each has a front matter block with name and description. app/skills.py lists them (list_skills), reads one (get_skill), and picks the most relevant ones for a message by whole-word keyword match (select_skills). Chat injects at most 2 playbooks per turn (skill_block). The /followup, /doctor and /content engines each get their default playbook (engine_skill): followup uses negotiation-closing, doctor uses direct-response (reporting-storytelling for campaign numbers), content uses social-growth. /skills in the bot lists the playbooks with one-line descriptions. To add one, drop a new .md file in skills/ with name and description front matter, and add its keywords to KEYWORDS in app/skills.py.

## Phase 2: engineering reliability

- Tools layer (app/agent/tools.py): whitelisted registry. run_tests, git_status, git_log, git_diff, db_backup, health_check. No arbitrary shell from user text.
- Scheduled backups and self-check (app/agent/jobs.py): backup every 6 h (keeps newest 10 in backups/), self-check every 1 h written to logs/health.json. Run `/health` in the bot for the latest status.
- Audit log (app/agent/audit.py): logs/audit.jsonl, append-only, no message content or secrets.
- Coding playbooks: skills/coding/codebase.md and debugging.md, added to the bot prompt for code and debug questions.
- Tests: `.venv/bin/python -m pytest -q tests`.

## Royce AI workspace (web app, installable on Windows)

The workspace is a single-page web app served by the same FastAPI server. It is not an Electron or native
desktop build. In Chrome or Edge on Windows you can install it as an app (see below), which gives it its own
window and launcher icon. No native .exe is built or shipped.

Set an owner token in .env (any long random string). The workspace refuses its APIs without it, so the public
tunnel never exposes chat, memory or approvals:

WORKSPACE_TOKEN=<a long random string you choose>

Run the backend with the existing uvicorn command (see Run above). Open the workspace at /workspace on your
server (or your tunnel URL plus /workspace), paste the same token in Settings, and the status bar shows whether
a provider is ready.

Install as a Windows app:
1. Open the workspace URL in Chrome or Edge.
2. Chrome: menu (three dots), then Save and share, then Install page as app. Edge: menu (three dots), then Apps, then Install this site as an app.
3. Click Install. Royce AI opens in its own window and appears in the Start menu.
The install needs the server reachable at that URL. The service worker caches only the static shell, never owner data.

Local development: run the server, edit app/static/workspace.html, workspace.css or workspace.js, and refresh.
The owner token is kept only for the current browser tab (session storage).


## Phone app (PWA at /m)

Open /m on an Android or iPhone browser. It shares the same backend as /workspace: chat, chats list,
tasks (polls every 3 s while one is running), approvals (approve and reject), memory (search and
forget), provider status (/readyz) and the owner token. Add it to the home screen from the browser
menu (Android: Install app or Add to Home screen. iPhone: Share, then Add to Home Screen). It opens
standalone with the Royce icon.

Offline: the app shell (/m, the css and js, icons) is cached by the service worker. /api is always
the network. With no connection the app says "you're offline. AI needs a connection." and never
shows a fake answer. The service worker is served at /sw.js with scope "/".

Test locally: `uvicorn app.main:app --port 8000`, then open http://localhost:8000/m on the phone via
your LAN IP (the phone must reach the server). For a real service worker on a phone you need HTTPS
(a tunnel or Render). On a desktop browser, http://localhost:8000/m works.

## Desktop app (Tauri, Windows)

desktop/ is a Tauri v2 project that opens the backend workspace in a native Windows window
(1280x800, min 1024x640). It holds no keys and no token. The backend URL comes from ROYCE_BACKEND_URL
at build and launch. See desktop/README.md for the exact steps.

Status: the project is complete and validated for config and wiring. The Windows .msi/.exe has not
been built: Tauri Windows builds must run on Windows, and this repo was prepared on Linux.

## Deployment prep (nothing deployed)

Nothing is deployed. render.yaml describes one Render web service that serves the API, /workspace,
/m and the PWA. Env vars are listed by name only (values go in the Render dashboard).

- ROYCE_DATA_DIR: point at the persistent disk (render.yaml uses /var/data/royce). Without it, data
  files stay in the repo root, exactly as before.
- ALLOWED_ORIGINS: comma-separated origins allowed to call the API cross-origin, for example
  https://royce-ai.onrender.com,https://your-app.vercel.app. No wildcard, no credentials header.
  Default (unset): http://localhost:8000 and http://127.0.0.1:8000 only.
- Auth: owner token (WORKSPACE_TOKEN). Each client sends it as the x-workspace-token header.
  The workspace and /m store it only in the browser or device localStorage. No frontend bundle
  contains a secret.

Simplest path: one Render service serves both the PWA and the API, so no CORS is needed.
Optional: host the static files separately (e.g. Vercel) and set ALLOWED_ORIGINS to that origin.
Nothing here is deployed; you create the Render service and set the env vars yourself.

## Telegram groups (add the bot to a group)

Add the bot to a group or supergroup. It stores the text of messages it can see, from the time it joined.
Group commands: /search <keyword>, /recap [hours] (default 24, max 168), /help, /deletemydata yes.
Admin-only commands (checked live with Telegram, never trusted from chat claims): /setwelcome <text>,
/welcome on|off, /welcomestatus, /setretention <hours>, /groupconfig, /flags, /flag <id> safe|dismiss|escalate,
/allowadd <user id>, /allowremove <user id>.

Moderation only flags messages for admin review (suspicious links, flooding, ads). It never bans, deletes or
restricts anyone. Admins and allowlisted users are not checked for flood or ads, but links are still checked.

What the bot cannot do, by design of the Telegram bot API:
it cannot read the owner's private chats with other people, and it cannot read messages in groups it is not in.
It only sees its own private chats and the groups it is a member of. In groups it stores text only, not photos,
files or service events. Edits update the stored text. Stored messages expire after the group's retention
(default 720 hours) and anyone can delete their own stored messages with /deletemydata yes.
Deletions and flags are audited by count and id only, never by content.

Channels: the bot stores channel posts (text, and edits) the same way as groups, with the channel title as the name.
Telegram only sends a bot the channel posts it can see, so channel history is only readable from the time the bot
was added as an admin. Earlier posts cannot be read.

Owner sends: in your private chat with the bot, say "tell the group <name>: <text>" or "post to the channel <name>: <text>".
The bot shows the exact text and waits for /approve <id>. Only the owner can queue or approve. Nothing is posted until you approve,
and the audit log records the target id and outcome, never the text.
