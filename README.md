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
