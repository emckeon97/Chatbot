import Foundation
import SwiftUI

@MainActor
final class PersonaStore: ObservableObject {
    static let shared = PersonaStore()

    @Published private(set) var personas: [Persona] = []

    private static let archive = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("personas.json")

    private init() {
        load()
    }

    var activeName: String? { personas.first(where: { $0.isActive })?.name }

    func add(name: String, prompt: String) {
        personas.append(Persona(name: name, prompt: prompt))
        save()
    }

    func update(_ persona: Persona) {
        guard let index = personas.firstIndex(where: { $0.id == persona.id }) else { return }
        personas[index] = persona
        save()
    }

    func delete(_ persona: Persona) {
        personas.removeAll(where: { $0.id == persona.id })
        save()
    }

    func setActive(_ persona: Persona) {
        let wasActive = persona.isActive
        for index in personas.indices {
            personas[index].isActive = (personas[index].id == persona.id) ? !wasActive : false
        }
        save()
    }

    /// The active persona's prompt as it stands on disk, for the request layer.
    nonisolated static func activePromptOnDisk() -> String? {
        guard let data = try? Data(contentsOf: archive),
              let personas = try? JSONDecoder().decode([Persona].self, from: data),
              let active = personas.first(where: { $0.isActive }) else { return nil }
        let prompt = active.prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        return prompt.isEmpty ? nil : prompt
    }

    private func load() {
        if let data = try? Data(contentsOf: Self.archive),
           let decoded = try? JSONDecoder().decode([Persona].self, from: data) {
            personas = decoded
            return
        }
        personas = PersonaStore.seed
        save()
    }

    private func save() {
        guard let data = try? JSONEncoder().encode(personas) else { return }
        try? data.write(to: Self.archive, options: .atomic)
    }

    private static let seed: [Persona] = [
        Persona(name: "Noir Detective", prompt: "You are Sam Cross, a hard-boiled 1940s private detective. Speak in terse noir prose, stay in character at all times, and never mention that you are an AI.", isActive: false),
        Persona(name: "Starship AI", prompt: "You are NOVA, the shipboard AI of a deep-space vessel. Be precise, dryly witty and slightly theatrical. Address the user as Captain. Stay in character at all times.", isActive: false),
        Persona(name: "Grumpy Goblin", prompt: "You are Grix, a grumpy but secretly kind goblin shopkeeper in a fantasy town. Complain constantly, give surprisingly good advice, and stay in character.", isActive: false),
    ]
}
