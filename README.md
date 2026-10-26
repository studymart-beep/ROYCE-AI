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
5. Paste both on the reporting page under "connected platforms", then press test.

TikTok Ads (TikTok Business API v1.3):
1. Create a developer app at TikTok for Business (business-api.tiktok.com developer portal) and get it approved for the Marketing API.
2. Grant the Reporting (read) scope, then authorize it against your advertiser account to get an access token.
3. The advertiser id is your TikTok Ads Manager advertiser id.
4. Paste both on the reporting page, then test.

Google Ads: coming. It needs an OAuth client (client id, secret, and refresh token) plus a Google Ads developer token. The collector is a stub that refuses with a clear "connect via OAuth first" message until that is built, so it never returns fake data.

API routes:
- GET /api/collectors: configured status per platform (no tokens)
- PUT /api/collectors with {name, token, account_id}: save
- POST /api/collectors/{name}/test: live check of the token
- POST /api/collectors/{name}/report with {preset}: normalized rows for one platform
- POST /api/report/all with {preset}: merged rows from every configured collector

Presets: last_7d (default), last_14d, last_30d, yesterday, today, this_month (TikTok only supports explicit dates; the collector maps these).

Telegram: ask for a report or campaign performance, and the bot pulls the rows from every configured collector, then has the LLM write a client-ready summary from those real numbers. If nothing is connected, it tells you which token to add.
