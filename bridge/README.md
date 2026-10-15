# iMessage bridge, self-text edition

You text your own Apple ID address like a contact, and llama 3.1 on your Mac answers in that same chat. No second Apple ID needed.

## Setup

1. On your iPhone, open Messages and text your own Apple ID address once (your iMessage email, like `you@icloud.com`, or the phone number tied to iMessage). The chat syncs to the Mac. Save it as a contact with whatever name you like, say Llama.
2. In Terminal, from the `bridge` folder:
   ```
   python3 imessage_bridge.py chats
   ```
   macOS asks to let Terminal control Messages the first time. Allow it.
3. `CHAT_ID` is auto-discovered from your address; set `SYSTEM_PROMPT` if you want a persona.
4. Reading goes through the Messages database, so give Terminal Full Disk Access: System Settings > Privacy & Security > Full Disk Access, add Terminal, then quit and reopen Terminal.

## Run it

Make sure Ollama is running with the model pulled (`ollama serve`, `ollama pull llama3.1`), then:

```
python3 imessage_bridge.py
```

Keep the Mac on and awake. Every text you send that contact gets a llama reply in the same chat; Ctrl-C stops it.

## Memory

The conversation is saved to `memory.json` next to the script and reloads on startup. Entries older than seven days drop off, so the model remembers the past week. Delete `memory.json` to wipe its memory and start fresh.

## Notes
- The bridge only ever sends model replies; it never echoes your own texts back, and copies of your text landing right after a reply are ignored.
- The bridge tells its own replies apart from your texts by matching the text, so avoid quoting one of the bot's replies word for word if you want an answer to it.
- The model sees up to the last 200 messages within the seven-day window.
- If replies ever stop, re-run `chats` to check the chat id, and confirm Ollama is still running.
