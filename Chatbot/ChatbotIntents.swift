import AppIntents
import Foundation

/// Siri Shortcuts integration for Chatbot.
/// Enables "Ask Chatbot" in the Shortcuts app.
struct AskChatbotIntent: AppIntent {
    static var title: LocalizedStringResource = "Ask Chatbot"
    static var description = IntentDescription("Send a question to your local AI chatbot.")

    @Parameter(title: "Question", requestValueDialog: "What do you want to ask?")
    var question: String

    static var parameterSummary: some ParameterSummary {
        Summary("Ask \(\.$question)")
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let settings = UserDefaults.standard
        var server = (settings.string(forKey: "serverURL") ?? "http://192.168.0.102:11434")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
            server = "http://" + server
        }
        while server.hasSuffix("/") { server.removeLast() }
        guard let base = URL(string: server),
              let url = URL(string: "v1/chat/completions", relativeTo: base) else {
            throw IntentError.serverUnreachable
        }

        let model = settings.string(forKey: "modelName") ?? "dolphin-llama3:8b"
        let payload: [String: Any] = [
            "model": model,
            "stream": false,
            "messages": [
                ["role": "user", "content": question]
            ]
        ]

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        request.timeoutInterval = 120

        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let choices = json["choices"] as? [[String: Any]],
              let message = choices.first?["message"] as? [String: Any],
              let content = message["content"] as? String else {
            throw IntentError.requestFailed
        }

        return .result(dialog: IntentDialog(stringLiteral: content))
    }
}

enum IntentError: Error, LocalizedError {
    case serverUnreachable, requestFailed
    var errorDescription: String? {
        switch self {
        case .serverUnreachable: return "Could not reach the AI server."
        case .requestFailed: return "The AI request failed."
        }
    }
}

/// App Shortcut for quick access
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
