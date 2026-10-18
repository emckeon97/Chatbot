# Chatbot

A SwiftUI chat app for iPhone that talks to a local Ollama server. JARVIS-style AI assistant with tool calling, GitHub integration, voice calls, and multiple personas. Also runs on Mac via Mac Catalyst.

## Features

- **Local AI**: Connects to Ollama on your Mac (LAN or Tailscale). No cloud, no API keys.
- **Tool calling**: Weather, calendar, stocks, crypto, news, dictionary, web search, JavaScript execution, Gmail, and daily briefings — the model decides when to use them.
- **GitHub integration**: Browse repos, read/write files, view commits — all from chat. PAT stored in Keychain.
- **Personas**: Multiple built-in personas (JARVIS, Noir Detective, etc.) with per-persona temperature. Tap to switch, pencil icon to edit.
- **Voice calls**: Phone-call-style voice interface with speech recognition.
- **Chat management**: Multiple conversations, regenerate responses, export chats.
- **Think-tag filtering**: Strips `<think>` reasoning blocks from the model stream automatically.

## Build

1. Clone the repo and open `Chatbot.xcodeproj` in Xcode.
2. **Add the GoogleSignIn package**: File → Add Package Dependencies → `https://github.com/google/GoogleSignIn-iOS`
3. Pick an iPhone simulator and run. Deployment target is iOS 26.

## Run it on the Mac

The same target supports Mac Catalyst. In Xcode, choose **My Mac (Catalyst)**, then run. The Mac app talks to `localhost:11434` directly.

## Point it at your model server

In Settings inside the app:
- **Server URL**: Primary server address.
  - On the same WiFi: your Mac's LAN IP (find with `ipconfig getifaddr en0` on the Mac), e.g. `http://192.168.1.5:11434`
  - Over Tailscale: your Mac's Tailscale IP, e.g. `http://100.x.x.x:11434`
  - The app tries the primary first, then falls back to the Tailscale address.
- **Model**: Default is the Qwen3-8B abliterated model. Use any Ollama model.
- **Temperature**: 0.7 recommended for reliable tool calling.

**Important**: Start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve` so it listens on the network, not just localhost.

## iMessage Bridge

The `bridge/` folder has a Python script that answers iMessages from a second Apple ID using the same local model. See `bridge/README.md` for setup.

## Personas

Multiple presets ship in the app. Each persona injects a system prompt and remembers its own temperature. Tap a persona to switch; tap the pencil icon to edit.
