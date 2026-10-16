import Foundation

/// Long-term memory: stores facts across conversations.
/// Say "remember that my dog's name is Rex" to save,
/// "what do you remember" to list, "forget that..." to remove.
@MainActor
final class MemoryService: ObservableObject {
    static let shared = MemoryService()

    @Published private(set) var memories: [String] = []

    private var fileURL: URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("memories.json")
    }

    private init() {
        load()
    }

    private func load() {
        guard let data = try? Data(contentsOf: fileURL),
              let decoded = try? JSONDecoder().decode([String].self, from: data) else {
            memories = []
            return
        }
        memories = decoded
    }

    private func save() {
        try? JSONEncoder().encode(memories).write(to: fileURL)
    }

    func remember(_ fact: String) {
        let trimmed = fact.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, !memories.contains(trimmed) else { return }
        memories.append(trimmed)
        save()
    }

    func forget(matching query: String) -> Bool {
        let lower = query.lowercased()
        let before = memories.count
        memories.removeAll { $0.lowercased().contains(lower) }
        if memories.count < before {
            save()
            return true
        }
        return false
    }

    func clear() {
        memories = []
        save()
    }

    /// Format memories for injection into the system prompt.
    func promptContext() -> String {
        guard !memories.isEmpty else { return "" }
        return "\n\nThings to remember about the user:\n" +
            memories.map { "- \($0)" }.joined(separator: "\n")
    }

    // MARK: - Parsing

    enum MemoryAction {
        case remember(String)
        case recall
        case forget(String)
        case clear
    }

    /// Parse memory commands. Returns nil if not a memory command.
    func parse(_ input: String) -> MemoryAction? {
        let lower = input.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)

        if lower == "what do you remember" || lower == "what do you remember about me" ||
           lower == "list your memories" || lower == "show memories" {
            return .recall
        }
        if lower == "forget everything" || lower == "clear memories" {
            return .clear
        }
        if lower.hasPrefix("remember that ") {
            let fact = String(input.dropFirst("remember that ".count))
            return fact.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : .remember(fact)
        }
        if lower.hasPrefix("remember ") {
            let fact = String(input.dropFirst("remember ".count))
            return fact.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : .remember(fact)
        }
        if lower.hasPrefix("forget that ") {
            let query = String(input.dropFirst("forget that ".count))
            return query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : .forget(query)
        }
        if lower.hasPrefix("forget ") {
            let query = String(input.dropFirst("forget ".count))
            return query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? nil : .forget(query)
        }
        return nil
    }
}
