# iMessage bridge

Texts sent to one iMessage chat on your Mac get answered by llama 3.1 on Ollama, so you can text the model like a contact.

## One-time setup

1. Create a free second Apple ID (for example `llamabot@icloud.com`) at appleid.apple.com. The bridge replies from that account, so replies land on your iPhone as a separate contact instead of mixing with your own texts.
2. On the Mac, open Messages > Settings > iMessage, click the + and sign in with that second Apple ID (keep your main one signed in too).
3. On your iPhone, text that address once so the chat exists.
4. In Terminal, from the `bridge` folder:
   ```
   python3 imessage_bridge.py chats
   ```
   macOS asks to let Terminal control Messages the first time. Allow it, then copy the chat id for that conversation.
5. Paste the chat id into `CHAT_ID` at the top of `imessage_bridge.py`, and set `SYSTEM_PROMPT` if you want a persona.

## Run it

```
python3 imessage_bridge.py
```

Keep the Mac on and awake. Every text you send the contact gets a llama reply; Ctrl-C stops it.

## Notes
- Ollama must be running (`ollama serve`) with the model pulled.
- The model sees the last 20 turns as context; delete or edit `personas` in the script for longer memory.
- If replies ever stop, re-run `chats` to check the chat id has not changed.
