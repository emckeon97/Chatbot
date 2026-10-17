import SwiftUI
import AVFoundation

struct SettingsView: View {
    @ObservedObject private var googleAuth = GoogleAuthService.shared
    @AppStorage("callVoiceIdentifier") private var callVoiceIdentifier = ""
    @State private var availableVoices: [AVSpeechSynthesisVoice] = []
    @AppStorage("serverURL") private var serverURL = "http://192.168.0.102:11434"
    @AppStorage("tailscaleServer") private var tailscaleURL = ""
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
                TextField("Tailscale IP (e.g. http://100.x.x.x:11434)", text: $tailscaleURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Server")
            } footer: {
                Text("Tailscale IP is used as a fallback when you're away from home Wi-Fi. Ollama listens on port 11434.")
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
            Section("Google") {
                if googleAuth.isSignedIn {
                    HStack {
                        Text(googleAuth.userEmail ?? "Signed in")
                        Spacer()
                        Button("Sign Out") {
                            googleAuth.signOut()
                        }
                        .foregroundStyle(.red)
                    }
                } else {
                    Button("Connect Google Account") {
                        googleAuth.signIn()
                    }
                    if let err = googleAuth.errorMessage {
                        Text(err)
                            .font(.footnote)
                            .foregroundStyle(.red)
                    }
                }
            } footer: {
                Text("Connect your Google account for Gmail access.")
            }
            Section("Call Voice") {
                if availableVoices.isEmpty {
                    Text("No voices found")
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Voice", selection: $callVoiceIdentifier) {
                        Text("Auto (best available)").tag("")
                        ForEach(availableVoices, id: \.identifier) { voice in
                            Text("\(voice.name) (\(voice.quality == .premium ? "Premium" : voice.quality == .enhanced ? "Enhanced" : "Default"))").tag(voice.identifier)
                        }
                    }
                    .pickerStyle(.menu)
                }
            } footer: {
                Text("Download premium voices in Settings > Accessibility > Spoken Content > Voices for the most natural sound.")
            }
            Section {
                Text("Changes apply from your next message.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
        .onAppear {
            Task { await loadModels() }
            availableVoices = AVSpeechSynthesisVoice.speechVoices()
                .filter { $0.language.hasPrefix("en") }
                .sorted { $0.name < $1.name }
        }
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
