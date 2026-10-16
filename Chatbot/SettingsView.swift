import SwiftUI

struct SettingsView: View {
    @AppStorage("serverURL") private var serverURL = "http://192.168.0.102:11434"
    @AppStorage("modelName") private var modelName = "dolphin-llama3:8b"
    @AppStorage("temperature") private var temperature = 0.7
    @AppStorage("systemPrompt") private var systemPrompt = ""
    @ObservedObject private var personaStore = PersonaStore.shared
    @State private var availableModels: [String] = []
    @State private var modelsLoading = false

    var body: some View {
        Form {
            Section {
                TextField("http://192.168.0.102:11434", text: $serverURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onChange(of: serverURL) { Task { await loadModels() } }
            } header: {
                Text("Server")
            } footer: {
                Text("Ollama listens on port 11434. On your iPhone, use your Mac's network address, like http://192.168.1.20:11434. A llama.cpp server works too (port 8080); both speak the same API.")
            }
            Section("Model") {
                if availableModels.isEmpty {
                    TextField("dolphin-llama3:8b", text: $modelName)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                } else {
                    Picker("Model", selection: $modelName) {
                        ForEach(availableModels, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                    .pickerStyle(.menu)
                }
                HStack {
                    if modelsLoading {
                        ProgressView()
                            .scaleEffect(0.8)
                    }
                    Button("Refresh models") {
                        Task { await loadModels() }
                    }
                    .font(.footnote)
                    .disabled(modelsLoading)
                }
                HStack {
                    Text("Temperature")
                    Slider(value: $temperature, in: 0...1.5, step: 0.1)
                    Text(String(format: "%.1f", temperature))
                        .monospacedDigit()
                        .frame(width: 34)
                }
            }
            Section {
                NavigationLink {
                    PersonasView()
                } label: {
                    HStack {
                        Text("Characters")
                        Spacer()
                        Text(personaStore.activeName ?? "None")
                            .foregroundStyle(.secondary)
                    }
                }
            } header: {
                Text("Character")
            } footer: {
                Text("A character gives the model a role to play and it stays in that role. The system prompt below applies when no character is active.")
            }
            Section("System prompt (used when no character is active)") {
                TextEditor(text: $systemPrompt)
                    .frame(minHeight: 80)
            }
            Section {
                Text("Changes apply from your next message.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { Task { await loadModels() } }
    }

    private func loadModels() async {
        modelsLoading = true
        defer { modelsLoading = false }
        do {
            let models = try await LlamaService().fetchModels()
            availableModels = models
            // If the saved model isn't installed anymore, fall back to the first available.
            if !models.contains(modelName), let first = models.first {
                modelName = first
            }
        } catch {
            availableModels = []
        }
    }

    static var endpointSummary: String {
        let settings = UserDefaults.standard
        let server = settings.string(forKey: "serverURL") ?? "http://192.168.0.102:11434"
        let model = settings.string(forKey: "modelName") ?? "dolphin-llama3:8b"
        return "\(model) at \(server)"
    }
}
