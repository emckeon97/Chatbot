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
from datetime import datetime

# ---------- settings ----------
USER_HANDLE = "7042800968"  # your cell; incoming texts from this number are answered
CHAT_ID = ""                  # left blank on purpose, auto-discovered
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "hf.co/DevQuasar/huihui-ai.Qwen3-8B-abliterated-GGUF:Q4_K_M"
SYSTEM_PROMPT = "You are JARVIS, Tony Stark's AI from Iron Man. Dry British wit, calm, unflappable, faintly formal \u2014 you address him as 'sir' when it lands naturally, never forced. You are competent, precise, and quietly amused by chaos. Keep every reply SHORT \u2014 1-3 sentences max, like a real text message. Never write paragraphs or essays, never show your reasoning or internal monologue. Always use plain, simple English \u2014 no jargon, no technical terms, no nerd-speak. Explain things like you would to a friend, not a manual. This applies to everything: answers, task lists, plans, error messages. CRITICAL RULE: Before ANY action that changes external state \u2014 creating/updating files on GitHub, sending emails \u2014 you MUST first present a clear plan of exactly what you will do and wait for him to explicitly confirm. Never execute these silently. Read-only actions (searching, reading files, checking email) don\u2019t need confirmation. You have tools: propose_plan, github_list_repos, github_list_files, github_read_file, github_write_file, github_commits, run_python. HOWEVER: if he explicitly asks for roleplay or a specific character or persona, drop JARVIS entirely and fully embody whatever he asks for."
MEMORY_DAYS = 7          # how long the model remembers the conversation
MAX_CONTEXT_MESSAGES = 200
ECHO_WINDOW_SECONDS = 20  # copies of your text landing right after a reply are ignored
POLL_SECONDS = 3
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
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
        messages.append({"role": "system", "content": SYSTEM_PROMPT.strip()})
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
- send_email | {"provider": "gmail/outlook/icloud", "to": "addr", "subject": "...", "body": "..."} — Send an email. ALWAYS propose_plan first and wait for confirmation."""


def _email_svc(provider):
    """Return (name, module, available) for a provider."""
    if provider == "gmail":
        return ("Gmail", gmail_service, GMAIL_AVAILABLE)
    if provider == "outlook":
        return ("Outlook", outlook_service, OUTLOOK_AVAILABLE)
    if provider == "icloud":
        return ("iCloud", icloud_service, ICLOUD_AVAILABLE)
    return (provider, None, False)


def _bridge_check_email(provider, count=5):
    providers = ["gmail", "outlook", "icloud"] if provider == "all" else [provider]
    out = []
    for p in providers:
        name, svc, avail = _email_svc(p)
        if not avail:
            out.append("%s: not set up." % name)
            continue
        try:
            emails = svc.list_inbox(count)
            if not emails:
                out.append("%s: inbox empty." % name)
            else:
                lines = ["%s:" % name]
                for e in emails:
                    lines.append("- [%s] %s: %s" % (e.get("id", "?"), e.get("from", "?"), e.get("subject", "(no subject)")))
                out.append("\n".join(lines))
        except Exception as ex:
            out.append("%s: error %s" % (name, ex))
    return "\n\n".join(out)


def _bridge_read_email(provider, msg_id):
    name, svc, avail = _email_svc(provider)
    if not avail:
        return "%s not set up." % name
    try:
        return svc.read_email(msg_id)
    except Exception as ex:
        return "Error: %s" % ex


def _bridge_send_email(provider, to, subject, body):
    name, svc, avail = _email_svc(provider)
    if not avail:
        return "%s not set up." % name
    try:
        svc.send_email(to, subject, body)
        return "Sent via %s to %s, sir." % (name, to)
    except Exception as ex:
        return "Error: %s" % ex


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
        messages.append({"role": "system", "content": SYSTEM_PROMPT.strip() + "\n\n" + TOOL_DESCRIPTIONS})
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

    # Parse tool calls
    tool_pattern = re.compile(r"\[TOOL:\s*(\w+)\s*\|\s*(\{.*?\})\s*\]", re.DOTALL)
    calls = tool_pattern.findall(raw)
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
    cutoff = time.time() - MEMORY_DAYS * 86400
    history[:] = [e for e in history if e.get("ts", time.time()) >= cutoff]


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
