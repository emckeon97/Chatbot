import SwiftUI

struct PersonasView: View {
    @ObservedObject private var store = PersonaStore.shared
    @State private var editing: Persona?
    @State private var addingNew = false

    var body: some View {
        Group {
            if store.personas.isEmpty {
                VStack(spacing: 10) {
                    Image(systemName: "theatermasks")
                        .font(.system(size: 44, weight: .light))
                        .foregroundStyle(.tint)
                    Text("No characters yet")
                        .font(.headline)
                    Text("Create one and give it a personality prompt.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    ForEach(store.personas) { persona in
                        HStack(spacing: 10) {
                            Button {
                                store.setActive(persona)
                            } label: {
                                Image(systemName: persona.isActive ? "checkmark.circle.fill" : "circle")
                                    .foregroundStyle(persona.isActive ? Color.accentColor : Color.secondary)
                            }
                            .buttonStyle(.borderless)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(persona.name)
                                    .fontWeight(.medium)
                                Text(persona.prompt)
                                    .font(.footnote)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        .contentShape(Rectangle())
                        .onTapGesture { editing = persona }
                        .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                            Button(role: .destructive) {
                                store.delete(persona)
                            } label: {
                                Label("Delete", systemImage: "trash")
                            }
                        }
                    }
                }
            }
        }
        .navigationTitle("Characters")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    addingNew = true
                } label: {
                    Image(systemName: "plus")
                }
            }
        }
        .sheet(isPresented: $addingNew) {
            EditPersonaView(existing: nil)
        }
        .sheet(item: $editing) { persona in
            EditPersonaView(existing: persona)
        }
    }
}

struct EditPersonaView: View {
    let existing: Persona?
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var store = PersonaStore.shared
    @State private var name = ""
    @State private var prompt = ""

    var body: some View {
        NavigationStack {
            Form {
                TextField("Name", text: $name)
                Section("Personality prompt") {
                    TextEditor(text: $prompt)
                        .frame(minHeight: 160)
                }
                Section {
                    Text("Example: You are Sam Cross, a hard-boiled 1940s private detective. Stay in character at all times.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle(existing == nil ? "New Character" : "Edit Character")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Save") {
                        save()
                        dismiss()
                    }
                    .disabled(name.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            }
            .onAppear {
                if let existing {
                    name = existing.name
                    prompt = existing.prompt
                }
            }
        }
    }

    private func save() {
        let cleanName = name.trimmingCharacters(in: .whitespacesAndNewlines)
        let cleanPrompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        if var persona = existing {
            persona.name = cleanName
            persona.prompt = cleanPrompt
            store.update(persona)
        } else {
            store.add(name: cleanName, prompt: cleanPrompt)
        }
    }
}
