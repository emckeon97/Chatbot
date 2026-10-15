import Foundation

final class LlamaService {
    private enum Defaults {
        static let serverKey = "serverURL"
        static let modelKey = "modelName"
        static let temperatureKey = "temperature"
        static let systemKey = "systemPrompt"
        static let server = "http://localhost:11434"
        static let model = "llama3.1"
        static let temperature = 0.7
    }

    /// Streams tokens from an OpenAI-compatible local server (Ollama or llama.cpp).
    func stream(messages: [ChatMessage]) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let settings = UserDefaults.standard
                    var server = (settings.string(forKey: Defaults.serverKey) ?? Defaults.server)
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
                        server = "http://" + server
                    }
                    while server.hasSuffix("/") { server.removeLast() }
                    guard let base = URL(string: server),
                          let url = URL(string: "v1/chat/completions", relativeTo: base) else {
                        throw URLError(.badURL)
                    }

                    var payload: [[String: String]] = []
                    let systemPrompt = (settings.string(forKey: Defaults.systemKey) ?? "")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !systemPrompt.isEmpty {
                        payload.append(["role": "system", "content": systemPrompt])
                    }
                    for message in messages {
                        payload.append(["role": message.role.rawValue, "content": message.content])
                    }

                    let body: [String: Any] = [
                        "model": settings.string(forKey: Defaults.modelKey) ?? Defaults.model,
                        "messages": payload,
                        "stream": true,
                        "temperature": (settings.object(forKey: Defaults.temperatureKey) as? Double) ?? Defaults.temperature,
                    ]

                    var request = URLRequest(url: url.absoluteURL)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.timeoutInterval = 120
                    request.httpBody = try JSONSerialization.data(withJSONObject: body)

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
                        throw URLError(.badServerResponse)
                    }

                    for try await line in bytes.lines {
                        guard line.hasPrefix("data:") else { continue }
                        let chunk = line.dropFirst(5).trimmingCharacters(in: .whitespaces)
                        if chunk == "[DONE]" { break }
                        guard let data = chunk.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              let choices = json["choices"] as? [[String: Any]],
                              let delta = choices.first?["delta"] as? [String: Any],
                              let content = delta["content"] as? String,
                              !content.isEmpty else { continue }
                        continuation.yield(content)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
