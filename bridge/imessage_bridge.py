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
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.request

# ---------- settings ----------
USER_HANDLE = "7042800968"  # your cell; incoming texts from this number are answered
CHAT_ID = ""                  # left blank on purpose, auto-discovered
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "dolphin-llama3:8b"
SYSTEM_PROMPT = "You are a flirty, playful chatbot. Stay in character and be natural and engaging."
MEMORY_DAYS = 7          # how long the model remembers the conversation
MAX_CONTEXT_MESSAGES = 200
ECHO_WINDOW_SECONDS = 20  # copies of your text landing right after a reply are ignored
POLL_SECONDS = 3
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")
MESSAGE_DB = os.path.expanduser("~/Library/Messages/chat.db")

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
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False}).encode()
    request = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"].strip()


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
                try:
                    reply = ask(history)
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
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chats":
        list_chats()
    else:
        main()
