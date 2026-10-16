import Foundation

final class LlamaService {
    private enum Defaults {
        static let serverKey = "serverURL"
        static let modelKey = "modelName"
        static let temperatureKey = "temperature"
        static let systemKey = "systemPrompt"
        static let server = "http://192.168.0.102:11434"
        static let model = "dolphin-llama3:8b"
        static let temperature = 0.7
    }

    /// Fetches installed model names from Ollama's /api/tags endpoint.
    func fetchModels() async throws -> [String] {
        let settings = UserDefaults.standard
        var server = (settings.string(forKey: Defaults.serverKey) ?? Defaults.server)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
            server = "http://" + server
        }
        while server.hasSuffix("/") { server.removeLast() }
        guard let base = URL(string: server),
              let url = URL(string: "api/tags", relativeTo: base) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url.absoluteURL)
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw URLError(.badServerResponse)
        }
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let models = json["models"] as? [[String: Any]] else {
            return []
        }
        return models.compactMap { $0["name"] as? String }.sorted()
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
                    let personaPrompt = PersonaStore.activePromptOnDisk()
                    var systemPrompt = personaPrompt
                        ?? (settings.string(forKey: Defaults.systemKey) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    // Inject long-term memory
                    let memoryContext = await MemoryService.shared.promptContext()
                    if !memoryContext.isEmpty {
                        systemPrompt += memoryContext
                    }
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
