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

import warnings
warnings.filterwarnings('ignore', message='.*LibreSSL.*')
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
import tempfile
import math
import subprocess
import sys
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

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
AGENT_TASKS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_tasks.json")
EXEC_LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "execution_log.jsonl")
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

SCHEDULED TASKS: When he asks for something to happen later or regularly ("at 8am", "every morning", "check X and tell me"), use create_task with an INSTRUCTION describing what to do when it fires. You will run that instruction fresh at fire time with full tool access and text him the result yourself \u2014 that proactive text is wanted, he asked for it. NEVER use schedule_text for a job that needs doing; schedule_text only replays pre-written reminder text verbatim. When he asks whether a task ran, ALWAYS call task_status first \u2014 the execution log is the only truth; never answer from memory.
SCHEDULED EMAIL RULE: If a request combines future or recurring timing with checking email and notifying him, use create_task with the full instruction. Never check email immediately, use email_briefing immediately, or create a static schedule_text/local reminder for that request.

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
- create_task | {"name": "Morning email briefing", "instruction": "Check all my email accounts and send me only what is vital", "when": "8am", "recurring": "daily"} \u2014 Create a real agent task that runs the instruction at fire time and texts him the result. recurring: none/daily/weekdays/weekly.
- list_tasks | {} \u2014 List agent tasks with next run time and last status.
- delete_task | {"index": 0} \u2014 Delete an agent task by its number.
- task_status | {} \u2014 Show the execution log: what actually ran, when, and whether it worked.
- email_briefing | {} \u2014 One consolidated digest of Gmail, Outlook and iCloud: only vital mail (security, banking, jobs, legal, deadlines), each with sender, subject and why it matters. Provider failures are reported, never hidden.
- remember_fact | {"fact": "..."} \u2014 Save something to permanent memory. Never forgets.
- recall_facts | {} \u2014 List everything in permanent memory.
- add_calendar_event | {"title": "...", "when": "tomorrow at 3pm", "duration_min": 60} \u2014 Add event to macOS Calendar.
- list_calendar_events | {"days": 7} \u2014 List upcoming calendar events.
- get_briefing | {} — Combined briefing: weather, calendar, headlines.
- web_search | {"query": "...", "count": 5} \u2014 Search the web.
- get_weather | {"location": "Charlotte"} \u2014 Current weather.
- get_news | {"count": 5} \u2014 Top news headlines.
- get_stock | {"symbol": "AAPL"} \u2014 Stock price.
- get_crypto | {"symbol": "BTC"} \u2014 Crypto price.
- define_word | {"word": "..."} \u2014 Dictionary definition.
- list_email_accounts | {}
- lookup_contact | {"name": "Mom"} — Find a contact by name.
- add_reminder | {"title": "Buy milk"} — Add an Apple Reminder.
- list_reminders | {} — List open Apple Reminders.
- system_status | {} — Mac battery and volume.
- music_control | {"action": "play/pause/next/previous/toggle"}
- set_volume | {"level": 50} — Set Mac volume 0-100.
- lock_mac | {} — Lock the Mac screen.
- sleep_display | {} — Put the display to sleep.
- open_app | {"app": "Safari"} — Open an app on the Mac.
- take_screenshot | {} — Take a Mac screenshot.
- get_clipboard | {}
- save_macro | {"name": "movie night", "steps": [{"tool": "set_volume", "args": {"level": 30}}, {"tool": "open_app", "args": {"app": "Netflix"}}]} — Save a multi-step macro.
- run_macro | {"name": "movie night"} — Run a saved macro.
- list_macros | {} — List saved macros.
- delete_macro | {"name": "movie night"} — Delete a macro. — Read the Mac clipboard. — Control Music.app. \u2014 List all configured email providers and connection status. Use for "what accounts" questions, NOT inbox contents.
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
            if not isinstance(emails, (list, tuple)):
                raise RuntimeError("provider returned an invalid result")
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
    """Parse relative delays and local wall-clock schedule times."""
    import re as _re
    s = when_str.lower().strip()
    now = time.time()
    if s == "tomorrow":
        return (datetime.now(_local_zone()) + timedelta(days=1)).timestamp()
    m = _re.match(r"in\s+(\d+)\s*(minute|min|hour|hr)", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        return now + (n * 3600 if unit.startswith("hour") or unit == "hr" else n * 60)
    # Weekday names: "Monday", "next Friday", "Monday at 3pm"
    weekdays = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
                "friday": 4, "saturday": 5, "sunday": 6}
    m = _re.search(r"(next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)(\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm))?", s)
    if m:
        target = weekdays[m.group(2)]
        is_next = bool(m.group(1))
        today = datetime.now().weekday()
        days_ahead = (target - today) % 7
        if days_ahead == 0 and not is_next:
            days_ahead = 7  # "Monday" on a Monday means next Monday
        if is_next:
            days_ahead += 7
        dt = datetime.now() + timedelta(days=days_ahead)
        if m.group(3):  # time specified
            hour = int(m.group(4))
            minute = int(m.group(5) or 0)
            ampm = m.group(6)
            if ampm == "pm" and hour < 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0
            dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        else:
            dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
        return dt.timestamp()
    m = _re.search(r"(?:(today|tomorrow)\s+)?at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s)
    if m:
        day = m.group(1)
        hour = int(m.group(2))
        minute = int(m.group(3) or 0)
        ampm = m.group(4)
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        local_now = datetime.now(_local_zone())
        dt = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if day == "tomorrow":
            dt += timedelta(days=1)
        elif day is None and dt < local_now:
            dt += timedelta(days=1)
        return dt.timestamp()
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
    cutoff = now + timedelta(days=days)
    script = 'tell application "Calendar"\n'
    script += 'set out to ""\n'
    script += 'repeat with cal in calendars\n'
    script += 'repeat with evt in (every event of cal)\n'
    script += 'set out to out & (summary of evt) & "|" & (start date of evt) & "\n"\n'
    script += 'end repeat\nend repeat\n'
    script += 'return out\nend tell'
    out, err = _cal_run(script)
    if err:
        return "Calendar error: %s" % err[:200]
    if not out.strip():
        return "Nothing on your calendar."
    # Filter by date in Python instead of AppleScript
    lines = []
    for line in out.strip().split("\n"):
        if "|" in line:
            lines.append("- " + line.replace("|", " at "))
        if len(lines) >= 20:
            break
    return "Upcoming events:\n" + "\n".join(lines) if lines else "Nothing on your calendar." 



# ---------- info tools (web search, weather, news, stocks, crypto, dictionary) ----------

def _http_get(url, timeout=15):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _bridge_web_search(query, count=5):
    """Multi-source web search: Brave API (if key set), Wikipedia, DDG Instant Answer."""
    import urllib.parse, json as js, re
    results = []

    # 1. Brave Search API (free tier, needs key in ~/.chatbot/brave_key.txt)
    brave_key = None
    for p in ["/Users/AI/.chatbot/brave_key.txt", __import__("os").path.expanduser("~/.chatbot/brave_key.txt")]:
        try:
            with open(p) as f:
                brave_key = f.read().strip()
                break
        except:
            pass
    if brave_key:
        try:
            req = __import__("urllib.request").Request(
                "https://api.search.brave.com/res/v1/web/search?q=%s&count=%d" % (urllib.parse.quote(query), count),
                headers={"X-Subscription-Token": brave_key, "Accept": "application/json"})
            with __import__("urllib.request").urlopen(req, timeout=15) as resp:
                data = js.loads(resp.read().decode())
            for r in data.get("web", {}).get("results", [])[:count]:
                results.append("- %s: %s" % (r.get("title", ""), r.get("description", "")[:200]))
            if results:
                return "Search results for '%s':\n" % query + "\n".join(results)
        except Exception:
            pass

    # 2. Wikipedia (reliable, no key) - try multiple variations
    import os as _os
    wiki_tries = [query]
    # Strip common suffixes like "facts", "info", "about"
    for suffix in [" facts", " info", " about", " definition", " meaning"]:
        if query.lower().endswith(suffix):
            wiki_tries.append(query[:-len(suffix)].strip())
    # Try singular last word
    words = query.split()
    if len(words) > 1:
        wiki_tries.append(words[-1])
    for wt in wiki_tries:
        try:
            w_url = "https://en.wikipedia.org/api/rest_v1/page/summary/%s" % urllib.parse.quote(wt.replace(" ", "_"))
            w_data = js.loads(_http_get(w_url))
            if w_data.get("extract") and w_data.get("type") != "disambiguation":
                results.append("- Wikipedia (%s): %s" % (wt, w_data["extract"][:400]))
                break
        except Exception:
            continue

    # 3. DDG Instant Answer
    try:
        ddg_url = "https://api.duckduckgo.com/?q=%s&format=json&no_html=1" % urllib.parse.quote(query)
        ddg_data = js.loads(_http_get(ddg_url))
        if ddg_data.get("Abstract"):
            results.append("- %s" % ddg_data["Abstract"][:400])
        for t in ddg_data.get("RelatedTopics", [])[:3]:
            if isinstance(t, dict) and t.get("Text"):
                results.append("- %s" % t["Text"][:200])
    except Exception:
        pass

    if not results:
        return "No results for '%s'. For full web search, add a Brave Search API key to ~/.chatbot/brave_key.txt (free at brave.com/search/api)." % query
    return "Search results for '%s':\n" % query + "\n".join(results[:count])


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


def _execute_tool_inner(name, args):
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
        import glob, re
        out = []
        # Gmail: read email from credential file
        gmail_addrs = []
        for p in ["/Users/AI/.chatbot/gmail_imap.json", os.path.expanduser("~/.chatbot/gmail_imap.json")]:
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        d = json.load(f)
                    if isinstance(d, dict) and d.get("email"):
                        gmail_addrs.append(d["email"])
                except:
                    pass
                break
        # Outlook: from OAuth token filenames
        outlook_addrs = []
        for d in ["/Users/AI/.chatbot", os.path.expanduser("~/.chatbot")]:
            if os.path.isdir(d):
                for fp in glob.glob(os.path.join(d, "outlook_token_*.json")):
                    m = re.search(r"outlook_token_(.+?)\.json", os.path.basename(fp))
                    if m and m.group(1) not in outlook_addrs:
                        outlook_addrs.append(m.group(1))
        # Fallback to IMAP creds if no OAuth tokens
        if not outlook_addrs:
            for p in ["/Users/AI/.chatbot/outlook_imap.json", os.path.expanduser("~/.chatbot/outlook_imap.json")]:
                if os.path.exists(p):
                    try:
                        with open(p) as f:
                            d = json.load(f)
                        accts = d if isinstance(d, list) else [d]
                        for a in accts:
                            if isinstance(a, dict) and a.get("email"):
                                outlook_addrs.append(a["email"])
                    except:
                        pass
                    break
        # iCloud: from credential file
        icloud_addrs = []
        for p in ["/Users/AI/.chatbot/icloud.json", os.path.expanduser("~/.chatbot/icloud.json")]:
            if os.path.exists(p):
                try:
                    with open(p) as f:
                        d = json.load(f)
                    if isinstance(d, dict) and d.get("email"):
                        icloud_addrs.append(d["email"])
                    elif isinstance(d, dict) and d.get("apple_id"):
                        icloud_addrs.append(d["apple_id"])
                except:
                    pass
                break
        for label, addrs in [("Gmail", gmail_addrs), ("Outlook", outlook_addrs), ("iCloud", icloud_addrs)]:
            if addrs:
                out.append("%s: %s" % (label, ", ".join(addrs)))
            else:
                out.append("%s: not connected" % label)
        return "Email accounts:\n" + "\n".join("- " + l for l in out)

    if name == "lookup_contact":
        import subprocess as sp
        query = args.get("name", "").strip()
        if not query:
            return "No name given."
        q = query.replace('"', '')
        script = 'tell application "Contacts"\n'
        script += 'set matches to {}\n'
        script += 'repeat with p in people\n'
        script += f'if (name of p contains "{q}") then\n'
        script += 'set end of matches to (name of p)\n'
        script += 'end if\n'
        script += 'end repeat\n'
        script += 'return matches\n'
        script += 'end tell'
        try:
            r = sp.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
            out = r.stdout.strip()
            if not out or out == "{}":
                return "No contact found for '%s'." % query
            return "Contacts matching '%s':\n%s" % (query, out.replace(", ", "\n"))
        except Exception as e:
            return "Couldn't look up contacts: %s" % e

    if name == "add_reminder":
        import subprocess as sp
        title = args.get("title", "").strip()
        if not title:
            return "No reminder title given."
        t = title.replace('"', '')
        script = 'tell application "Reminders" to make new reminder with properties {name:"%s"}' % t
        try:
            sp.run(["osascript", "-e", script], capture_output=True, timeout=15)
            return "Reminder added: %s" % title
        except Exception as e:
            return "Couldn't add reminder: %s" % e

    if name == "list_reminders":
        import subprocess as sp
        script = 'tell application "Reminders"\n'
        script += 'set out to {}\n'
        script += 'repeat with r in reminders\n'
        script += 'if not completed of r then\n'
        script += 'set end of out to name of r\n'
        script += 'end if\n'
        script += 'end repeat\n'
        script += 'return out\n'
        script += 'end tell'
        try:
            r = sp.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
            out = r.stdout.strip()
            if not out or out == "{}":
                return "No open reminders."
            return "Open reminders:\n%s" % out.replace(", ", "\n")
        except Exception as e:
            return "Couldn't list reminders: %s" % e

    if name == "system_status":
        import subprocess as sp
        parts = []
        try:
            r = sp.run(["pmset", "-g", "batt"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.split("\n"):
                if "%" in line:
                    parts.append("Battery: " + line.strip().split(";")[0].strip())
                    break
        except:
            pass
        try:
            r = sp.run(["osascript", "-e", "output volume of (get volume settings)"],
                       capture_output=True, text=True, timeout=10)
            parts.append("Volume: %s%%" % r.stdout.strip())
        except:
            pass
        return "\n".join(parts) if parts else "Couldn't get system status."

    if name == "music_control":
        import subprocess as sp
        action = args.get("action", "").lower().strip()
        cmds = {"play": "play", "pause": "pause", "next": "next track",
                "previous": "previous track", "toggle": "playpause"}
        if action not in cmds:
            return "Action must be play, pause, next, previous, or toggle."
        try:
            sp.run(["osascript", "-e", 'tell application "Music" to %s' % cmds[action]],
                   capture_output=True, timeout=10)
            r = sp.run(["osascript", "-e",
                        'tell application "Music" to (name of current track & " - " & artist of current track)'],
                       capture_output=True, text=True, timeout=10)
            track = r.stdout.strip()
            msg = "Music %s." % action
            if track:
                msg += " Now playing: %s" % track
            return msg
        except Exception as e:
            return "Couldn't control music: %s" % e


    if name == "set_volume":
        import subprocess as sp
        try:
            vol = int(args.get("level", 50))
            vol = max(0, min(100, vol))
            sp.run(["osascript", "-e", "set volume output volume %d" % vol],
                   capture_output=True, timeout=10)
            return "Volume set to %d%%." % vol
        except Exception as e:
            return "Couldn't set volume: %s" % e

    if name == "lock_mac":
        import subprocess as sp
        try:
            sp.run(["osascript", "-e",
                    'tell application "System Events" to keystroke "q" using {control down, command down}'],
                   capture_output=True, timeout=10)
            return "Mac locked."
        except Exception as e:
            return "Couldn't lock: %s" % e

    if name == "sleep_display":
        import subprocess as sp
        try:
            sp.run(["pmset", "displaysleepnow"], capture_output=True, timeout=10)
            return "Display sleeping."
        except Exception as e:
            return "Couldn't sleep display: %s" % e

    if name == "open_app":
        import subprocess as sp
        app = args.get("app", "").strip()
        if not app:
            return "No app name given."
        a = app.replace('"', '')
        try:
            sp.run(["open", "-a", a], capture_output=True, timeout=15)
            return "Opening %s." % app
        except Exception as e:
            return "Couldn't open %s: %s" % (app, e)

    if name == "take_screenshot":
        import subprocess as sp, time
        try:
            ts = int(time.time())
            p = "/tmp/bridge_screenshot_%d.png" % ts
            sp.run(["screencapture", "-x", p], capture_output=True, timeout=15)
            if os.path.exists(p):
                return "Screenshot saved to %s." % p
            return "Screenshot failed."
        except Exception as e:
            return "Couldn't take screenshot: %s" % e

    if name == "get_clipboard":
        import subprocess as sp
        try:
            r = sp.run(["pbpaste"], capture_output=True, text=True, timeout=10)
            clip = r.stdout.strip()
            if not clip:
                return "Clipboard is empty."
            if len(clip) > 500:
                clip = clip[:500] + "..."
            return "Clipboard:\n%s" % clip
        except Exception as e:
            return "Couldn't read clipboard: %s" % e


    if name == "save_macro":
        mname = args.get("name", "").strip().lower()
        steps = args.get("steps", [])
        if not mname:
            return "No macro name given."
        if not steps or not isinstance(steps, list):
            return "No steps given. Steps should be a list of {tool, args}."
        try:
            mp = os.path.expanduser("~/.chatbot/macros.json")
            ai_mp = "/Users/AI/.chatbot/macros.json"
            use_p = ai_mp if os.path.isdir("/Users/AI/.chatbot") else mp
            macros = {}
            if os.path.exists(use_p):
                with open(use_p) as f:
                    macros = json.load(f)
            macros[mname] = steps
            os.makedirs(os.path.dirname(use_p), exist_ok=True)
            with open(use_p, "w") as f:
                json.dump(macros, f, indent=2)
            return "Macro '%s' saved with %d steps." % (mname, len(steps))
        except Exception as e:
            return "Couldn't save macro: %s" % e

    if name == "run_macro":
        mname = args.get("name", "").strip().lower()
        if not mname:
            return "No macro name given."
        try:
            macros = {}
            for p in ["/Users/AI/.chatbot/macros.json", os.path.expanduser("~/.chatbot/macros.json")]:
                if os.path.exists(p):
                    with open(p) as f:
                        macros = json.load(f)
                    break
            if mname not in macros:
                return "No macro named '%s'. Use list_macros to see saved ones." % mname
            results = []
            for step in macros[mname]:
                tool = step.get("tool", "")
                targs = step.get("args", {})
                try:
                    r = _execute_tool_inner(tool, targs)
                    results.append("%s: %s" % (tool, str(r)[:100]))
                except Exception as e:
                    results.append("%s: FAILED (%s)" % (tool, e))
            return "Ran macro '%s':\n%s" % (mname, "\n".join(results))
        except Exception as e:
            return "Couldn't run macro: %s" % e

    if name == "list_macros":
        try:
            macros = {}
            for p in ["/Users/AI/.chatbot/macros.json", os.path.expanduser("~/.chatbot/macros.json")]:
                if os.path.exists(p):
                    with open(p) as f:
                        macros = json.load(f)
                    break
            if not macros:
                return "No macros saved yet."
            out = []
            for mname, steps in macros.items():
                out.append("%s (%d steps)" % (mname, len(steps)))
            return "Saved macros:\n" + "\n".join("- " + l for l in out)
        except Exception as e:
            return "Couldn't list macros: %s" % e

    if name == "delete_macro":
        mname = args.get("name", "").strip().lower()
        if not mname:
            return "No macro name given."
        try:
            use_p = None
            macros = {}
            for p in ["/Users/AI/.chatbot/macros.json", os.path.expanduser("~/.chatbot/macros.json")]:
                if os.path.exists(p):
                    with open(p) as f:
                        macros = json.load(f)
                    use_p = p
                    break
            if mname not in macros:
                return "No macro named '%s'." % mname
            del macros[mname]
            with open(use_p, "w") as f:
                json.dump(macros, f, indent=2)
            return "Macro '%s' deleted." % mname
        except Exception as e:
            return "Couldn't delete macro: %s" % e


    if name == "get_briefing":
        parts = []
        try:
            w = _execute_tool_inner("get_weather", {})
            parts.append("Weather:\n%s" % w)
        except Exception as e:
            parts.append("Weather: unavailable (%s)" % e)
        try:
            c = _execute_tool_inner("list_calendar_events", {"days": 1})
            parts.append("Today's calendar:\n%s" % c)
        except Exception as e:
            parts.append("Calendar: unavailable (%s)" % e)
        try:
            n = _execute_tool_inner("get_news", {"count": 5})
            parts.append("Headlines:\n%s" % n)
        except Exception as e:
            parts.append("News: unavailable (%s)" % e)
        return "\n\n".join(parts)

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
    if name == "create_task":
        return _bridge_create_task(args.get("name", ""), args.get("instruction", ""), args.get("when", ""), args.get("recurring", "none"))
    if name == "list_tasks":
        return _bridge_list_tasks()
    if name == "delete_task":
        return _bridge_delete_task(int(args.get("index", -1)))
    if name == "task_status":
        return _bridge_task_status()
    if name == "email_briefing":
        return _bridge_email_briefing()
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


def execute_tool(name, args):
    """Wrapper: never hang, always respond in plain English on failure."""
    import traceback
    if _CAPTURE_ACTIVE:
        _RUN_TOOL_CAPTURE.append(name)
    try:
        return _execute_tool_inner(name, args)
    except Exception as e:
        # Log the technical details for debugging
        tb = traceback.format_exc()
        try:
            with open("/Users/AI/.chatbot/tool_errors.log", "a") as f:
                f.write(f"\n[{name}] {e}\n{tb}\n")
        except:
            pass
        # Plain English to the user (no jargon per his preference)
        friendly = {
            "check_email": "I couldn't check your email right now.",
            "read_email": "I couldn't open that email.",
            "send_email": "I couldn't send that email.",
            "list_email_accounts": "I couldn't list your email accounts.",
        }
        msg = friendly.get(name, f"The {name} tool ran into a problem.")
        return f"{msg} It's logged and I'll get it fixed. (Error: {e})"


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
    # Log tool calls for debugging hallucinations
    import datetime as _dt
    def _log_tool(name, args, result):
        try:
            with open("/Users/AI/.chatbot/tool_log.txt", "a") as f:
                f.write("%s | %s | %s | %s\n" % (_dt.datetime.now().isoformat(), name, str(args)[:200], str(result)[:200]))
        except:
            pass
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
    messages.append({"role": "user", "content": "Tool results:\n\n" + "\n\n".join(results_text) + "\n\nNow answer the original request using these results. Keep it SHORT (1-3 sentences) unless he asked for detail.\nCRITICAL: NEVER claim you completed an action (added event, sent email, etc.) unless the tool result above confirms it. If you did not call a tool, say so honestly. Making up confirmations is the worst thing you can do."})
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




# ---------- agent tasks: scheduled jobs executed fresh at fire time ----------
# A task stores an INSTRUCTION, not a pre-written message. When it fires, the
# instruction runs through the same model+tools pipeline as a live text, and
# the result is texted to Elijah on its own. Every run is written to the
# execution log, so "did it run?" always has a provable answer.

_RUN_TOOL_CAPTURE = []
_CAPTURE_ACTIVE = False

VITAL_TERMS = (
    "security alert", "unusual activity", "suspicious activity", "sign-in",
    "signin", "login attempt", "password", "verification", "verify your",
    "one-time code", "one time code", "passcode", "access code",
    "account locked", "account suspended", "account disabled",
    "payment failed", "payment declined", "payment overdue", "past due",
    "overdraft", "fraud", "chargeback", "bank notice", "tax notice",
    "interview", "job offer", "application update", "recruiter",
    "background check", "onboarding", "benefits enrollment",
    "court", "legal notice", "summons", "lease", "eviction",
    "medical", "doctor", "appointment reminder", "prescription",
    "action required", "response required", "important account",
)
STRONG_TERMS = (
    "security", "password", "verification", "verify", "code", "fraud",
    "overdraft", "payment", "interview", "court", "legal", "eviction",
    "locked", "suspended", "account",
)
PROMO_TERMS = (
    "unsubscribe", "newsletter", "weekly digest", "daily digest", "sale",
    "deal", "promo", "promotion", "coupon", "clearance", "black friday",
    "cyber monday", "marketing", "sponsored",
)


def _load_persisted_list(path, label):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise RuntimeError("Could not load %s from %s: %s" % (label, path, e)) from e
    if not isinstance(data, list):
        raise RuntimeError("Malformed %s at %s: expected a JSON array." % (label, path))
    if any(not isinstance(item, dict) for item in data):
        raise RuntimeError("Malformed %s at %s: every entry must be a JSON object." % (label, path))
    return data


def _save_persisted_list(path, items, label):
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=os.path.dirname(path),
            prefix="." + os.path.basename(path) + ".", suffix=".tmp", delete=False
        ) as f:
            temp_path = f.name
            json.dump(items, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception as e:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
        raise RuntimeError("Could not save %s to %s: %s" % (label, path, e)) from e


def load_agent_tasks() -> list:
    return _load_persisted_list(AGENT_TASKS_FILE, "agent tasks")
def save_agent_tasks(tasks: list) -> None:
    _save_persisted_list(AGENT_TASKS_FILE, tasks, "agent tasks")
def log_execution(kind, name, ok, detail="", tools=None):
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"),
                 "kind": kind, "name": name, "ok": bool(ok),
                 "tools": tools or [], "detail": str(detail)[:300]}
        with open(EXEC_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _local_zone():
    name = os.environ.get("TZ", "").lstrip(":")
    if name:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    for path in ("/etc/localtime", "/var/db/timezone/zoneinfo/localtime"):
        try:
            with open(path, "rb") as f:
                return ZoneInfo.from_file(f)
        except (OSError, ValueError):
            pass
    return datetime.now().astimezone().tzinfo


def _calendar_next_timestamp(at_ts, after_ts, days, weekdays_only=False):
    zone = _local_zone()
    candidate = datetime.fromtimestamp(at_ts, zone)
    while True:
        candidate += timedelta(days=days)
        if candidate.timestamp() > after_ts and (not weekdays_only or candidate.weekday() < 5):
            return candidate.timestamp()


def _next_daily(hour, minute):
    now = datetime.now(_local_zone())
    cand = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand.timestamp() <= time.time():
        cand += timedelta(days=1)
    return cand.timestamp()
def _task_next_fire(task, after_ts):
    rec = task.get("recurring", "none")
    at = task.get("at", 0)
    if rec == "daily":
        return _calendar_next_timestamp(at, after_ts, 1)
    if rec == "weekly":
        return _calendar_next_timestamp(at, after_ts, 7)
    if rec == "weekdays":
        return _calendar_next_timestamp(at, after_ts, 1, weekdays_only=True)
    return None
def _vital_reason(frm, subj):
    hay = (str(frm) + " " + str(subj)).lower()
    hits = [t for t in VITAL_TERMS if t in hay]
    if not hits:
        return None
    if any(t in hay for t in PROMO_TERMS) and not any(s in hay for s in STRONG_TERMS):
        return None
    return hits[0]


def _bridge_email_briefing(count=15):
    lines = ["Email briefing — %s" % datetime.now().strftime("%A %b %d, %I:%M %p")]
    any_vital = False
    checked_any = False
    for p in ("gmail", "outlook", "icloud"):
        name, svc = _email_svc(p)
        if not svc:
            lines.append("\n%s: NOT CONNECTED — the module did not load. Run its setup." % name)
            continue
        try:
            emails = svc.list_inbox(count)
            if not isinstance(emails, (list, tuple)):
                raise RuntimeError("provider returned an invalid result")
        except Exception as ex:
            lines.append("\n%s: COULD NOT CHECK — %s. The connection needs attention." % (name, ex))
            continue
        checked_any = True
        vital, other = [], 0
        for e in emails:
            ne = _norm_email(e)
            reason = _vital_reason(ne.get("from", ""), ne.get("subject", ""))
            if reason:
                vital.append((ne, reason))
            else:
                other += 1
        if vital:
            any_vital = True
            lines.append("\n%s — %d that need you:" % (name, len(vital)))
            for ne, reason in vital:
                sender = re.sub(r"\s*<[^>]+>", "", str(ne.get("from", ""))).strip() or ne.get("from", "?")
                lines.append("- %s: %s (flagged: %s)" % (sender, ne.get("subject", "(no subject)"), reason))
        else:
            lines.append("\n%s: nothing vital." % name)
        if other:
            lines.append("  (+%d other messages that do not need you.)" % other)
    if not any_vital:
        lines.append("\nNo vital messages were found in inboxes that were successfully checked." if checked_any else "\nNo email inbox could be checked, sir.")
    return "\n".join(lines)
def _bridge_create_task(name, instruction, when_str, recurring="none"):
    if not instruction:
        return "I need to know what the task should do, sir."
    recurring = (recurring or "none").lower().strip()
    if recurring not in ("none", "daily", "weekdays", "weekly"):
        recurring = "none"
    at = None
    if recurring in ("daily", "weekdays", "weekly"):
        s = (when_str or "").lower()
        m = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", s)
        if m:
            hour = int(m.group(1))
            minute = int(m.group(2) or 0)
            if m.group(3) == "pm" and hour < 12:
                hour += 12
            if m.group(3) == "am" and hour == 12:
                hour = 0
            at = _next_daily(hour, minute)
        else:
            m2 = re.search(r"(\d{1,2}):(\d{2})", s)
            if m2:
                at = _next_daily(int(m2.group(1)), int(m2.group(2)))
    if at is None:
        at = _parse_when(when_str or "")
    if at is None:
        return "I could not work out when to run that, sir. Try 'tomorrow at 8am' or 'every day at 8am'."
    tasks = load_agent_tasks()
    task = {"id": int(time.time()), "name": name or instruction[:40],
            "instruction": instruction, "at": at, "recurring": recurring,
            "enabled": True, "last_run": None, "last_status": None}
    tasks.append(task)
    save_agent_tasks(tasks)
    when_txt = datetime.fromtimestamp(at).strftime("%a %b %d, %I:%M %p")
    rec_txt = "" if recurring == "none" else " (%s)" % recurring
    log_execution("task_created", task["name"], True, "first run %s%s" % (when_txt, rec_txt))
    return "Task set, sir: '%s' — first run %s%s. I will text you the result when it fires." % (task["name"], when_txt, rec_txt)


def _bridge_list_tasks():
    tasks = load_agent_tasks()
    if not tasks:
        return "No agent tasks set, sir."
    out = []
    for i, t in enumerate(tasks):
        nxt = datetime.fromtimestamp(t.get("at", 0)).strftime("%a %b %d, %I:%M %p")
        state = "on" if t.get("enabled", True) else "off/done"
        last = t.get("last_status") or "not run yet"
        out.append("%d. \"%s\" — %s, next: %s [%s] (last: %s)" % (
            i, t.get("name", "?"), t.get("recurring", "none"), nxt, state, last))
    return "\n".join(out)


def _bridge_delete_task(index):
    tasks = load_agent_tasks()
    if 0 <= index < len(tasks):
        gone = tasks.pop(index)
        save_agent_tasks(tasks)
        return "Deleted task '%s', sir." % gone.get("name", "?")
    return "No task at number %d, sir." % index


def _bridge_task_status():
    if not os.path.exists(EXEC_LOG_FILE):
        return "No executions logged yet, sir — nothing has run."
    try:
        with open(EXEC_LOG_FILE, "r", encoding="utf-8") as f:
            lines = [l for l in f.read().splitlines() if l.strip()]
    except Exception as e:
        return "Could not read the execution log: %s" % e
    out = []
    for raw in lines[-8:]:
        try:
            e = json.loads(raw)
        except Exception:
            continue
        when = e.get("ts", "")[5:16].replace("T", " ")
        tools = ", ".join(e.get("tools", [])) or "no tools"
        out.append("- %s %s \"%s\": %s (%s)%s" % (
            when, e.get("kind", "?"), e.get("name", "?"),
            "OK" if e.get("ok") else "FAILED", tools,
            (" — " + e["detail"][:120]) if e.get("detail") else ""))
    return "Execution log (latest last):\n" + "\n".join(out) if out else "Log is empty, sir."


def _run_agent_task(task):
    global _CAPTURE_ACTIVE
    name = task.get("name", "task")
    instruction = task.get("instruction", "")
    del _RUN_TOOL_CAPTURE[:]
    _CAPTURE_ACTIVE = True
    try:
        note = ("[SCHEDULED TASK — RUNNING NOW. Elijah set this up earlier and is not watching. "
                "Do the job completely with your tools, then write the exact message he should receive. "
                "He asked for this proactively. If part of it fails, say plainly what failed and why.]\n\nTask: " + instruction)
        reply = ask_with_tools([{"role": "user", "content": note}])
        if not reply:
            raise RuntimeError("the model returned nothing")
        send(reply)
        task["last_status"] = "ok %s" % datetime.now().strftime("%b %d %I:%M %p")
        log_execution("agent_task", name, True, reply[:200], list(_RUN_TOOL_CAPTURE))
        print("agent task ran:", name)
    except Exception as e:
        task["last_status"] = "FAILED %s: %s" % (datetime.now().strftime("%b %d %I:%M %p"), e)
        sent = False
        low = instruction.lower()
        if "email" in low or "inbox" in low:
            try:
                send(_bridge_email_briefing())
                sent = True
                log_execution("agent_task", name, True,
                              "model run failed (%s); sent the raw briefing instead" % e,
                              ["email_briefing"])
            except Exception:
                pass
        if not sent:
            try:
                send("Task '%s' failed, sir: %s" % (name, e))
            except Exception:
                pass
            log_execution("agent_task", name, False, str(e), list(_RUN_TOOL_CAPTURE))
        print("agent task failed:", name, e)
    finally:
        _CAPTURE_ACTIVE = False


def check_agent_tasks() -> None:
    """Fire due tasks only after their next run has been persisted."""
    try:
        tasks = load_agent_tasks()
    except Exception as e:
        print("could not load agent tasks:", e)
        log_execution("agent_task", "task store", False, str(e))
        return
    if not tasks:
        return
    now = time.time()
    changed = False
    for task in tasks:
        if not task.get("enabled", True) or task.get("at", 0) > now:
            continue
        nxt = _task_next_fire(task, now)
        if nxt:
            task["at"] = nxt
        else:
            task["enabled"] = False
        task["last_run"] = datetime.now().isoformat(timespec="seconds")
        changed = True
        try:
            save_agent_tasks(tasks)
        except Exception as e:
            print("agent task was not run because its next run could not be saved:", e)
            log_execution("agent_task", task.get("name", "task"), False, str(e))
            return
        _run_agent_task(task)
    if changed:
        try:
            save_agent_tasks(tasks)
        except Exception as e:
            print("could not persist agent task result:", e)
            log_execution("agent_task", "task store", False, str(e))
def load_schedule() -> list:
    return _load_persisted_list(SCHEDULE_FILE, "scheduled reminders")
def save_schedule(items: list) -> None:
    _save_persisted_list(SCHEDULE_FILE, items, "scheduled reminders")
def next_occurrence(item):
    """Calculate the next local calendar occurrence of a recurring item."""
    recurring = item.get("recurring")
    if recurring == "daily":
        return _calendar_next_timestamp(item.get("at", time.time()), time.time(), 1)
    if recurring == "weekly":
        return _calendar_next_timestamp(item.get("at", time.time()), time.time(), 7)
    return None
def check_schedule() -> None:
    """Send any due scheduled texts."""
    try:
        items = load_schedule()
    except Exception as e:
        print("could not load scheduled reminders:", e)
        log_execution("scheduled_text", "schedule store", False, str(e))
        return
    now = time.time()
    remaining = []
    changed = False
    for item in items:
        if item.get("at", 0) <= now:
            try:
                send(item.get("message", ""))
                print("sent scheduled text:", item.get("message", "")[:50])
                log_execution("scheduled_text", item.get("message", "")[:60], True)
                changed = True
                nxt = next_occurrence(item)
                if nxt:
                    item["at"] = nxt
                    remaining.append(item)
            except Exception as e:
                print("scheduled send failed:", e)
                log_execution("scheduled_text", item.get("message", "")[:60], False, str(e))
                remaining.append(item)
        else:
            remaining.append(item)
    if changed or len(remaining) != len(items):
        try:
            save_schedule(remaining)
        except Exception as e:
            print("could not persist scheduled reminders:", e)
            log_execution("scheduled_text", "schedule store", False, str(e))
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
        elif self.path == "/create_task":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                instruction = str(data.get("instruction", "")).strip()
                name = str(data.get("name", "Scheduled task")).strip() or "Scheduled task"
                at = float(data.get("at", 0))
                recurring = str(data.get("recurring", "none") or "none").lower()
                if not instruction:
                    raise ValueError("empty instruction")
                if recurring not in ("none", "daily", "weekdays", "weekly"):
                    raise ValueError("unsupported recurrence: " + recurring)
                if not math.isfinite(at) or at <= time.time():
                    raise ValueError("scheduled time must be in the future")
                tasks = load_agent_tasks()
                task = {"id": int(time.time() * 1000000), "name": name,
                        "instruction": instruction, "at": at, "recurring": recurring,
                        "enabled": True, "last_run": None, "last_status": None}
                tasks.append(task)
                save_agent_tasks(tasks)
                log_execution("task_created", name, True, "first run %s" % datetime.fromtimestamp(at).strftime("%a %b %d, %I:%M %p"))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "id": task["id"]}).encode())
            except ValueError as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
            except Exception as e:
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
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
    schedule_requested = re.search(r'\b(?:tomorrow|tonight|daily|weekdays|weekly|every\s+(?:day|morning|week|weekday)|in\s+\d+\s+(?:minutes?|hours?|days?)|at\s+\d{1,2}(?::\d{2})?\s*(?:am|pm))\b', low)
    email_check_requested = re.search(r'\b(check|read|show|any|new|latest|unread|briefing|digest)\b.*\b(email|mail|inbox|gmail|outlook|icloud)\b', low) or re.search(r'\b(email|mail|inbox|gmail|outlook|icloud)\b.*\b(check|new|latest|unread|briefing|digest)\b', low)
    if schedule_requested and email_check_requested:
        return None
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
                    parts.append(f"{n}: not connected")
                    continue
                try:
                    emails = s.list_inbox(3)
                    if not isinstance(emails, (list, tuple)):
                        raise RuntimeError("provider returned an invalid result")
                    if emails:
                        lines = []
                        for i, e in enumerate(emails):
                            ne = _norm_email(e)
                            frm = ne["from"].split("<")[0].strip() or ne["from"]
                            lines.append(f"  {i+1}. {frm}: {ne['subject']}")
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
        check_agent_tasks()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chats":
        list_chats()
    else:
        main()
