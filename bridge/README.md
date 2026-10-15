# iMessage bridge, self-text edition

You text your own Apple ID address like a contact, and llama 3.1 on your Mac answers in that same chat. No second Apple ID needed.

## Setup

1. On your iPhone, open Messages and text your own Apple ID address once (your iMessage email, like `you@icloud.com`, or the phone number tied to iMessage). The chat syncs to the Mac. Save it as a contact with whatever name you like, say Llama.
2. In Terminal, from the `bridge` folder:
   ```
   python3 imessage_bridge.py chats
   ```
   macOS asks to let Terminal control Messages the first time. Allow it, then copy the chat id for that self-chat, like `iMessage;-;you@icloud.com`.
3. Paste the chat id into `CHAT_ID` at the top of `imessage_bridge.py`, and set `SYSTEM_PROMPT` if you want a persona.

## Run it

Make sure Ollama is running with the model pulled (`ollama serve`, `ollama pull llama3.1`), then:

```
python3 imessage_bridge.py
```

Keep the Mac on and awake. Every text you send that contact gets a llama reply in the same chat; Ctrl-C stops it.

## Notes
- The bridge tells its own replies apart from your texts by matching the text, so avoid quoting one of the bot's replies word for word if you want an answer to it.
- The model sees the last 20 turns as context.
- If replies ever stop, re-run `chats` to check the chat id, and confirm Ollama is still running.
