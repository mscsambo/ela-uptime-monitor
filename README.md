# Free website uptime monitor → Telegram alerts

Checks your websites every 5 minutes using GitHub Actions (free) and messages
you on Telegram when a site goes **down**, **stays down** (reminder every hour),
and when it **recovers**.

## Setup (about 15 minutes)

### 1. Create the Telegram bot
1. In Telegram, open **@BotFather** and send `/newbot`.
2. Choose a name and a username ending in `bot` (e.g. `mysites_alert_bot`).
3. BotFather replies with a **token** like `123456789:ABCdef...`. Keep it private.
4. Open your new bot and press **Start** (or send it "hi"). This is required, or
   the bot is not allowed to message you.

### 2. Get your chat ID
1. In your browser, open (replace `<TOKEN>` with your token):
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
2. Find `"chat":{"id":123456789` — that number is your **chat ID**.
   - If you see `"result":[]`, send the bot another message and refresh.
   - Want alerts in a group? Add the bot to the group, send a message there,
     and use the group's ID (it starts with `-`).

### 3. Create the GitHub repository
1. Sign up / sign in at github.com.
2. Click **New repository** → name it e.g. `uptime-monitor` → choose
   **Public** → Create.
   - Public = unlimited free Actions minutes. Your token stays hidden in
     Secrets; only your site list is visible.
   - Private also works, but every-5-minute runs use ~2,000+ minutes/month,
     which is right at the free limit for private repos.
3. Click **uploading an existing file** and upload `monitor.py`, `sites.txt`
   and `README.md`.
4. The workflow file must be at `.github/workflows/uptime.yml`. Easiest way:
   **Add file → Create new file**, type the name
   `.github/workflows/uptime.yml`, paste the contents of `uptime.yml`, and
   commit.

### 4. Add your secrets
Repo → **Settings → Secrets and variables → Actions → New repository secret**:
- `TELEGRAM_BOT_TOKEN` = your bot token
- `TELEGRAM_CHAT_ID` = your chat ID

### 5. Add your domains
Edit `sites.txt` on GitHub (pencil icon) and replace the 5 example lines with
your real domains. Commit.

### 6. Test it
Go to **Actions → Uptime monitor → Run workflow**. Within a minute you should
get a Telegram message listing all 5 sites with ✅ or 🔴. From then on it runs
automatically every 5 minutes.

## What the alerts look like
- 🔴 **DOWN**: the site failed twice in a row (15 seconds apart), so short blips
  don't wake you up.
- ⚠️ **STILL DOWN**: a reminder every 60 minutes while it stays down.
- ✅ **RECOVERED**: back up, with how long it was down.

A site counts as **down** if it doesn't answer within 20 seconds, the
connection fails, the SSL certificate is invalid, or it returns an HTTP error
(400 or higher, e.g. 500, 503, 404).

## Changing settings
Edit the top of `monitor.py`:
- `REMIND_EVERY_MINUTES = 60`: set to `0` to turn off "still down" reminders.
- `TIMEOUT_SECONDS = 20`: how long to wait for a slow site.

To add or remove a site, just edit `sites.txt`.

## Good to know
- GitHub's 5-minute schedule is best effort; at busy times a run can start a
  few minutes late.
- GitHub pauses scheduled workflows in repos with no activity for 60 days. The
  script saves `status.json` at least once a month to prevent that.
- If Hostinger's firewall ever blocks GitHub's checks (you'd see 403 errors
  while the site works in your browser), let me know and we can adjust it.
