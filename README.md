# Chatbot

A SwiftUI chat app for iPhone that talks to a local llama 3.1 server (Ollama or llama.cpp) over the OpenAI-compatible chat completions API. Multiple conversations, role-play personas, temperature control. Also runs on the Mac through Mac Catalyst.

## Build

1. Clone the repo and open `Chatbot.xcodeproj` in Xcode.
2. Pick an iPhone simulator and run. The deployment target is iOS 26.

## Run it on the Mac

The same target supports Mac Catalyst. In Xcode, choose **My Mac (Catalyst)** in the run destination next to the scheme, then run. The Mac app talks to `localhost:11434` directly, so just have `ollama serve` running on the Mac.

## Point it at your model server

In Settings inside the app:
- **Server URL**, default `http://localhost:11434` (Ollama). For llama.cpp use its `--host` address, the API path is the same OpenAI-compatible `/v1/chat/completions`.
- On a real device, use your Mac's LAN address, and start Ollama with `OLLAMA_HOST=0.0.0.0 ollama serve` so it listens on the network.
- **Model**, default `llama3.1`.
- **Temperature**, how adventurous the model gets; around 0.8 for role-play, 0.3 for factual answers.

## iMessage bridge

The `bridge` folder has a script that texts the model from your own Apple ID over iMessage. See `bridge/README.md`.

## Personas

Three presets ship in the app (Noir Detective, Starship AI, Grumpy Goblin) and you can add your own; a persona injects a system prompt and remembers its own temperature.
