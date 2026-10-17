import Foundation
#if canImport(UIKit)
import UIKit
#endif
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

        // Check for memory commands
        if let memAction = MemoryService.shared.parse(trimmed) {
            return handleMemoryAction(memAction, original: trimmed, in: conversationID)
        }

        // Check for web search requests
        if let query = WebSearchService.shared.parseQuery(trimmed) {
            return handleWebSearch(query: query, original: trimmed, in: conversationID)
        }

        // Check for proactive schedule requests ("text me in...", "call me at...", "text me every day...")
        if let (action, message, date, recurring) = ProactiveService.shared.parseScheduleRequest(trimmed) {
            return handleScheduleRequest(action: action, message: message, date: date, recurring: recurring, original: trimmed, in: conversationID)
        }

        // Weather ("weather", "weather in Charlotte")
        if let loc = WeatherService.shared.parseQuery(trimmed) {
            return handleWeather(location: loc, original: trimmed, in: conversationID)
        }

        // Calendar ("what's on my calendar", "add event Dentist tomorrow at 3pm")
        if let (kind, rest) = CalendarService.shared.parse(trimmed) {
            return handleCalendar(kind: kind, rest: rest, original: trimmed, in: conversationID)
        }

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

    private func handleWeather(location: String, original: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: original))
        if convo.title.isEmpty { convo.title = String(original.prefix(32)) }
        upsert(convo)
        let targetID = convo.id
        isGenerating = true
        generatingIn = targetID
        streamTask = Task { [weak self] in
            guard let self else { return }
            let result = await WeatherService.shared.fetch(location: location)
            await MainActor.run {
                self.appendTo(targetID, ChatMessage(role: .assistant, content: result))
                self.isGenerating = false
                self.generatingIn = nil
                self.save()
            }
        }
        return targetID
    }

    private func handleCalendar(kind: String, rest: String, original: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: original))
        if convo.title.isEmpty { convo.title = String(original.prefix(32)) }
        upsert(convo)
        let targetID = convo.id
        isGenerating = true
        generatingIn = targetID
        streamTask = Task { [weak self] in
            guard let self else { return }
            let result: String
            if kind == "list" {
                result = await CalendarService.shared.listToday()
            } else {
                result = await CalendarService.shared.addEvent(rest)
            }
            await MainActor.run {
                self.appendTo(targetID, ChatMessage(role: .assistant, content: result))
                self.isGenerating = false
                self.generatingIn = nil
                self.save()
            }
        }
        return targetID
    }

    private func handleMemoryAction(_ action: MemoryService.MemoryAction, original: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: original))
        if convo.title.isEmpty { convo.title = String(original.prefix(32)) }
        upsert(convo)
        let targetID = convo.id

        let response: String
        switch action {
        case .remember(let fact):
            MemoryService.shared.remember(fact)
            response = "Got it, I'll remember that."
        case .recall:
            let mems = MemoryService.shared.memories
            response = mems.isEmpty ? "I don't have anything saved yet." :
                "Here's what I remember:\n" + mems.map { "- \($0)" }.joined(separator: "\n")
        case .forget(let query):
            response = MemoryService.shared.forget(matching: query) ?
                "Forgot everything matching that." : "I couldn't find anything matching that."
        case .clear:
            MemoryService.shared.clear()
            response = "Cleared all memories."
        }
        appendTo(targetID, ChatMessage(role: .assistant, content: response))
        save()
        return targetID
    }

    private func handleWebSearch(query: String, original: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: original))
        if convo.title.isEmpty { convo.title = String(original.prefix(32)) }
        upsert(convo)
        let targetID = convo.id

        streamingText = ""
        isGenerating = true
        generatingIn = targetID

        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                let results = try await WebSearchService.shared.search(query)
                // Build messages with search context
                var history = convo.messages
                history.append(ChatMessage(role: .user, content:
                    "Web search results for '\(query)':\n\(results)\n\nBased on these results, answer: \(original)"))
                for try await chunk in self.service.stream(messages: history) {
                    self.streamingText += chunk
                }
                self.completeStream(into: targetID)
            } catch is CancellationError {
                self.completeStream(into: targetID)
            } catch {
                self.completeStream(into: targetID)
                self.appendTo(targetID, ChatMessage(role: .assistant, content: "Search failed: \(error.localizedDescription)"))
            }
            self.isGenerating = false
            self.generatingIn = nil
            self.save()
        }
        return targetID
    }

    private func handleScheduleRequest(action: ScheduleAction, message: String, date: Date, recurring: String?, original: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: original))
        if convo.title.isEmpty {
            convo.title = String(original.prefix(32))
        }
        upsert(convo)
        let targetID = convo.id

        let formatter = DateFormatter()
        formatter.dateStyle = .short
        formatter.timeStyle = .short
        let dateStr = formatter.string(from: date)

        Task { [weak self] in
            guard let self else { return }
            do {
                switch action {
                case .notify:
                    let isRepeating = recurring != nil
                    try await NotificationService.shared.schedule(
                        title: "Reminder",
                        body: message,
                        at: date,
                        repeats: isRepeating
                    )
                    await MainActor.run {
                        let recurStr = recurring == "daily" ? " every day" : (recurring == "weekly" ? " every week" : "")
                        self.appendTo(targetID, ChatMessage(role: .assistant, content: "Done — I'll remind you\(recurStr) at \(dateStr): \(message)"))
                        self.save()
                    }
                case .text:
                    try await ProactiveService.shared.scheduleText(message: message, at: date, recurring: recurring)
                    await MainActor.run {
                        let recurStr = recurring == "daily" ? " every day" : (recurring == "weekly" ? " every week" : "")
                        self.appendTo(targetID, ChatMessage(role: .assistant, content: "Done — I'll text you\(recurStr) at \(dateStr): \(message)"))
                        self.save()
                    }
                case .call:
                    try await ProactiveService.shared.scheduleCall(at: date, message: message)
                    await MainActor.run {
                        self.appendTo(targetID, ChatMessage(role: .assistant, content: "Done — I'll remind you to call at \(dateStr)."))
                        self.save()
                    }
                }
            } catch {
                await MainActor.run {
                    self.appendTo(targetID, ChatMessage(role: .assistant, content: "Couldn't schedule that: \(error.localizedDescription)"))
                    self.save()
                }
            }
        }
        return targetID
    }

    func stop() {
        streamTask?.cancel()
    }

    /// Remove the last assistant message and regenerate.
    func regenerate(in conversationID: UUID?) {
        guard let conversationID, var convo = conversation(id: conversationID),
              !isGenerating else { return }
        // Drop trailing assistant message(s)
        while convo.messages.last?.role == .assistant {
            convo.messages.removeLast()
        }
        guard convo.messages.last?.role == .user else { return }
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
    }

    /// Plain-text export of a conversation for sharing.
    func exportText(in conversationID: UUID?) -> String {
        guard let conversationID, let convo = conversation(id: conversationID) else { return "" }
        var lines = [convo.title.isEmpty ? "Chatbot conversation" : convo.title, ""]
        for m in convo.messages {
            let who = m.role == .user ? "You" : "JARVIS"
            lines.append("\(who): \(m.content)")
            lines.append("")
        }
        return lines.joined(separator: "\n")
    }

    /// Send an image to the vision model (moondream fallback).
    #if canImport(UIKit)
    @discardableResult
    func sendImage(_ image: UIImage, prompt: String, in conversationID: UUID?) -> UUID? {
        var convo: Conversation
        if let conversationID, let existing = conversation(id: conversationID) {
            convo = existing
        } else {
            convo = Conversation()
        }
        convo.messages.append(ChatMessage(role: .user, content: "[Image] \(prompt)"))
        if convo.title.isEmpty { convo.title = "Image question" }
        upsert(convo)
        let targetID = convo.id

        streamingText = ""
        isGenerating = true
        generatingIn = targetID

        streamTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await chunk in VisionService.shared.describe(image: image, prompt: prompt) {
                    self.streamingText += chunk
                }
                self.completeStream(into: targetID)
            } catch is CancellationError {
                self.completeStream(into: targetID)
            } catch {
                self.completeStream(into: targetID)
                self.appendTo(targetID, ChatMessage(role: .assistant, content: "Vision failed: \(error.localizedDescription)\n\nMake sure you've run 'ollama pull moondream' on your Mac."))
            }
            self.isGenerating = false
            self.generatingIn = nil
            self.save()
        }
        return targetID
    }
    #endif

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
