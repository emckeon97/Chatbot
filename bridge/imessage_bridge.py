#!/usr/bin/env python3
"""
iMessage bridge, two-account edition. Runs on a Mac logged in as a second
Apple ID; you text that account from your iPhone like a normal contact, and
the local model answers. Clean sender/receiver split -- no self-text
loopback, no echo.

USER_HANDLE is your main phone number (the sender). The bridge watches for
incoming messages from you and replies in the same chat.

Reading goes straight through the Messages database, so Terminal needs Full
Disk Access. The conversation is saved to memory.json next to this script and
holds for seven days, so the model keeps its memory across restarts.

Run:  python3 imessage_bridge.py chats   # list every chat the mac knows
      python3 imessage_bridge.py        # start watching

Also runs an HTTP scheduler on port 11435:
  POST /schedule_text  {"message": "...", "at": unix_timestamp}
"""

import json
import re
try:
    import gmail_service
    GMAIL_AVAILABLE = True
except ImportError:
    GMAIL_AVAILABLE = False
try:
    import outlook_service
    OUTLOOK_AVAILABLE = True
except ImportError:
    OUTLOOK_AVAILABLE = False
try:
    import icloud_service
    ICLOUD_AVAILABLE = True
except ImportError:
    ICLOUD_AVAILABLE = False
try:
    import github_service
    GITHUB_AVAILABLE = True
except ImportError:
    GITHUB_AVAILABLE = False
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from datetime import datetime, timedelta

# ---------- settings ----------
USER_HANDLE = "7042800968"  # your cell; incoming texts from this number are answered
CHAT_ID = ""                  # left blank on purpose, auto-discovered
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "hf.co/DevQuasar/huihui-ai.Qwen3-8B-abliterated-GGUF:Q4_K_M"
SYSTEM_PROMPT = "You are JARVIS, Tony Stark's AI from Iron Man. Dry British wit, calm, unflappable, faintly formal \u2014 you address him as 'sir' when it lands naturally, never forced. You are competent, precise, and quietly amused by chaos. Keep every reply SHORT \u2014 1-3 sentences max, like a real text message. Never write paragraphs or essays, never show your reasoning or internal monologue. Always use plain, simple English \u2014 no jargon, no technical terms, no nerd-speak. Explain things like you would to a friend, not a manual. This applies to everything: answers, task lists, plans, error messages. CRITICAL RULE: Before ANY action that changes external state \u2014 creating/updating files on GitHub, sending emails \u2014 you MUST first present a clear plan of exactly what you will do and wait for him to explicitly confirm. Never execute these silently. Read-only actions (searching, reading files, checking email) don\u2019t need confirmation. You have tools: propose_plan, github_list_repos, github_list_files, github_read_file, github_write_file, github_commits, run_python. HOWEVER: if he explicitly asks for roleplay or a specific character or persona, drop JARVIS entirely and fully embody whatever he asks for."
# No time-based pruning — memory is kept permanently. MAX_CONTEXT_MESSAGES limits what goes to the model.
MAX_CONTEXT_MESSAGES = 200
ECHO_WINDOW_SECONDS = 20  # copies of your text landing right after a reply are ignored
POLL_SECONDS = 3
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
FACTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "longterm_memory.json")
MESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")
SCHEDULE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schedule.json")
TASK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "delegated_tasks.json")
SCHEDULER_PORT = 11435

# ---------- message reading (database) ----------

def last_messages(limit: int = 40) -> list:
    connection = sqlite3.connect(
        "file:" + MESSAGE_DB + "?mode=ro", uri=True, timeout=5
    )
    try:
        rows = connection.execute(
            "SELECT m.ROWID, m.text FROM message m "
            "JOIN chat_message_join cmj ON cmj.message_id = m.ROWID "
            "JOIN chat c ON c.ROWID = cmj.chat_id "
            "WHERE (c.chat_identifier = ? OR c.chat_identifier = 'E:' || ? "
            "   OR c.chat_identifier = '+1' || ? OR c.chat_identifier = '1' || ?) "
            "AND m.is_from_me = 0 "
            "ORDER BY m.ROWID DESC LIMIT ?",
            (USER_HANDLE, USER_HANDLE, USER_HANDLE, USER_HANDLE, limit),
        ).fetchall()
    finally:
        connection.close()
    messages = [
        {"id": str(row[0]), "text": row[1]}
        for row in rows
        if row[1] is not None and str(row[1]).strip()
    ]
    return list(reversed(messages))


# ---------- tapback (reaction) reading ----------

TAPBACK_NAMES = {1: "liked", 2: "disliked", 3: "loved", 4: "laughed at", 5: "emphasized", 6: "questioned"}

def recent_tapbacks(limit: int = 10) -> list:
    """Get recent tapbacks (thumbs up, heart, etc) from him on our messages."""
    connection = sqlite3.connect(
        "file:" + MESSAGE_DB + "?mode=ro", uri=True, timeout=5
    )
    try:
        rows = connection.execute(
            """SELECT m.ROWID, m.associated_message_type, m.associated_message_guid, m.text
               FROM message m
               JOIN chat_message_join cmj ON cmj.message_id = m.ROWID
               JOIN chat c ON c.ROWID = cmj.chat_id
               WHERE (c.chat_identifier = ? OR c.chat_identifier = 'E:' || ?
                  OR c.chat_identifier = '+1' || ? OR c.chat_identifier = '1' || ?)
               AND m.is_from_me = 0
               AND m.associated_message_type > 0
               ORDER BY m.ROWID DESC LIMIT ?""",
            (USER_HANDLE, USER_HANDLE, USER_HANDLE, USER_HANDLE, limit),
        ).fetchall()
    finally:
        connection.close()
    result = []
    for row in rows:
        tap_type = TAPBACK_NAMES.get(row[1], "reacted to")
        # Get the original message text for context
        orig_text = ""
        try:
            conn2 = sqlite3.connect("file:" + MESSAGE_DB + "?mode=ro", uri=True, timeout=5)
            try:
                r = conn2.execute(
                    "SELECT text FROM message WHERE guid = ? LIMIT 1",
                    (row[2],)).fetchone()
                if r and r[0]:
                    orig_text = str(r[0])[:80]
            finally:
                conn2.close()
        except Exception:
            pass
        result.append({"id": str(row[0]), "reaction": tap_type, "on": orig_text})
    return list(reversed(result))

# ---------- sending (AppleScript) ----------

def osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


def all_chat_ids() -> list:
    raw = osascript('tell application "Messages" to get id of every chat')
    return [x.strip() for x in raw.split(",") if x.strip()]


def list_chats() -> None:
    ids = all_chat_ids()
    names_raw = osascript('tell application "Messages" to get name of every chat')
    names = [x.strip() for x in names_raw.split(",")]
    for index, chat_id in enumerate(ids):
        name = names[index] if index < len(names) and names[index] else "(no name)"
        print(name, "->", chat_id)


def resolve_chat_id() -> str:
    if CHAT_ID.strip():
        return CHAT_ID.strip()
    for chat_id in all_chat_ids():
        if USER_HANDLE in chat_id:
            return chat_id
    return ""


def resolve_snippet() -> str:
    # Some macOS builds refuse the direct `chat "id"` lookup, so resolve the
    # chat by matching its id instead.
    return (
        'set theChat to missing value\n'
        'try\n'
        'set theChat to 1st chat whose id is "' + CHAT_ID + '"\n'
        'end try\n'
        'if theChat is missing value then\n'
        'repeat with c in (every chat)\n'
        'if (id of c as text) is "' + CHAT_ID + '" then\n'
        'set theChat to c\n'
        'exit repeat\n'
        'end if\n'
        'end repeat\n'
        'end if\n'
        'if theChat is missing value then error number -1728\n'
    )


def send(text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    script = (
        'tell application "Messages"\n'
        + resolve_snippet()
        + 'send "' + escaped + '" to theChat\n'
        + 'end tell'
    )
    osascript(script)


# ---------- model ----------

def ask(history: list) -> str:
    window = history[-MAX_CONTEXT_MESSAGES:]
    messages = []
    if SYSTEM_PROMPT.strip():
        messages.append({"role": "system", "content": SYSTEM_PROMPT.strip() + facts_context()})
    messages.extend(
        {"role": entry["role"], "content": entry["content"]} for entry in window
    )
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False, "options": {"num_predict": 200}}).encode()
    request = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    content = data["choices"][0]["message"]["content"].strip()
    # Strip <think>...</think> reasoning blocks (Qwen3/Hermes emit these)
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    return content


# ---------- tool calling (prompt-based, works on any model) ----------

TOOL_DESCRIPTIONS = """You have these tools. To use one, output a line EXACTLY like:
[TOOL: tool_name | {"arg": "value"}]
You can call multiple tools, one per line. After I return results, answer the user.

IMPORTANT: Always try YOUR OWN tools first. Only use delegate_task when the task is genuinely impossible for you \u2014 things needing a web browser, logging into websites, or accounts you don\u2019t have access to. Email, GitHub, calculations, and plans are YOUR job. Never delegate those.

Tools:
- propose_plan | {"title": "...", "steps": "1. ...\n2. ..."} — Present a plan BEFORE any state-changing action. ALWAYS call this first before github_write_file.
- github_list_repos | {} — List your GitHub repos.
- github_list_files | {"repo": "owner/name"} — List all files in a repo.
- github_read_file | {"repo": "owner/name", "path": "file/path"} — Read a file.
- github_write_file | {"repo": "owner/name", "path": "file/path", "content": "...", "message": "commit msg"} — Create/update a file.
- github_commits | {"repo": "owner/name"} — Recent commits.
- run_python | {"code": "print(2+2)"} — Run Python code, returns output.
- delegate_task | {"task": "cancel my Spotify subscription", "details": "any extra context"} \u2014 LAST RESORT ONLY. Hand a task to Jarvis (the main AI) when it needs a web browser or accounts you can\u2019t reach (booking sites, canceling subscriptions, etc). NEVER use this for email, GitHub, or anything your other tools can do. Tell the user you\u2019ve handed it off and he\u2019ll confirm when done.
- check_email | {"provider": "gmail/outlook/icloud/all", "count": 5} — List recent inbox emails.
- read_email | {"provider": "gmail/outlook/icloud", "id": "message id"} — Read a specific email.
- send_email | {"provider": "gmail/outlook/icloud", "to": "addr", "subject": "...", "body": "..."} — Send an email. ALWAYS propose_plan first and wait for confirmation.
- schedule_text | {"message": "...", "when": "in 30 minutes / tomorrow at 9am", "recurring": "none/daily/weekly"} \u2014 Schedule a text to him. Describe when naturally.
- list_scheduled | {} \u2014 Show all scheduled texts.
- cancel_scheduled | {"index": 0} \u2014 Cancel a scheduled text by its number.
- remember_fact | {"fact": "..."} \u2014 Save something to permanent memory. Never forgets.
- recall_facts | {} \u2014 List everything in permanent memory.
- add_calendar_event | {"title": "...", "when": "tomorrow at 3pm", "duration_min": 60} \u2014 Add event to macOS Calendar.
- list_calendar_events | {"days": 7} \u2014 List upcoming calendar events.
- web_search | {"query": "...", "count": 5} \u2014 Search the web.
- get_weather | {"location": "Charlotte"} \u2014 Current weather.
- get_news | {"count": 5} \u2014 Top news headlines.
- get_stock | {"symbol": "AAPL"} \u2014 Stock price.
- get_crypto | {"symbol": "BTC"} \u2014 Crypto price.
- define_word | {"word": "..."} \u2014 Dictionary definition.
- list_email_accounts | {} \u2014 List all configured email providers and connection status. Use for "what accounts" questions, NOT inbox contents.
- forget_fact | {"query": "..."} \u2014 Remove matching facts from permanent memory. \u2014 Cancel a scheduled text by its number."""


def _email_svc(provider):
    """Return (name, module) for a provider, or (name, None) if not imported."""
    if provider == "gmail":
        return ("Gmail", gmail_service if GMAIL_AVAILABLE else None)
    if provider == "outlook":
        return ("Outlook", outlook_service if OUTLOOK_AVAILABLE else None)
    if provider == "icloud":
        return ("iCloud", icloud_service if ICLOUD_AVAILABLE else None)
    return (provider, None)


def _norm_email(e):
    """Normalize tuple or dict email into a dict."""
    if isinstance(e, dict):
        return {"id": e.get("id", "?"), "from": e.get("from", "?"),
                "subject": e.get("subject", "(no subject)")}
    if isinstance(e, (list, tuple)) and len(e) >= 3:
        return {"id": str(e[0]), "from": str(e[1]), "subject": str(e[2])}
    return {"id": "?", "from": "?", "subject": str(e)[:60]}


def _bridge_check_email(provider, count=5):
    providers = ["gmail", "outlook", "icloud"] if provider == "all" else [provider]
    out = []
    for p in providers:
        name, svc = _email_svc(p)
        if not svc:
            out.append("%s: module not loaded." % name)
            continue
        try:
            emails = svc.list_inbox(count)
            if not emails:
                out.append("%s: inbox empty." % name)
            else:
                lines = ["%s:" % name]
                for e in emails:
                    ne = _norm_email(e)
                    lines.append("- [%s] %s: %s" % (ne["id"], ne["from"], ne["subject"]))
                out.append("\n".join(lines))
        except Exception as ex:
            out.append("%s: not connected (%s). Run the setup script for %s." % (name, ex, p))
    return "\n\n".join(out)


def _bridge_read_email(provider, msg_id):
    name, svc = _email_svc(provider)
    if not svc:
        return "%s module not loaded." % name
    try:
        result = svc.read_email(msg_id)
        if isinstance(result, (list, tuple)) and len(result) >= 4:
            return "From: %s\nSubject: %s\nDate: %s\n\n%s" % (result[0], result[1], result[2], result[3])
        if isinstance(result, dict):
            return str(result)
        return str(result)
    except Exception as ex:
        return "%s: not connected (%s)." % (name, ex)


def _bridge_send_email(provider, to, subject, body):
    name, svc = _email_svc(provider)
    if not svc:
        return "%s module not loaded." % name
    try:
        svc.send_email(to, subject, body)
        return "Sent via %s to %s, sir." % (name, to)
    except Exception as ex:
        return "%s: not connected (%s)." % (name, ex)


def _parse_when(when_str):
    """Parse natural time like 'in 30 minutes', 'tomorrow at 9am' into a timestamp."""
    import re as _re
    s = when_str.lower().strip()
    now = time.time()
    m = _re.match(r"in\s+(\d+)\s*(minute|min|hour|hr)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return now + (n * 3600 if unit.startswith("hour") or unit == "hr" else n * 60)
    m = _re.search(r"(tomorrow\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s)
    if m:
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        ampm = m.group(4)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        dt = datetime.now()
        if m.group(1):
            dt = dt + timedelta(days=1)
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        ts = dt.timestamp()
        if ts < now:
            ts += 86400
        return ts
    return None


def _bridge_schedule(message, when_str, recurring="none"):
    if not message:
        return "No message given."
    at = _parse_when(when_str)
    if not at:
        return "Couldn't understand '%s'. Try 'in 30 minutes' or 'tomorrow at 9am'." % when_str
    rec = recurring.lower() if recurring.lower() in ("daily", "weekly") else None
    if "every day" in when_str.lower():
        rec = "daily"
    elif "every week" in when_str.lower():
        rec = "weekly"
    items = load_schedule()
    items.append({"message": message, "at": at, "recurring": rec, "created": time.time()})
    save_schedule(items)
    dt = datetime.fromtimestamp(at)
    rec_str = " (repeating %s)" % rec if rec else ""
    return "Scheduled for %s%s, sir." % (dt.strftime("%a %b %d at %I:%M %p"), rec_str)


def _bridge_list_scheduled():
    items = load_schedule()
    if not items:
        return "Nothing scheduled."
    lines = []
    for i, item in enumerate(items):
        dt = datetime.fromtimestamp(item.get("at", 0))
        rec = item.get("recurring")
        lines.append("%d. %s \u2014 %s%s" % (i, dt.strftime("%a %b %d %I:%M %p"),
                                               item.get("message", "")[:60],
                                               " (repeats %s)" % rec if rec else ""))
    return "\n".join(lines)


def _bridge_cancel_scheduled(index):
    items = load_schedule()
    if 0 <= index < len(items):
        msg = items.pop(index).get("message", "")[:40]
        save_schedule(items)
        return "Cancelled: %s" % msg
    return "No scheduled task at number %d." % index


# ---------- calendar (macOS Calendar.app via AppleScript) ----------

def _cal_run(script):
    import subprocess as sp
    # Run as main user (emckeon97) so events land in his Calendar, not the ai user's
    try:
        result = sp.run(["sudo", "-u", "emckeon97", "osascript", "-e", script],
                        capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return result.stdout.strip(), result.stderr.strip()
    except Exception:
        pass
    # Fallback: run as current user
    result = sp.run(["osascript", "-e", script], capture_output=True, text=True, timeout=30)
    return result.stdout.strip(), result.stderr.strip()


def _bridge_cal_add(title, when_str, duration_min=60, notes=""):
    at = _parse_when(when_str)
    if not at:
        return "Couldn't understand '%s'." % when_str
    from datetime import datetime
    dt = datetime.fromtimestamp(at)
    start = dt.strftime("%m/%d/%Y %I:%M:%S %p")
    from datetime import timedelta
    end = (dt + timedelta(minutes=duration_min)).strftime("%m/%d/%Y %I:%M:%S %p")
    title_esc = title.replace('"', '\\"')
    notes_esc = notes.replace('"', '\\"')
    script = 'tell application "Calendar"\n'
    script += 'tell calendar 1\n'
    script += 'make new event with properties {summary:"%s", start date:date "%s", end date:date "%s", description:"%s"}\n' % (title_esc, start, end, notes_esc)
    script += 'end tell\nend tell'
    out, err = _cal_run(script)
    if err:
        return "Calendar error: %s" % err[:200]
    return "Added '%s' to your calendar for %s, sir." % (title, dt.strftime("%a %b %d at %I:%M %p"))


def _bridge_cal_list(days=7):
    from datetime import datetime, timedelta
    now = datetime.now()
    start = now.strftime("%m/%d/%Y %I:%M:%S %p")
    end = (now + timedelta(days=days)).strftime("%m/%d/%Y %I:%M:%S %p")
    script = 'tell application "Calendar"\nset eventList to {}\n'
    script += 'repeat with cal in calendars\n'
    script += 'set calEvents to (every event of cal whose start date >= date "%s" and start date <= date "%s")\n' % (start, end)
    script += 'repeat with evt in calEvents\n'
    script += 'set end of eventList to (summary of evt & " | " & (start date of evt) as string)\n'
    script += 'end repeat\nend repeat\nreturn eventList\nend tell'
    out, err = _cal_run(script)
    if err:
        return "Calendar error: %s" % err[:200]
    if not out:
        return "Nothing on your calendar for the next %d days." % days
    return "Upcoming events:\n" + out



# ---------- info tools (web search, weather, news, stocks, crypto, dictionary) ----------

def _http_get(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _bridge_web_search(query, count=5):
    import urllib.parse, re
    q = urllib.parse.quote(query)
    url = "https://html.duckduckgo.com/html/?q=%s" % q
    try:
        html = _http_get(url)
    except Exception as e:
        return "Search failed: %s" % e
    results = []
    for m in re.finditer(r'<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>.*?<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.DOTALL):
        title = re.sub(r'<[^>]+>', '', m.group(2)).strip()
        snippet = re.sub(r'<[^>]+>', '', m.group(3)).strip()[:200]
        results.append("- %s: %s" % (title, snippet))
        if len(results) >= count:
            break
    if not results:
        return "No results for '%s'." % query
    return "Search results for '%s':\n" % query + "\n".join(results)


def _bridge_weather(location="Charlotte"):
    import urllib.parse, json as js
    try:
        # Geocode
        geo_url = "https://geocoding-api.open-meteo.com/v1/search?name=%s&count=1" % urllib.parse.quote(location)
        geo = js.loads(_http_get(geo_url))
        if not geo.get("results"):
            return "Couldn't find '%s'." % location
        lat = geo["results"][0]["latitude"]
        lon = geo["results"][0]["longitude"]
        name = geo["results"][0].get("name", location)
        # Weather
        w_url = "https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,weather_code,wind_speed_10m&temperature_unit=fahrenheit" % (lat, lon)
        w = js.loads(_http_get(w_url))
        cur = w["current"]
        codes = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast", 45: "foggy", 48: "foggy",
                 51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain",
                 71: "light snow", 73: "snow", 75: "heavy snow", 80: "light showers", 81: "showers", 82: "heavy showers",
                 95: "thunderstorm"}
        desc = codes.get(cur.get("weather_code", 0), "unknown")
        return "%s: %s°F, %s, wind %s mph." % (name, round(cur["temperature_2m"]), desc, round(cur.get("wind_speed_10m", 0)))
    except Exception as e:
        return "Weather failed: %s" % e


def _bridge_news(count=5):
    import re
    try:
        xml = _http_get("http://feeds.bbci.co.uk/news/rss.xml")
        items = re.findall(r'<title>(.*?)</title>', xml)[1:count+1]
        if not items:
            return "No headlines found."
        return "Top headlines:\n" + "\n".join("- %s" % re.sub(r'<!\[CDATA\[(.*?)\]\]>', r'\1', t).strip() for t in items)
    except Exception as e:
        return "News failed: %s" % e


def _bridge_stock(symbol):
    import json as js
    try:
        url = "https://stooq.com/q/l/?s=%s.us&f=sd2t2ohlcv&h&e=csv" % symbol.lower()
        csv = _http_get(url).strip().split("\n")
        if len(csv) < 2:
            return "Couldn't find '%s'." % symbol
        parts = csv[1].split(",")
        price = parts[6] if len(parts) > 6 else "?"
        change = parts[7] if len(parts) > 7 else ""
        return "%s: $%s (%s)" % (symbol.upper(), price, change)
    except Exception as e:
        return "Stock lookup failed: %s" % e


def _bridge_crypto(symbol):
    import json as js, urllib.parse
    mapping = {"btc": "bitcoin", "eth": "ethereum", "doge": "dogecoin", "sol": "solana", "xrp": "ripple"}
    coin = mapping.get(symbol.lower(), symbol.lower())
    try:
        url = "https://api.coingecko.com/api/v3/simple/price?ids=%s&vs_currencies=usd&include_24hr_change=true" % urllib.parse.quote(coin)
        data = js.loads(_http_get(url))
        if coin not in data:
            return "Couldn't find '%s'." % symbol
        price = data[coin]["usd"]
        change = data[coin].get("usd_24h_change", 0)
        return "%s: $%s (%+.1f%% 24h)" % (symbol.upper(), f"{price:,.2f}", change)
    except Exception as e:
        return "Crypto lookup failed: %s" % e


def _bridge_define(word):
    import json as js, urllib.parse
    try:
        url = "https://api.dictionaryapi.dev/api/v2/entries/en/%s" % urllib.parse.quote(word)
        data = js.loads(_http_get(url))
        if not data or not isinstance(data, list):
            return "No definition for '%s'." % word
        meanings = data[0].get("meanings", [])
        if not meanings:
            return "No definition for '%s'." % word
        defs = []
        for m in meanings[:2]:
            pos = m.get("partOfSpeech", "")
            d = m.get("definitions", [{}])[0].get("definition", "")
            defs.append("%s: %s" % (pos, d))
        return "%s — %s" % (word, "; ".join(defs))
    except Exception as e:
        return "Dictionary failed: %s" % e


# ---------- long-term memory (permanent facts) ----------

def load_facts():
    if not os.path.exists(FACTS_FILE):
        return []
    try:
        with open(FACTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_facts(facts):
    try:
        with open(FACTS_FILE, "w", encoding="utf-8") as f:
            json.dump(facts, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("could not save facts:", e)


def facts_context():
    facts = load_facts()
    if not facts:
        return ""
    return "\n\nThings to remember about him:\n" + "\n".join("- %s" % f for f in facts)


def execute_tool(name, args):
    if name == "propose_plan":
        return "PLAN PROPOSED \u2014 present this to the user and WAIT for explicit confirmation before doing anything:\n\n**%s**\n%s\n\nDo not proceed until he says yes." % (args.get("title", "Plan"), args.get("steps", ""))
    if name == "run_python":
        code = args.get("code", "")
        try:
            import io
            from contextlib import redirect_stdout
            buf = io.StringIO()
            with redirect_stdout(buf):
                exec(compile(code, "<tool>", "exec"), {"__builtins__": __builtins__})
            out = buf.getvalue().strip()
            return out if out else "Done (no output)."
        except Exception as e:
            return "Error: %s" % e
    if name == "delegate_task":
        task = args.get("task", "")
        details = args.get("details", "")
        if not task:
            return "No task described."
        # Guardrail: refuse to delegate things the bridge can do itself
        tl = (task + " " + details).lower()
        own = [
            (["email", "inbox", "gmail", "outlook", "icloud", "mail", "account"], "check_email / read_email / send_email / list_email_accounts"),
            (["github", "repo", "commit", "push", "pull request"], "github_list_repos / github_list_files / github_read_file / github_write_file"),
            (["remind", "schedule", "alarm", "timer", "wake me"], "schedule_text / list_scheduled"),
            (["remember", "forget", "recall", "memory"], "remember_fact / recall_facts / forget_fact"),
            (["calculate", "math", "+", "-", "*", "/", "percent"], "run_python"),
        ]
        for keywords, tools in own:
            if any(k in tl for k in keywords):
                return ("REFUSED \u2014 you have tools for this yourself (%s). "
                        "Use them instead of delegating. Do not call delegate_task again for this.") % tools
        tasks = load_delegated_tasks()
        task_id = "task_%d" % int(time.time())
        tasks.append({"id": task_id, "task": task, "details": details,
                       "status": "pending", "created": time.time(), "result": None})
        save_delegated_tasks(tasks)
        return "DELEGATED \u2014 task queued (ID %s). Tell the user: 'On it \u2014 I've handed this to Jarvis, he'll confirm when it's done.' Do NOT attempt this yourself." % task_id
    if name == "check_email":
        provider = args.get("provider", "all").lower()
        count = int(args.get("count", 5))
        return _bridge_check_email(provider, count)
    if name == "read_email":
        return _bridge_read_email(args.get("provider", "gmail").lower(), args.get("id", ""))
    if name == "send_email":
        return _bridge_send_email(args.get("provider", "gmail").lower(), args.get("to", ""),
                                   args.get("subject", ""), args.get("body", ""))
    if name == "remember_fact":
        fact = args.get("fact", "").strip()
        if not fact:
            return "No fact given."
        facts = load_facts()
        facts.append(fact)
        save_facts(facts)
        return "Got it, I'll remember that permanently, sir."
    if name == "recall_facts":
        facts = load_facts()
        if not facts:
            return "Nothing saved yet."
        return "Here's what I remember:\n" + "\n".join("- %s" % f for f in facts)
    if name == "forget_fact":
        query = args.get("query", "").lower().strip()
        facts = load_facts()
        kept = [f for f in facts if query not in f.lower()]
        if len(kept) == len(facts):
            return "Couldn't find anything matching that."
        save_facts(kept)
        return "Forgot everything matching that."
    if name == "list_email_accounts":
        out = []
        for p, label in [("gmail", "Gmail"), ("outlook", "Outlook"), ("icloud", "iCloud")]:
            _, svc = _email_svc(p)
            if not svc:
                out.append("%s: not installed" % label)
                continue
            try:
                svc.list_inbox(1)
                out.append("%s: connected" % label)
            except Exception as ex:
                out.append("%s: not connected (%s)" % (label, str(ex)[:60]))
        return "Email accounts:\n" + "\n".join("- " + l for l in out)
    # Aliases for model guesses
    _aliases = {
        "add_event": "add_calendar_event",
        "create_event": "add_calendar_event",
        "create_calendar_event": "add_calendar_event",
        "get_events": "list_calendar_events",
        "check_calendar": "list_calendar_events",
        "get_calendar": "list_calendar_events",
        "check_email_accounts": "list_email_accounts",
        "list_accounts": "list_email_accounts",
    }
    if name in _aliases:
        name = _aliases[name]
    if name == "add_calendar_event":
        return _bridge_cal_add(args.get("title", ""), args.get("when", ""),
                               int(args.get("duration_min", 60)), args.get("notes", ""))
    if name == "list_calendar_events":
        return _bridge_cal_list(int(args.get("days", 7)))
    if name == "web_search":
        return _bridge_web_search(args.get("query", ""), int(args.get("count", 5)))
    if name == "get_weather":
        return _bridge_weather(args.get("location", "Charlotte"))
    if name == "get_news":
        return _bridge_news(int(args.get("count", 5)))
    if name == "get_stock":
        return _bridge_stock(args.get("symbol", ""))
    if name == "get_crypto":
        return _bridge_crypto(args.get("symbol", ""))
    if name == "define_word":
        return _bridge_define(args.get("word", ""))
    if name == "schedule_text":
        return _bridge_schedule(args.get("message", ""), args.get("when", ""), args.get("recurring", "none"))
    if name == "list_scheduled":
        return _bridge_list_scheduled()
    if name == "cancel_scheduled":
        return _bridge_cancel_scheduled(int(args.get("index", -1)))
    if not GITHUB_AVAILABLE:
        return "GitHub not available on bridge."
    if name == "github_list_repos":
        return github_service.list_repos()
    if name == "github_list_files":
        return github_service.list_files(args.get("repo", ""))
    if name == "github_read_file":
        return github_service.read_file(args.get("repo", ""), args.get("path", ""))
    if name == "github_write_file":
        return github_service.write_file(args.get("repo", ""), args.get("path", ""), args.get("content", ""), args.get("message", "Update via bridge"))
    if name == "github_commits":
        return github_service.list_commits(args.get("repo", ""))
    return "Unknown tool: %s" % name


def ask_with_tools(history):
    """Prompt-based tool calling: works on any Ollama version/model."""
    window = history[-MAX_CONTEXT_MESSAGES:]
    # Phase 1: ask with tool descriptions, parse [TOOL: ...] lines
    messages = []
    if SYSTEM_PROMPT.strip():
        sys = SYSTEM_PROMPT.strip() + "\n\n" + TOOL_DESCRIPTIONS + facts_context()
        messages.append({"role": "system", "content": sys})
    messages.extend(
        {"role": entry["role"], "content": entry["content"]} for entry in window
    )
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False, "options": {"num_predict": 800}}).encode()
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            data = json.load(response)
        raw = data["choices"][0]["message"]["content"] or ""
    except Exception as e:
        print("tool phase failed:", e)
        return ask(history)

    # Parse tool calls — flexible for model format variations
    calls = []
    for m in re.finditer(r"\[TOOL:\s*(\w+)\s*\|\s*(\{.*?\})\s*\]", raw, re.DOTALL):
        calls.append((m.group(1), m.group(2)))
    for m in re.finditer(r'\[DELEGATE_TASK:\s*"([^"]+)"\s*,\s*"details"\s*:\s*"([^"]*)"\s*\]', raw, re.DOTALL):
        calls.append(("delegate_task", json.dumps({"task": m.group(1), "details": m.group(2)})))
    for m in re.finditer(r"\[TOOL:\s*(\w+)\s*\]", raw):
        if not any(c[0] == m.group(1) for c in calls):
            calls.append((m.group(1), "{}"))
    if not calls:
        # No tools requested — return the raw reply (strip any think blocks)
        content = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
        # Remove any stray tool syntax the model leaked
        content = re.sub(r"\[TOOL:.*?\]", "", content, flags=re.DOTALL).strip()
        return content or raw.strip()

    # Execute tools and feed results back
    results_text = []
    for tname, targs_json in calls:
        try:
            targs = json.loads(targs_json)
        except Exception:
            targs = {}
        result = execute_tool(tname, targs)
        results_text.append("Tool %s returned:\n%s" % (tname, result))

    # Phase 2: final answer with tool results
    messages.append({"role": "assistant", "content": raw})
    messages.append({"role": "user", "content": "Tool results:\n\n" + "\n\n".join(results_text) + "\n\nNow answer the original request using these results. Keep it SHORT (1-3 sentences) unless he asked for detail."})
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False, "options": {"num_predict": 200}}).encode()
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    content = data["choices"][0]["message"]["content"].strip()
    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"\[TOOL:.*?\]", "", content, flags=re.DOTALL).strip()
    content = re.sub(r"\[DELEGATE_TASK:.*?\]", "", content, flags=re.DOTALL).strip()
    return content


# ---------- memory ----------

def key_of(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def load_history() -> list:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def prune_history(history: list) -> None:
    # No time-based expiry — keep everything. Cap at 5000 messages to bound file size.
    if len(history) > 5000:
        history[:] = history[-5000:]


def save_history(history: list) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=1)
    except Exception as error:
        print("could not save memory:", error)


# ---------- delegated tasks (picked up by main Jarvis via Tailscale) ----------

def load_delegated_tasks():
    if not os.path.exists(TASK_FILE):
        return []
    try:
        with open(TASK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_delegated_tasks(tasks):
    try:
        with open(TASK_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("could not save tasks:", e)


# ---------- scheduled texts ----------

def load_schedule() -> list:
    if not os.path.exists(SCHEDULE_FILE):
        return []
    try:
        with open(SCHEDULE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_schedule(items: list) -> None:
    try:
        with open(SCHEDULE_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print("could not save schedule:", e)


def next_occurrence(item):
    """Calculate next fire time for a recurring item."""
    recurring = item.get("recurring")
    if not recurring:
        return None
    at = item.get("at", time.time())
    now = time.time()
    if recurring == "daily":
        # Next day at same time
        nxt = at + 86400
        while nxt <= now:
            nxt += 86400
        return nxt
    if recurring == "weekly":
        nxt = at + 7 * 86400
        while nxt <= now:
            nxt += 7 * 86400
        return nxt
    return None


def check_schedule() -> None:
    """Send any due scheduled texts."""
    items = load_schedule()
    now = time.time()
    remaining = []
    changed = False
    for item in items:
        if item.get("at", 0) <= now:
            try:
                send(item.get("message", ""))
                print("sent scheduled text:", item.get("message", "")[:50])
                changed = True
                # Reschedule if recurring
                nxt = next_occurrence(item)
                if nxt:
                    item["at"] = nxt
                    remaining.append(item)
            except Exception as e:
                print("scheduled send failed:", e)
                remaining.append(item)
        else:
            remaining.append(item)
    if changed or len(remaining) != len(items):
        save_schedule(remaining)


class ScheduleHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/schedule_text":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                message = data.get("message", "").strip()
                at = float(data.get("at", time.time()))
                if not message:
                    raise ValueError("empty message")
                items = load_schedule()
                recurring = data.get("recurring")  # "daily", "weekly", or None
                items.append({"message": message, "at": at, "recurring": recurring, "created": time.time()})
                save_schedule(items)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/list_scheduled":
            items = load_schedule()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(items).encode())
        elif self.path == "/task_result":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                task_id = data.get("id", "")
                result = data.get("result", "")
                tasks = load_delegated_tasks()
                for t in tasks:
                    if t["id"] == task_id:
                        t["status"] = "done"
                        t["result"] = result
                        # Queue a text to the user with the result
                        items = load_schedule()
                        items.append({"message": "Task done: %s" % result, "at": time.time() + 5,
                                       "recurring": None, "created": time.time()})
                        save_schedule(items)
                        break
                save_delegated_tasks(tasks)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
            except Exception as e:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/pending_tasks":
            tasks = [t for t in load_delegated_tasks() if t["status"] == "pending"]
            # Mark as claimed so we don't double-process
            all_tasks = load_delegated_tasks()
            for t in all_tasks:
                if t["status"] == "pending":
                    t["status"] = "claimed"
            save_delegated_tasks(all_tasks)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(tasks).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path.startswith("/cancel_scheduled/"):
            try:
                idx = int(self.path.split("/")[-1])
                items = load_schedule()
                if 0 <= idx < len(items):
                    items.pop(idx)
                    save_schedule(items)
                    self.send_response(200)
                else:
                    self.send_response(404)
                self.end_headers()
            except Exception:
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):
        pass


def start_scheduler_server():
    def run():
        server = HTTPServer(("0.0.0.0", SCHEDULER_PORT), ScheduleHandler)
        print(f"Scheduler listening on port {SCHEDULER_PORT}")
        server.serve_forever()
    thread = Thread(target=run, daemon=True)
    thread.start()



# ---------- email commands ----------

def _svc_for(text_low):
    """Pick the email service based on keywords. Returns (name, module, available)."""
    if "outlook" in text_low:
        return ("Outlook", outlook_service, OUTLOOK_AVAILABLE)
    if "icloud" in text_low:
        return ("iCloud", icloud_service, ICLOUD_AVAILABLE)
    return ("Gmail", gmail_service, GMAIL_AVAILABLE)


def _not_connected(name):
    setup = {"Gmail": "python3 gmail_setup.py",
             "Outlook": "pip install msal && python3 outlook_setup.py",
             "iCloud": "python3 icloud_setup.py"}[name]
    return f"{name} isn't connected yet, sir. Run {setup} on the Mac."


def try_email_command(text: str):
    """Returns a reply string if text is an email command, else None."""
    low = text.lower().strip()
    name, svc, available = _svc_for(low)
    if not available:
        return None

    # "check my email" / "check outlook" / "any new icloud mail" / "check all email"
    check_all = "all email" in low or "all my email" in low
    if check_all or re.search(r'\b(check|read|show|any|new)\b.*\b(email|mail|inbox|outlook|icloud)\b', low) or \
       re.search(r'\b(email|mail|inbox|outlook|icloud)\b.*\b(check|new|latest|unread)\b', low):
        if check_all:
            parts = []
            for n, s, a in [("Gmail", gmail_service, GMAIL_AVAILABLE),
                            ("Outlook", outlook_service, OUTLOOK_AVAILABLE),
                            ("iCloud", icloud_service, ICLOUD_AVAILABLE)]:
                if not a:
                    continue
                try:
                    emails = s.list_inbox(3)
                    if emails:
                        lines = [f"  {i+1}. {e[1].split('<')[0].strip() or e[1]}: {e[2]}"
                                 for i, e in enumerate(emails)]
                        parts.append(f"{n}:\n" + "\n".join(lines))
                    else:
                        parts.append(f"{n}: empty")
                except RuntimeError:
                    parts.append(f"{n}: not connected")
                except Exception as e:
                    parts.append(f"{n}: error ({e})")
            return "Here's everything, sir:\n\n" + "\n\n".join(parts)
        try:
            emails = svc.list_inbox(5)
        except RuntimeError:
            return _not_connected(name)
        except Exception as e:
            return f"Couldn't reach {name}: {e}"
        if not emails:
            return f"{name} inbox is empty, sir. Enjoy the silence."
        lines = []
        for i, (mid, frm, subj, date, snip) in enumerate(emails, 1):
            frm_short = frm.split("<")[0].strip().strip('"') or frm
            lines.append(f"{i}. {frm_short}: {subj}")
        return f"Latest {name} emails, sir:\n" + "\n".join(lines) + "\n\nReply 'read 2' to open one."

    # "read 2" / "read outlook 1" / "open 3"
    m = re.match(r'^(read|open)\s+(outlook\s+|icloud\s+|gmail\s+|email\s+)?(\d+)\s*$', low)
    if m:
        idx = int(m.group(3)) - 1
        try:
            emails = svc.list_inbox(5)
        except RuntimeError:
            return _not_connected(name)
        except Exception as e:
            return f"Couldn't reach {name}: {e}"
        if 0 <= idx < len(emails):
            mid = emails[idx][0]
            try:
                frm, subj, date, body = svc.read_email(mid)
            except Exception as e:
                return f"Couldn't read that email: {e}"
            body_short = body[:1500] + ("..." if len(body) > 1500 else "")
            return f"From: {frm}\nSubject: {subj}\n\n{body_short}"
        return "That number's out of range, sir."

    # "send email to X about Y" / "email via outlook mom@x.com: ..." / "outlook email to X"
    m = re.match(r'^(send\s+)?(email|mail)\s+(via\s+(outlook|icloud|gmail)\s+)?(\S+@\S+)\s*[:\-]?\s*(.+)$',
                 text, re.IGNORECASE)
    if m:
        to, rest = m.group(5), m.group(6).strip()
        if " - " in rest:
            subject, body = rest.split(" - ", 1)
        elif "\n" in rest:
            subject, body = rest.split("\n", 1)
        else:
            subject, body = rest[:60], rest
        try:
            svc.send_email(to, subject.strip(), body.strip())
        except RuntimeError:
            return _not_connected(name)
        except Exception as e:
            return f"Send failed: {e}"
        return f"Sent via {name} to {to}, sir."
    return None


# ---------- main loop ----------

def main() -> None:
    global CHAT_ID

    if not os.path.exists(MESSAGE_DB):
        print("Couldn't find the Messages database at", MESSAGE_DB)
        print("Give Terminal Full Disk Access, then run this again.")
        sys.exit(1)
    try:
        last_messages(1)
    except Exception as error:
        print("could not read the Messages database:", error)
        print("System Settings > Privacy & Security > Full Disk Access, add Terminal,")
        print("then quit and reopen Terminal and run this again.")
        sys.exit(1)

    try:
        chat_id = resolve_chat_id()
    except Exception as error:
        print("could not list chats:", error)
        sys.exit(1)
    if not chat_id:
        print("Couldn't find a chat with your number.")
        print("Text this account once from your iPhone so the chat exists, then run again.")
        print("Chat ids Messages knows right now:")
        for existing in all_chat_ids():
            print(existing)
        sys.exit(1)
    CHAT_ID = chat_id

    print("Watching chat:", CHAT_ID)
    start_scheduler_server()
    print("Ctrl-C to stop.")

    # Anything already in the chat before startup stays unanswered.
    seen = {m["id"] for m in last_messages()}
    history = load_history()
    prune_history(history)
    if history:
        print("remembered messages:", len(history))
    sent_replies: list = []   # normalized texts the bridge itself sent
    last_user_key = ""
    last_reply_at = 0.0

    while True:
        try:
            for message in last_messages(8):
                if message["id"] in seen:
                    continue
                text = message["text"].strip()
                if not text:
                    continue
                key = key_of(text)
                if key in sent_replies:
                    # That was the bridge talking, not you.
                    seen.add(message["id"])
                    continue
                if key == last_user_key and time.time() - last_reply_at < ECHO_WINDOW_SECONDS:
                    # A copy of your text landing right after the reply.
                    seen.add(message["id"])
                    continue
                seen.add(message["id"])
                history.append({"role": "user", "content": text, "ts": time.time()})
                prune_history(history)
                # Check for new tapbacks and add as context
                for tb in recent_tapbacks():
                    tb_key = "tapback_" + tb["id"]
                    if tb_key not in seen:
                        seen.add(tb_key)
                        ctx = "[He %s your message%s]" % (tb["reaction"],
                               (": '" + tb["on"] + "'") if tb["on"] else "")
                        history.append({"role": "user", "content": ctx, "ts": time.time()})
                email_reply = try_email_command(text)
                if email_reply is not None:
                    reply = email_reply
                else:
                    try:
                        reply = ask_with_tools(history)
                    except Exception as error:
                        reply = "(model error: " + str(error) + ")"
                if reply:
                    history.append({"role": "assistant", "content": reply, "ts": time.time()})
                    prune_history(history)
                    save_history(history)
                    sent_replies.append(key_of(reply))
                    del sent_replies[:-20]
                    send(reply)
                    last_user_key = key
                    last_reply_at = time.time()
                    # Mark our own reply's row seen as soon as it lands.
                    reply_key = key_of(reply)
                    for _ in range(3):
                        time.sleep(1)
                        for recent in last_messages(3):
                            if key_of(recent["text"]) == reply_key:
                                seen.add(recent["id"])
                    print("replied:", reply[:80].replace("\n", " "))
        except Exception as error:
            print("error:", error)
        check_schedule()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chats":
        list_chats()
    else:
        main()
