# iMessage Bridge

A Python bridge that answers iMessages using a local Ollama model. Runs on your Mac under a second Apple ID — text it from your phone and get JARVIS-style replies.

## What it does

- **iMessage integration**: Reads from `~/Library/Messages/chat.db`, sends via AppleScript. Responds to a specific contact/phone number.
- **JARVIS persona**: Dry wit, 1-3 sentence replies, plain English. `<think>` tags stripped.
- **Tool calling**: The model emits `[TOOL: name | {args}]`, the bridge executes and feeds results back.
- **Permanent memory**: Conversation history kept forever (5000 msg cap). Long-term facts via `remember_fact` / `recall_facts` / `forget_fact`.
- **Tapback awareness**: Reads iMessage reactions for context (can't send them — no API).

## Tools

| Category | Tools |
|----------|-------|
| Email | `check_email`, `read_email`, `send_email`, `list_email_accounts` (Gmail, Outlook, iCloud) |
| Calendar | `add_calendar_event`, `list_calendar_events` (macOS Calendar.app) |
| Reminders | `add_reminder`, `list_reminders` |
| Contacts | `lookup_contact` |
| Info | `web_search`, `get_weather`, `get_news`, `get_stock`, `get_crypto`, `define_word`, `get_briefing` |
| Mac control | `system_status`, `music_control`, `set_volume`, `lock_mac`, `sleep_display`, `open_app`, `take_screenshot`, `get_clipboard` |
| Macros | `save_macro`, `run_macro`, `list_macros`, `delete_macro` |
| Scheduling | `schedule_text`, `list_scheduled`, `cancel_scheduled` |
| GitHub | `github_list_repos`, `github_list_files`, `github_read_file`, `github_write_file`, `github_commits` |
| Other | `run_python`, `propose_plan`, `delegate_task` |

## Setup

### 1. Prerequisites

- macOS with a second user account (e.g. `ai`) for the bridge
- Python 3.9+
- Ollama running with `OLLAMA_HOST=0.0.0.0 ollama serve`
- Tailscale (optional, for remote access)

### 2. Permissions

The bridge needs:
- **Full Disk Access** for Terminal/Python: System Settings → Privacy & Security → Full Disk Access
- **Automation** permission for Messages and Calendar when first run

### 3. Configure

Edit the top of `imessage_bridge.py`:
- `USER_HANDLE`: The phone number to respond to (e.g. `+17045551234`)
- `MODEL`: Ollama model tag
- `OLLAMA_URL`: Usually `http://localhost:11434`

### 4. Email setup

**Gmail** (IMAP app password):
1. Go to https://myaccount.google.com/apppasswords
2. Generate an app password
3. Save to `~/.chatbot/gmail_imap.json`:
   ```json
   {"email": "you@gmail.com", "password": "xxxx xxxx xxxx xxxx"}
   ```
4. `chmod 600 ~/.chatbot/gmail_imap.json`

**Outlook** (OAuth — Microsoft disabled IMAP basic auth):
1. Register an app at https://portal.azure.com → App registrations
2. Add delegated permissions: `Mail.Read`, `Mail.Send`
3. Add redirect URI: `http://localhost` (Mobile and desktop applications)
4. Copy the Application (client) ID into `outlook_setup.py` and `outlook_service.py`
5. Run: `pip install msal && python3 outlook_setup.py you@outlook.com`
6. Repeat for each Outlook account

**iCloud** (app-specific password):
1. Go to https://appleid.apple.com → Sign-In and Security → App-Specific Passwords
2. Save to `~/.chatbot/icloud.json`:
   ```json
   {"email": "you@icloud.com", "password": "xxxx-xxxx-xxxx-xxxx"}
   ```

### 5. Calendar setup (optional)

To add events to your main user's Calendar (not the bridge user's):
```bash
sudo visudo -f /etc/sudoers.d/chatbot-calendar
# Add: bridgeuser ALL=(mainuser) NOPASSWD: /usr/bin/osascript
sudo chmod 440 /etc/sudoers.d/chatbot-calendar
```

### 6. GitHub setup (optional)

1. Generate a PAT at https://github.com/settings/tokens (repo scope)
2. Save to `~/.chatbot/github_token.txt`
3. `chmod 600 ~/.chatbot/github_token.txt`

## Run it

```bash
python3 imessage_bridge.py
```

### Keep it running (LaunchAgent)

```bash
# Write the plist
python3 -c "
import plistlib
plist = {
    'Label': 'com.chatbot.bridge',
    'ProgramArguments': ['/usr/bin/python3', '/path/to/bridge/imessage_bridge.py'],
    'RunAtLoad': True,
    'KeepAlive': True,
    'StandardOutPath': '/Users/ai/.chatbot/bridge.log',
    'StandardErrorPath': '/Users/ai/.chatbot/bridge.err',
}
with open('/Users/ai/Library/LaunchAgents/com.chatbot.bridge.plist', 'wb') as f:
    plistlib.dump(plist, f)
"
# Load it
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.chatbot.bridge.plist
```

**Note**: macOS suspends background user sessions on fast user switching. The bridge only responds while its user session is active.

## Memory

- `memory.json`: Conversation history (permanent, 5000 message cap, last 200 sent to model)
- `longterm_memory.json`: Facts saved via `remember_fact`
- Delete either to wipe.

## Task delegation

The bridge can delegate browser tasks via `delegate_task`. These queue to `delegated_tasks.json` and are picked up via HTTP:
- `GET :11435/pending_tasks` — Claim pending tasks
- `POST :11435/task_result` — Submit results (bridge texts the user)
