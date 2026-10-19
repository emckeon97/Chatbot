#!/usr/bin/env python3
"""Route the iPhone app, Siri Shortcuts, and iMessage through one assistant context.
Run after pulling the repo: python3 bridge/enable_unified_assistant.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BRIDGE = HERE / "imessage_bridge.py"
INTENTS = ROOT / "Chatbot" / "ChatbotIntents.swift"
STORE = ROOT / "Chatbot" / "ChatStore.swift"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

BRIDGE_SUPPORT = r'''
# ---------- unified assistant endpoint ----------
ASSISTANT_LOCK = Lock()

def run_unified_assistant(text):
    text = str(text or "").strip()
    if not text:
        raise ValueError("empty message")
    with ASSISTANT_LOCK:
        history = load_history()
        prune_history(history)
        history.append({"role": "user", "content": text, "ts": time.time()})
        email_reply = try_email_command(text)
        if email_reply is not None:
            reply = email_reply
        else:
            reply = ask_with_tools(history)
        if reply:
            history.append({"role": "assistant", "content": reply, "ts": time.time()})
        prune_history(history)
        save_history(history)
        return reply or "I couldn't produce a reply."
'''

ASSISTANT_ENDPOINT = r'''        if self.path == "/assistant":
            length = int(self.headers.get("Content-Length", 0))
            try:
                data = json.loads(self.rfile.read(length))
                prompt = data.get("prompt", data.get("message", ""))
                reply = run_unified_assistant(prompt)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True, "reply": reply}).encode())
            except Exception as e:
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": False, "error": str(e)}).encode())
        elif self.path == "/schedule_text":
'''

INTENTS_SOURCE = r'''import AppIntents
import Foundation

/// One network route for the phone app and Siri Shortcuts. The Mac bridge keeps
/// the shared history, persona, memory, and tool handling used by iMessage.
enum UnifiedAssistantError: LocalizedError {
    case unavailable
    case requestFailed

    var errorDescription: String? {
        switch self {
        case .unavailable: return "Could not reach the assistant bridge."
        case .requestFailed: return "The assistant request failed."
        }
    }
}

final class UnifiedAssistantService {
    static let shared = UnifiedAssistantService()

    private init() {}

    var isEnabled: Bool {
        UserDefaults.standard.object(forKey: "useUnifiedAssistant") as? Bool ?? true
    }

    private var endpoint: URL? {
        let settings = UserDefaults.standard
        var server = (settings.string(forKey: "serverURL") ?? "http://192.168.0.102:11434")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
            server = "http://" + server
        }
        guard let base = URL(string: server), var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
            return nil
        }
        parts.port = 11435
        parts.path = "/assistant"
        parts.query = nil
        return parts.url
    }

    func ask(_ prompt: String) async throws -> String {
        guard let endpoint else { throw UnifiedAssistantError.unavailable }
        let body = try JSONSerialization.data(withJSONObject: ["prompt": prompt])
        var request = URLRequest(url: endpoint)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        request.timeoutInterval = 180
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let reply = json["reply"] as? String, !reply.isEmpty else {
            throw UnifiedAssistantError.requestFailed
        }
        return reply
    }
}

/// Siri Shortcuts uses the same bridge as the app and iMessage, not a blank
/// standalone model request.
struct AskChatbotIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Chatbot"
    static var description = IntentDescription("Send a question to your personal assistant.")

    @Parameter(title: "Question", requestValueDialog: "What do you want to ask?")
    var question: String

    static var parameterSummary: some ParameterSummary {
        Summary("Ask \(\.$question)")
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let reply = try await UnifiedAssistantService.shared.ask(question)
        return .result(dialog: IntentDialog(stringLiteral: reply))
    }
}

struct ChatbotShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(
            intent: AskChatbotIntent(),
            phrases: [
                "Ask \(.applicationName)",
                "Ask my \(.applicationName)",
                "Talk to \(.applicationName)"
            ],
            shortTitle: "Ask Chatbot",
            systemImageName: "message.fill"
        )
    }
}
'''

STORE_OLD = r'''        streamingText = ""
        isGenerating = true
        generatingIn = targetID
        let history = convo.messages
        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in self.service.streamWithTools(messages: history) {
                    self.streamingText += chunk
                }
                self.completeStream(into: targetID)
            } catch is CancellationError {
                self.completeStream(into: targetID)
            } catch {
                self.completeStream(into: targetID)
                self.appendTo(targetID, ChatMessage(role: .assistant, content: "Error: \(error.localizedDescription)"))
            }
            self.isGenerating = false
            self.generatingIn = nil
            self.save()
        }
        return targetID
'''

STORE_NEW = r'''        let history = convo.messages
        if UnifiedAssistantService.shared.isEnabled {
            streamingText = ""
            isGenerating = true
            generatingIn = targetID
            streamTask = Task { [weak self] in
                guard let self else { return }
                do {
                    let reply = try await UnifiedAssistantService.shared.ask(trimmed)
                    self.appendTo(targetID, ChatMessage(role: .assistant, content: reply))
                    self.isGenerating = false
                    self.generatingIn = nil
                    self.save()
                } catch {
                    self.startLocalStream(history: history, targetID: targetID)
                }
            }
        } else {
            startLocalStream(history: history, targetID: targetID)
        }
        return targetID
'''

STORE_HELPER = r'''
    private func startLocalStream(history: [ChatMessage], targetID: UUID) {
        streamingText = ""
        isGenerating = true
        generatingIn = targetID
        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in self.service.streamWithTools(messages: history) {
                    self.streamingText += chunk
                }
                self.completeStream(into: targetID)
            } catch is CancellationError {
                self.completeStream(into: targetID)
            } catch {
                self.completeStream(into: targetID)
                self.appendTo(targetID, ChatMessage(role: .assistant, content: "Error: \(error.localizedDescription)"))
            }
            self.isGenerating = false
            self.generatingIn = nil
            self.save()
        }
    }

'''

def patch_bridge(source):
    changes = []
    from enable_control_center import patch as apply_control_center
    source, inherited_changes = apply_control_center(source)
    changes.extend(inherited_changes)
    if "from threading import Thread, Lock" not in source:
        if "from threading import Thread" not in source:
            raise RuntimeError("could not find thread import")
        source = source.replace("from threading import Thread", "from threading import Thread, Lock", 1)
        changes.append("bridge request lock added")
    if "def run_unified_assistant" not in source:
        marker = "\n# ---------- delegated tasks (picked up by main Jarvis via Tailscale)"
        if marker not in source:
            raise RuntimeError("could not find bridge insertion point")
        source = source.replace(marker, "\n" + BRIDGE_SUPPORT + marker, 1)
        changes.append("shared assistant endpoint added")
    if 'if self.path == "/assistant":' not in source:
        marker = '        if self.path == "/schedule_text":\n'
        if marker not in source:
            raise RuntimeError("could not find HTTP handler")
        source = source.replace(marker, ASSISTANT_ENDPOINT, 1)
        changes.append("phone and shortcut requests routed through shared context")
    return source, changes

def patch_store(source):
    if "private func startLocalStream" in source:
        return source, []
    if STORE_OLD not in source:
        raise RuntimeError("could not find app chat request block")
    source = source.replace(STORE_OLD, STORE_NEW, 1)
    marker = "    func stop() {\n"
    if marker not in source:
        raise RuntimeError("could not find chat helper insertion point")
    source = source.replace(marker, STORE_HELPER + marker, 1)
    return source, ["phone app now uses shared context with local fallback"]

def restart_bridge():
    if not PLIST.exists():
        return False
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, str(PLIST)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not restart the bridge")
    return True

def main():
    if not BRIDGE.exists() or not INTENTS.exists() or not STORE.exists():
        raise RuntimeError("run this from the pulled Chatbot repository")
    bridge_source, changes = patch_bridge(BRIDGE.read_text(encoding="utf-8"))
    store_source, store_changes = patch_store(STORE.read_text(encoding="utf-8"))
    changes.extend(store_changes)
    if INTENTS.read_text(encoding="utf-8") != INTENTS_SOURCE:
        INTENTS.write_text(INTENTS_SOURCE, encoding="utf-8")
        changes.append("Siri Shortcuts now use shared context")
    if changes:
        BRIDGE.write_text(bridge_source, encoding="utf-8")
        STORE.write_text(store_source, encoding="utf-8")
        import py_compile
        py_compile.compile(str(BRIDGE), doraise=True)
    restarted = restart_bridge()
    print("Unified assistant upgrade is ready." if changes else "Unified assistant upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")
    print("Open the project in Xcode and build once to install the updated phone app.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Unified assistant upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
