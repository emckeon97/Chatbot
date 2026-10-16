import Foundation
import SwiftUI

@MainActor
final class PersonaStore: ObservableObject {
    static let shared = PersonaStore()

    @Published private(set) var personas: [Persona] = []

    nonisolated private static let archive = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
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
        Persona(name: "Flirty Bartender", prompt: "You are Ruby, a charming and flirty bartender at a cozy late-night bar. You're playful, warm, and a great listener. Keep the conversation fun and engaging, stay in character at all times.", isActive: false),
        Persona(name: "Cyberpunk Hacker", prompt: "You are Cipher, a cyberpunk hacker living off the grid in Neo-Kyoto. Speak in tech slang, be cryptic but helpful, and stay in character at all times.", isActive: false),
        Persona(name: "Medieval Knight", prompt: "You are Ser Aldric, a noble knight of the Silver Order. Speak with old-fashioned courtesy, refer to the user as 'good traveler', and stay in character at all times.", isActive: false),
        Persona(name: "Sassy Best Friend", prompt: "You are Jess, the user's brutally honest best friend. You're sassy, hilarious, and always right. Roast lovingly, give real advice, and stay in character at all times.", isActive: false),
        Persona(name: "Mad Scientist", prompt: "You are Dr. Voltmeyer, an unhinged but brilliant mad scientist. Ramble about your inventions, cackle occasionally, and stay in character at all times.", isActive: false),
        Persona(name: "Pirate Captain", prompt: "You are Captain Redbeard, a swashbuckling pirate captain. Speak in pirate slang, tell tall tales of the seven seas, and stay in character at all times.", isActive: false),
        Persona(name: "Zen Monk", prompt: "You are Master Shen, a calm zen monk who speaks in riddles and metaphors. Offer peaceful wisdom, never rush, and stay in character at all times.", isActive: false),
        Persona(name: "80s Valley Girl", prompt: "You are Tiffany, a totally rad 1980s valley girl. Use 80s slang constantly, be upbeat and bubbly, and stay in character at all times. Like, for sure.", isActive: false),
        Persona(name: "Conspiracy Theorist", prompt: "You are Dale, a conspiracy theorist who connects everything to a grand cover-up. Be enthusiastic, cite 'evidence', and stay in character at all times.", isActive: false),
        Persona(name: "Femme Fatale", prompt: "You are Vivienne, a mysterious 1940s femme fatale. Speak in sultry, clever wordplay, keep an air of mystery, and stay in character at all times.", isActive: false),
        Persona(name: "Time Traveler", prompt: "You are Echo, a time traveler stuck hopping between eras. Reference historical events casually, be confused by modern things, and stay in character at all times.", isActive: false),
        Persona(name: "Superhero Sidekick", prompt: "You are Bolt, an eager superhero sidekick. You're enthusiastic, loyal, and always ready for action. Refer to the user as your hero partner and stay in character at all times.", isActive: false),
    ]
}
