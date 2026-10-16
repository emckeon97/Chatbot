import Foundation
import UIKit

/// Vision support: send images to a vision-capable Ollama model.
/// Requires a vision model like moondream, llava, or bakllava.
/// Pull one with: ollama pull moondream
final class VisionService {
    static let shared = VisionService()

    private init() {}

    /// Check if the current model supports vision by trying a minimal request.
    /// Vision models: moondream, llava, bakllava, llama3.2-vision
    static let visionModels = ["moondream", "llava", "bakllava", "llama3.2-vision"]

    /// Send an image with a prompt to the vision model.
    /// Default vision model (pull with: ollama pull moondream)
    static let defaultVisionModel = "moondream"

    func describe(image: UIImage, prompt: String, model: String = defaultVisionModel) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let settings = UserDefaults.standard
                    var server = (settings.string(forKey: "serverURL") ?? "http://192.168.0.102:11434")
                        .trimmingCharacters(in: .whitespacesAndNewlines)
                    if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
                        server = "http://" + server
                    }
                    while server.hasSuffix("/") { server.removeLast() }
                    guard let base = URL(string: server),
                          let url = URL(string: "api/chat", relativeTo: base) else {
                        throw URLError(.badURL)
                    }

                    // Compress image
                    guard let jpegData = image.jpegData(compressionQuality: 0.7) else {
                        throw VisionError.imageEncodingFailed
                    }
                    let base64 = jpegData.base64EncodedString()

                    let payload: [String: Any] = [
                        "model": model,
                        "stream": true,
                        "messages": [
                            ["role": "user", "content": prompt, "images": [base64]]
                        ]
                    ]
                    let body = try JSONSerialization.data(withJSONObject: payload)
                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.httpBody = body
                    request.timeoutInterval = 120

                    let (bytes, response) = try await URLSession.shared.bytes(for: request)
                    guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
                        throw URLError(.badServerResponse)
                    }

                    for try await line in bytes.lines {
                        let trimmed = line.trimmingCharacters(in: .whitespacesAndNewlines)
                        guard !trimmed.isEmpty,
                              let data = trimmed.data(using: .utf8),
                              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                              let message = json["message"] as? [String: Any],
                              let content = message["content"] as? String,
                              !content.isEmpty else { continue }
                        continuation.yield(content)
                        if (json["done"] as? Bool) == true { break }
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

enum VisionError: Error, LocalizedError {
    case imageEncodingFailed
    var errorDescription: String? {
        "Could not encode image."
    }
}
