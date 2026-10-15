# Chatbot

A fast, clean SwiftUI chat app for iOS that streams replies from a **local llama 3.1** server.

## Features
- Streaming token-by-token responses
- Character personas for role-play, with presets included
- Chat history saved between launches
- Configurable server address, model, temperature and system prompt
- Stop button mid-generation, clear-chat, clean dark-friendly UI
- No third-party dependencies

## Build
1. Open `Chatbot.xcodeproj` in Xcode 27 (or 16+).
2. Pick an iOS 26+ simulator or your iPhone, press Run.
3. First run on device: Xcode signs with your personal team automatically.

## Connect your local model
The app speaks the OpenAI-compatible API, so both servers below work:

**Ollama (default)**
```
ollama pull llama3.1
ollama serve        # listens on port 11434
```
In the app, set the server to `http://<your-mac-ip>:11434` when running on a real iPhone on the same Wi-Fi (on the Mac simulator, `http://localhost:11434` just works). Find your Mac IP in System Settings > Wi-Fi > Details.

**llama.cpp**
```
llama-server -m llama3.1.gguf --port 8080
```
Set the server to `http://<your-mac-ip>:8080`.

Model name must match what the server knows (`llama3.1`, `llama3.1:8b-instruct-q4_K_M`, etc.).

## Characters
Settings > Characters lets you save personas, each with its own personality prompt. Tap the checkmark to make one active and the model stays in that role. Three starter characters ship in the box; edit or delete them freely.

## Notes
- Local HTTP is allowed via the Info.plist local-networking exception, so no server changes needed.
- Set a system prompt in Settings to shape the assistant's behavior when no character is active.
