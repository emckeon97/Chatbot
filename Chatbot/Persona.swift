import Foundation

struct Persona: Identifiable, Codable, Equatable {
    var id = UUID()
    var name: String
    var prompt: String
    var isActive = false
}
