import Foundation

final class LlamaService {
    private enum Defaults {
        static let serverKey = "serverURL"
        static let modelKey = "modelName"
        static let temperatureKey = "temperature"
        static let systemKey = "systemPrompt"
        static let server = "http://192.168.0.102:11434"
        static let tailscaleKey = "tailscaleServer"
        static let model = "dolphin-llama3:8b"
        static let temperature = 0.7
    }

    /// Fetches installed model names from Ollama's /api/tags endpoint.
    func fetchModels() async throws -> [String] {
        let servers = Self.serverURLs()
        guard !servers.isEmpty else { throw URLError(.badURL) }
        var lastError: Error = URLError(.cannotConnectToHost)
        for base in servers {
            guard let url = URL(string: "api/tags", relativeTo: base) else { continue }
            do {
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
            } catch {
                lastError = error
                continue
            }
        }
        throw lastError
    }

    /// Streams tokens from an OpenAI-compatible local server (Ollama or llama.cpp).
    /// Resolve server URLs to try: primary first, then Tailscale fallback.
    static func serverURLs() -> [URL] {
        let settings = UserDefaults.standard
        var urls: [URL] = []
        let primary = (settings.string(forKey: Defaults.serverKey) ?? Defaults.server)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if let url = Self.normalizedURL(primary) { urls.append(url) }
        let tailscale = (settings.string(forKey: Defaults.tailscaleKey) ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !tailscale.isEmpty, let url = Self.normalizedURL(tailscale), !urls.contains(url) {
            urls.append(url)
        }
        return urls
    }

    private static func normalizedURL(_ server: String) -> URL? {
        var s = server.trimmingCharacters(in: .whitespacesAndNewlines)
        if !s.hasPrefix("http://") && !s.hasPrefix("https://") { s = "http://" + s }
        while s.hasSuffix("/") { s.removeLast() }
        return URL(string: s)
    }

    func stream(messages: [ChatMessage]) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                let settings = UserDefaults.standard
                let servers = Self.serverURLs()
                guard !servers.isEmpty else {
                    continuation.finish(throwing: URLError(.badURL))
                    return
                }

                // Build payload once
                var payload: [[String: String]] = []
                let personaPrompt = PersonaStore.activePromptOnDisk()
                var systemPrompt = personaPrompt
                    ?? (settings.string(forKey: Defaults.systemKey) ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
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

                // Think-tag filter state (handles tags split across chunks)
                var inThink = false
                var thinkBuffer = ""

                // Try each server in order
                var lastError: Error = URLError(.cannotConnectToHost)
                for base in servers {
                    guard let url = URL(string: "v1/chat/completions", relativeTo: base) else { continue }
                    do {
                        var request = URLRequest(url: url.absoluteURL)
                        request.httpMethod = "POST"
                        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                        request.httpBody = try JSONSerialization.data(withJSONObject: body)
                        request.timeoutInterval = 15

                        let (bytes, response) = try await URLSession.shared.bytes(for: request)
                        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                            throw URLError(.badServerResponse)
                        }

                        for try await line in bytes.lines {
                            let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                            guard trimmed.hasPrefix("data: ") else { continue }
                            let jsonStr = String(trimmed.dropFirst(6))
                            if jsonStr == "[DONE]" { break }
                            guard let data = jsonStr.data(using: .utf8),
                                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                                  let choices = json["choices"] as? [[String: Any]],
                                  let delta = choices.first?["delta"] as? [String: Any],
                                  let content = delta["content"] as? String,
                                  !content.isEmpty else { continue }
                            // Strip <think>...</think> blocks (may span chunks)
                            thinkBuffer += content
                            var out = ""
                            while !thinkBuffer.isEmpty {
                                if inThink {
                                    if let end = thinkBuffer.range(of: "</think>") {
                                        thinkBuffer = String(thinkBuffer[end.upperBound...])
                                        inThink = false
                                    } else {
                                        thinkBuffer = ""
                                    }
                                } else if let start = thinkBuffer.range(of: "<think>") {
                                    out += thinkBuffer[..<start.lowerBound]
                                    thinkBuffer = String(thinkBuffer[start.upperBound...])
                                    inThink = true
                                } else {
                                    // No complete tag yet; hold back a tail in case "<think" is split
                                    let hold = min(thinkBuffer.count, 7)
                                    let splitAt = thinkBuffer.index(thinkBuffer.endIndex, offsetBy: -hold)
                                    out += thinkBuffer[..<splitAt]
                                    thinkBuffer = String(thinkBuffer[splitAt...])
                                    break
                                }
                            }
                            if !out.isEmpty {
                                continuation.yield(out)
                            }
                        }
                        // Flush leftover non-think text
                        if !inThink && !thinkBuffer.isEmpty {
                            continuation.yield(thinkBuffer)
                        }
                        continuation.finish()
                        return  // Success
                    } catch {
                        lastError = error
                        continue  // Try next server
                    }
                }
                continuation.finish(throwing: lastError)
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }
}
