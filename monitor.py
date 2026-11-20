#!/usr/bin/env python3
"""
Website uptime monitor with Telegram alerts.

Runs on GitHub Actions every 5 minutes. Checks every site in sites.txt and
sends a Telegram message when a site goes DOWN, while it STAYS down (reminder),
and when it RECOVERS. Status is remembered in status.json so you only get one
alert per change, not one every 5 minutes.

No extra packages needed - uses only the Python standard library.
"""

import html
import json
import os
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# ---------------- Settings you can change ----------------
SITES_FILE = "sites.txt"
STATE_FILE = "status.json"
TIMEOUT_SECONDS = 20          # how long to wait for a site to answer
RETRY_DELAY_SECONDS = 15      # wait this long, then re-check before calling it DOWN
REMIND_EVERY_MINUTES = 60     # "still down" reminder; set to 0 to turn off
KEEPALIVE_DAYS = 30           # small commit so GitHub never pauses the schedule
LOCAL_TZ = timezone(timedelta(hours=7))   # times in messages: Phnom Penh (UTC+7)
TZ_LABEL = "UTC+7"
TEAM_NAME = "Website Monitor"  # shown at the top of every message
USER_AGENT = "Mozilla/5.0 (compatible; UptimeMonitor/1.0)"
# ----------------------------------------------------------

HTTP_MEANINGS = {
    400: "Bad request",
    401: "Login required",
    403: "Access blocked (forbidden)",
    404: "Page not found",
    408: "Request timed out",
    429: "Too many requests (rate limited)",
    500: "Server error",
    502: "Bad gateway",
    503: "Service unavailable / server overloaded",
    504: "Gateway timeout",
    508: "Hosting resource limit reached",
    520: "Unknown error from the server",
    521: "Web server is down",
    522: "Connection timed out",
    523: "Origin unreachable",
    524: "Server took too long to respond",
    525: "SSL handshake failed",
    526: "Invalid SSL certificate",
}


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value)


def fmt_time(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%d %b %Y, %H:%M") + f" ({TZ_LABEL})"


def fmt_duration(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "less than 1 minute"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days} day{'s' if days != 1 else ''}")
    if hours:
        parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
    if mins:
        parts.append(f"{mins} min")
    return " ".join(parts)


def esc(text):
    return html.escape(str(text), quote=False)


def domain_of(url):
    return urllib.parse.urlparse(url).netloc or url


def load_sites():
    sites = []
    with open(SITES_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if not line:
                continue
            if not line.startswith(("http://", "https://")):
                line = "https://" + line
            if line not in sites:
                sites.append(line)
    return sites


def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
            if isinstance(state, dict):
                return state
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {"sites": {}}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")


def explain_error(exc):
    """Turn a technical error into a short, plain-language reason."""
    if isinstance(exc, urllib.error.HTTPError):
        meaning = HTTP_MEANINGS.get(exc.code, "Error response")
        return f"{meaning} (HTTP {exc.code})"
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
    if isinstance(reason, ssl.SSLCertVerificationError):
        msg = getattr(reason, "verify_message", "") or ""
        if "expired" in msg.lower():
            return "SSL certificate has expired"
        return "SSL certificate is invalid"
    if isinstance(reason, ssl.SSLError):
        return "SSL/HTTPS connection problem"
    if isinstance(reason, socket.gaierror):
        return "Domain not found (DNS problem or domain expired)"
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return f"No response within {TIMEOUT_SECONDS} seconds"
    if isinstance(reason, ConnectionRefusedError):
        return "Connection refused (web server not running)"
    if isinstance(reason, ConnectionResetError):
        return "Connection was dropped by the server"
    text = str(reason)
    if "timed out" in text.lower():
        return f"No response within {TIMEOUT_SECONDS} seconds"
    return f"Cannot connect ({text})"


def check_once(url):
    """Return (is_up, detail). Redirects are followed; any final status < 400 is UP."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return True, f"OK (HTTP {resp.status})"
    except Exception as e:
        return False, explain_error(e)


def check_site(url):
    is_up, detail = check_once(url)
    if is_up:
        return is_up, detail
    time.sleep(RETRY_DELAY_SECONDS)   # avoid false alarms from a short blip
    return check_once(url)


def send_telegram(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("!! TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing. Message not sent:\n" + text)
        return False
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    try:
        with urllib.request.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20
        ) as resp:
            ok = json.load(resp).get("ok", False)
            if not ok:
                print("!! Telegram did not accept the message.")
            return ok
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            body = {}
        print(f"!! Telegram error {e.code}: {body.get('description', e.reason)}")
        new_id = (body.get("parameters") or {}).get("migrate_to_chat_id")
        if new_id:
            print(f"!! Your group was upgraded. Change the TELEGRAM_CHAT_ID secret to: {new_id}")
        return False
    except Exception as e:
        print(f"!! Telegram send failed: {type(e).__name__}: {e}")
        return False


# ---------------- Message templates ----------------

def header(title):
    return f"<b>{esc(title)}</b>\n<i>{esc(TEAM_NAME)}</i>\n"


def msg_down(url, detail, t):
    return (
        header("🔴 WEBSITE DOWN")
        + f"\n🌐 <b>Site:</b> {esc(domain_of(url))}"
        + f"\n❗ <b>Problem:</b> {esc(detail)}"
        + f"\n🕒 <b>Detected:</b> {esc(fmt_time(t))}"
        + f"\n🔗 {esc(url)}"
        + "\n\n👉 Please check the site and the Hostinger panel."
    )


def msg_still_down(url, detail, since, t):
    return (
        header("⚠️ STILL DOWN")
        + f"\n🌐 <b>Site:</b> {esc(domain_of(url))}"
        + f"\n⏱ <b>Down for:</b> {esc(fmt_duration(t - since))}"
        + f"\n❗ <b>Problem:</b> {esc(detail)}"
        + f"\n🕒 <b>Since:</b> {esc(fmt_time(since))}"
    )


def msg_recovered(url, since, t):
    return (
        header("✅ WEBSITE BACK ONLINE")
        + f"\n🌐 <b>Site:</b> {esc(domain_of(url))}"
        + f"\n⏱ <b>Total downtime:</b> {esc(fmt_duration(t - since))}"
        + f"\n🕒 <b>Back up at:</b> {esc(fmt_time(t))}"
    )


def msg_new_site(url, is_up, detail):
    status = "✅ Online" if is_up else f"🔴 Down — {detail}"
    return (
        header("➕ NEW SITE ADDED")
        + f"\n🌐 <b>Site:</b> {esc(domain_of(url))}"
        + f"\n📊 <b>Status:</b> {esc(status)}"
    )


def msg_start(results, t):
    up = sum(1 for ok, _ in results.values() if ok)
    lines = []
    for url, (ok, detail) in results.items():
        if ok:
            lines.append(f"✅ {esc(domain_of(url))}")
        else:
            lines.append(f"🔴 {esc(domain_of(url))} — {esc(detail)}")
    return (
        header("👋 MONITORING STARTED")
        + f"\nChecking {len(results)} sites every 5 minutes."
        + f"\n📊 <b>{up}/{len(results)} online</b> at {esc(fmt_time(t))}\n\n"
        + "\n".join(lines)
    )


# ---------------------------------------------------

def main():
    sites = load_sites()
    if not sites:
        print("No sites found in sites.txt")
        return 1

    state = load_state()
    known = state.setdefault("sites", {})
    before = json.dumps(state, sort_keys=True)
    first_run = not known

    with ThreadPoolExecutor(max_workers=min(10, len(sites))) as pool:
        results = dict(zip(sites, pool.map(check_site, sites)))

    t = now()
    t_iso = t.isoformat()
    failed_sends = 0

    for url, (is_up, detail) in results.items():
        print(f"{'UP  ' if is_up else 'DOWN'}  {url}  ({detail})")

    def notify(text):
        nonlocal failed_sends
        ok = send_telegram(text)
        if not ok:
            failed_sends += 1
        return ok

    def record(url, is_up, detail):
        known[url] = {"up": is_up, "since": t_iso, "detail": detail, "last_alert": t_iso}

    if first_run:
        if notify(msg_start(results, t)):
            for url, (is_up, detail) in results.items():
                record(url, is_up, detail)
    else:
        for url, (is_up, detail) in results.items():
            prev = known.get(url)

            if prev is None:  # a site you just added to sites.txt
                if notify(msg_new_site(url, is_up, detail)):
                    record(url, is_up, detail)

            elif is_up and prev["up"]:
                continue  # still fine, nothing to do

            elif not is_up and not prev["up"]:  # still down
                since_alert = t - parse_time(prev["last_alert"])
                if REMIND_EVERY_MINUTES and since_alert >= timedelta(minutes=REMIND_EVERY_MINUTES):
                    if notify(msg_still_down(url, detail, parse_time(prev["since"]), t)):
                        prev["last_alert"] = t_iso
                        prev["detail"] = detail

            elif not is_up:  # just went down
                if notify(msg_down(url, detail, t)):
                    record(url, False, detail)

            else:  # just came back up
                if notify(msg_recovered(url, parse_time(prev["since"]), t)):
                    record(url, True, detail)

    # Forget sites you removed from sites.txt
    for url in list(known):
        if url not in results:
            del known[url]

    # Save only when something changed, or once a month so GitHub keeps the schedule active
    last_saved = state.get("last_saved")
    stale = not last_saved or t - parse_time(last_saved) >= timedelta(days=KEEPALIVE_DAYS)
    if json.dumps(state, sort_keys=True) != before or stale:
        state["last_saved"] = t_iso
        save_state(state)

    if failed_sends:
        print(f"{failed_sends} Telegram message(s) failed; will retry on the next run.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
