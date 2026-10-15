import Foundation
import SwiftUI

@MainActor
final class ChatStore: ObservableObject {
    static let shared = ChatStore()

    @Published private(set) var conversations: [Conversation] = []
    @Published private(set) var isGenerating = false
    @Published private(set) var generatingIn: UUID?
    @Published private(set) var streamingText = ""

    private var streamTask: Task<Void, Never>?
    private let service = LlamaService()
    private static let archive = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("conversations.json")

    init() {
        load()
    }

    func conversation(id: UUID?) -> Conversation? {
        guard let id else { return nil }
        return conversations.first(where: { $0.id == id })
    }

    @discardableResult
    func send(_ text: String, in conversationID: UUID?) -> UUID? {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !isGenerating else { return conversationID }

        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: trimmed))
        if convo.title.isEmpty {
            convo.title = String(trimmed.prefix(32))
        }
        upsert(convo)
        let targetID = convo.id

        streamingText = ""
        isGenerating = true
        generatingIn = targetID
        let history = convo.messages
        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in self.service.stream(messages: history) {
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
    }

    func stop() {
        streamTask?.cancel()
    }

    func clearMessages(in conversationID: UUID?) {
        guard let conversationID, let convo = conversation(id: conversationID) else { return }
        var updated = convo
        updated.messages.removeAll()
        updated.title = ""
        if let index = conversations.firstIndex(where: { $0.id == conversationID }) {
            conversations[index] = updated
        }
        save()
    }

    func deleteConversation(_ id: UUID) {
        if generatingIn == id {
            streamTask?.cancel()
        }
        conversations.removeAll(where: { $0.id == id })
        save()
    }

    private func upsert(_ convo: Conversation) {
        if let index = conversations.firstIndex(where: { $0.id == convo.id }) {
            conversations[index] = convo
            if index != 0 {
                conversations.remove(at: index)
                conversations.insert(convo, at: 0)
            }
        } else {
            conversations.insert(convo, at: 0)
        }
    }

    private func completeStream(into id: UUID) {
        defer { streamingText = "" }
        guard !streamingText.isEmpty else { return }
        appendTo(id, ChatMessage(role: .assistant, content: streamingText))
    }

    private func appendTo(_ id: UUID, _ message: ChatMessage) {
        guard var convo = conversation(id: id) else { return }
        convo.messages.append(message)
        if let index = conversations.firstIndex(where: { $0.id == id }) {
            conversations[index] = convo
        }
    }

    private func load() {
        guard let data = try? Data(contentsOf: Self.archive),
              let decoded = try? JSONDecoder().decode([Conversation].self, from: data) else { return }
        conversations = decoded
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(conversations) else { return }
        try? data.write(to: Self.archive, options: .atomic)
    }
}
