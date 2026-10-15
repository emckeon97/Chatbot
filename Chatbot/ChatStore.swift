import Foundation
import SwiftUI

@MainActor
final class ChatStore: ObservableObject {
    @Published private(set) var messages: [ChatMessage] = []
    @Published private(set) var isGenerating = false
    @Published private(set) var streamingText = ""

    private var streamTask: Task<Void, Never>?
    private let service = LlamaService()
    private static let archive = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("chat_history.json")

    init() {
        load()
    }

    func send(_ text: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isGenerating else { return }
        messages.append(ChatMessage(role: .user, content: trimmed))
        streamingText = ""
        isGenerating = true
        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in self.service.stream(messages: self.messages) {
                    self.streamingText += chunk
                }
                self.completeStream()
            } catch is CancellationError {
                self.completeStream()
            } catch {
                self.completeStream()
                self.messages.append(ChatMessage(role: .assistant, content: "Error: \(error.localizedDescription)"))
            }
            self.isGenerating = false
            self.save()
        }
    }

    func stop() {
        streamTask?.cancel()
    }

    func clear() {
        streamTask?.cancel()
        messages.removeAll()
        streamingText = ""
        isGenerating = false
        save()
    }

    private func completeStream() {
        if !streamingText.isEmpty {
            messages.append(ChatMessage(role: .assistant, content: streamingText))
        }
        streamingText = ""
    }

    private func load() {
        guard let data = try? Data(contentsOf: Self.archive),
              let decoded = try? JSONDecoder().decode([ChatMessage].self, from: data) else { return }
        messages = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(messages) else { return }
        try? data.write(to: Self.archive, options: .atomic)
    }
}
