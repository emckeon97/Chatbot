#!/usr/bin/env python3
"""
iMessage bridge, self-text edition. You text your own Apple ID address like a
contact, and llama 3.1 on this Mac answers in that same chat. No second
account needed; the bridge tells its own replies apart from yours by text.

The conversation is saved to memory.json next to this script, so the model
keeps its memory across restarts.

Run:  python3 imessage_bridge.py chats   # list every chat id and name
      python3 imessage_bridge.py        # start watching
"""

import json
import os
import subprocess
import sys
import time
import urllib.request

# ---------- settings ----------
CHAT_ID = "iMessage;-;elimckeon97@icloud.com"   # Elijah's self-chat (verify with chats)
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"
MODEL = "llama3.1"
SYSTEM_PROMPT = ""       # optional, e.g. "You are NOVA, a dry-witted shipboard AI."
HISTORY_LIMIT = 20       # turns of context kept
POLL_SECONDS = 3
MEMORY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.json")

# ---------- apple bridge ----------

def osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


def list_chats() -> None:
    ids_raw = osascript('tell application "Messages" to get id of every chat')
    names_raw = osascript('tell application "Messages" to get name of every chat')
    ids = [x.strip() for x in ids_raw.split(",") if x.strip()]
    names = [x.strip() for x in names_raw.split(",")]
    for index, chat_id in enumerate(ids):
        name = names[index] if index < len(names) and names[index] else "(no name)"
        print(name, "->", chat_id)


def last_messages() -> list:
    script = (
        'tell application "Messages"\n'
        'set theChat to chat "' + CHAT_ID + '"\n'
        'set out to ""\n'
        'repeat with m in (messages of theChat)\n'
        'set mid to id of m as text\n'
        'set mtext to ""\n'
        'try\n'
        'set mtext to (text of m) as text\n'
        'end try\n'
        'set out to out & mid & "@@" & mtext & "###"\n'
        'end repeat\n'
        'return out\n'
        'end tell'
    )
    raw = osascript(script)
    messages = []
    for entry in [e for e in raw.split("###") if e.strip()]:
        parts = entry.split("@@")
        if len(parts) >= 2:
            messages.append({
                "id": parts[0].strip(),
                "text": "@@".join(parts[1:]),
            })
    return messages


def send(text: str) -> None:
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    osascript(
        'tell application "Messages" to send "' + escaped + '" to chat "' + CHAT_ID + '"'
    )


# ---------- llama ----------

def ask(history: list) -> str:
    messages = []
    if SYSTEM_PROMPT.strip():
        messages.append({"role": "system", "content": SYSTEM_PROMPT.strip()})
    messages.extend(history)
    body = json.dumps({"model": MODEL, "messages": messages, "stream": False}).encode()
    request = urllib.request.Request(
        OLLAMA_URL, data=body, headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        data = json.load(response)
    return data["choices"][0]["message"]["content"].strip()


# ---------- memory ----------

def load_history() -> list:
    if not os.path.exists(MEMORY_FILE):
        return []
    try:
        with open(MEMORY_FILE, "r", encoding="utf-8") as file:
            data = json.load(file)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_history(history: list) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=1)
    except Exception as error:
        print("could not save memory:", error)


# ---------- main loop ----------

def main() -> None:
    if not CHAT_ID.strip():
        print("Set CHAT_ID at the top of this file first.")
        print("Run:  python3 imessage_bridge.py chats")
        sys.exit(1)

    print("Watching chat:", CHAT_ID)
    print("Ctrl-C to stop.")

    # Anything already in the chat before startup stays unanswered.
    seen = {m["id"] for m in last_messages()}
    history = load_history()
    if history:
        print("remembered turns:", len(history) // 2)
    sent_replies: list = []   # the bridge tells its own replies apart by text

    while True:
        try:
            for message in last_messages()[-8:]:
                if message["id"] in seen:
                    continue
                text = message["text"].strip()
                if text in sent_replies:
                    # That was the bridge talking, not you.
                    seen.add(message["id"])
                    continue
                if not text:
                    continue
                seen.add(message["id"])
                history.append({"role": "user", "content": text})
                del history[:-2 * HISTORY_LIMIT]
                try:
                    reply = ask(history)
                except Exception as error:
                    reply = "(model error: " + str(error) + ")"
                if reply:
                    history.append({"role": "assistant", "content": reply})
                    del history[:-2 * HISTORY_LIMIT]
                    save_history(history)
                    sent_replies.append(reply)
                    del sent_replies[:-5]
                    send(reply)
                    print("replied:", reply[:80].replace("\n", " "))
        except Exception as error:
            print("error:", error)
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "chats":
        list_chats()
    else:
        main()
