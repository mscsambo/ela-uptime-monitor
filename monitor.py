#!/usr/bin/env python3
"""
Website uptime monitor with Telegram alerts.

Runs on GitHub Actions every 5 minutes. Checks every site in sites.txt and
sends a Telegram message when a site goes DOWN, while it STAYS down (hourly
reminder), and when it RECOVERS. Status is remembered in status.json so you
only get one alert per change, not one every 5 minutes.

No extra packages needed - uses only the Python standard library.
"""

import json
import os
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
REMIND_EVERY_MINUTES = 120     # "still down" reminder; set to 0 to turn off
KEEPALIVE_DAYS = 30           # small commit so GitHub never pauses the schedule
LOCAL_TZ = timezone(timedelta(hours=7))   # times in messages: Phnom Penh (UTC+7)
USER_AGENT = "Mozilla/5.0 (compatible; UptimeMonitor/1.0)"
# ----------------------------------------------------------


def now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.fromisoformat(value)


def fmt_time(dt):
    return dt.astimezone(LOCAL_TZ).strftime("%d %b %Y, %H:%M") + " (UTC+7)"


def fmt_duration(delta):
    minutes = int(delta.total_seconds() // 60)
    if minutes < 1:
        return "less than a minute"
    days, rest = divmod(minutes, 1440)
    hours, mins = divmod(rest, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if mins:
        parts.append(f"{mins}m")
    return " ".join(parts)


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


def check_once(url):
    """Return (is_up, detail). Redirects are followed; any final status < 400 is UP."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return False, f"Cannot connect ({e.reason})"
    except Exception as e:  # timeouts, SSL problems, etc.
        return False, f"{type(e).__name__}: {e}"


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
    except Exception as e:
        print(f"!! Telegram send failed: {type(e).__name__}: {e}")
        return False


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
        lines = [f"{'✅' if up else '🔴'} {url} — {d}" for url, (up, d) in results.items()]
        text = "👋 Uptime monitor is running.\nChecking every 5 minutes:\n\n" + "\n".join(lines)
        if notify(text):
            for url, (is_up, detail) in results.items():
                record(url, is_up, detail)
    else:
        for url, (is_up, detail) in results.items():
            prev = known.get(url)

            if prev is None:  # a site you just added to sites.txt
                status = "UP ✅" if is_up else "DOWN 🔴"
                if notify(f"➕ Now monitoring {url}\nStatus: {status} ({detail})"):
                    record(url, is_up, detail)

            elif is_up and prev["up"]:
                continue  # still fine, nothing to do

            elif not is_up and not prev["up"]:  # still down
                since_alert = t - parse_time(prev["last_alert"])
                if REMIND_EVERY_MINUTES and since_alert >= timedelta(minutes=REMIND_EVERY_MINUTES):
                    down_for = fmt_duration(t - parse_time(prev["since"]))
                    text = f"⚠️ STILL DOWN: {url}\nDown for about {down_for}\nReason: {detail}"
                    if notify(text):
                        prev["last_alert"] = t_iso
                        prev["detail"] = detail

            elif not is_up:  # just went down
                text = f"🔴 DOWN: {url}\nReason: {detail}\nDetected: {fmt_time(t)}"
                if notify(text):
                    record(url, False, detail)

            else:  # just came back up
                down_for = fmt_duration(t - parse_time(prev["since"]))
                text = f"✅ RECOVERED: {url}\nWas down for about {down_for}\nBack up: {fmt_time(t)}"
                if notify(text):
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
