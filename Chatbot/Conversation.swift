import Foundation

struct Conversation: Identifiable, Codable, Equatable {
    var id = UUID()
    var createdAt = Date()
    var title = ""
    var messages: [ChatMessage] = []
}
