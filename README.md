# Daily Tech News — Telegram Bot

Pushes the top Hacker News stories to your Telegram every morning. Runs free on
GitHub Actions (cron); your PC does not need to be on.

## What it does
- Pulls the top stories from Hacker News (no API key needed)
- Optionally summarizes each article in one line with Google Gemini's free tier
  (needs `GEMINI_API_KEY`); pages that can't be read fall back to headline-only
- Groups stories into topic sections with source domains, scores, and comments
- Sends a formatted digest to your chat via the Telegram Bot API

## One-time setup (3 manual steps)

### 1. Create the bot + get the token
1. Open Telegram, search for **@BotFather**.
2. Send `/newbot`, pick a name and a username ending in `bot`.
3. BotFather replies with a **token** like `123456:ABC...`. Save it.

### 2. Get your chat ID
1. Send any message to your new bot (search its username, tap Start, say "hi").
2. Visit this URL in a browser, pasting your token:
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find `"chat":{"id":123456789,...}` — that number is your **chat ID**.

### 3. Add GitHub Secrets
In your GitHub repo: **Settings → Secrets and variables → Actions → New secret**
- `TELEGRAM_BOT_TOKEN` — the token from step 1
- `TELEGRAM_CHAT_ID` — the id from step 2
- `GEMINI_API_KEY` — *(optional)* free key from https://aistudio.google.com for
  per-article summaries. Without it, the digest sends headlines only.

> **Model note:** the default is `gemini-2.5-flash`. Some projects have zero
> free quota on `gemini-2.0-flash`; if summaries fail, set a `GEMINI_MODEL`
> secret to a model your key can access.

Then go to the **Actions** tab → **Daily Tech News** → **Run workflow** to test it
immediately. After that it runs on the schedule in
`.github/workflows/daily-news.yml` (default 08:00 IST — edit the cron to change).

## Test it locally first (optional)
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit .env with your token + chat id
# load .env and run:
python bot.py
```
(Local run needs the env vars set; `.env` is just a place to keep them.)

## Changing the send time
GitHub cron is in **UTC**. Current `30 3 * * *` = 03:30 UTC = 09:00 IST.
Use [crontab.guru](https://crontab.guru) to pick a new value.
