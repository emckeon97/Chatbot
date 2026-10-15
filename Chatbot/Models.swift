import Foundation

struct ChatMessage: Identifiable, Codable, Equatable {
    enum Role: String, Codable {
        case system, user, assistant
    }

    var id = UUID()
    var role: Role
    var content: String

    var isUser: Bool { role == .user }
}
