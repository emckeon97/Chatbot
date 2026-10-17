import SwiftUI
import AVFoundation

struct SettingsView: View {
    @ObservedObject private var googleAuth = GoogleAuthService.shared
    @AppStorage("callVoiceIdentifier") private var callVoiceIdentifier = ""
    @State private var availableVoices: [AVSpeechSynthesisVoice] = []
    @State private var serverStatus: [String: Bool] = [:]
    @State private var testingConnection = false
    @State private var githubToken = ""
    @State private var githubConnected = GitHubService.shared.hasToken
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
                ServerRow(label: "Primary", url: $serverURL, placeholder: "http://192.168.0.102:11434",
                          status: serverStatus[serverURL], onTest: testConnections)
                ServerRow(label: "Tailscale", url: $tailscaleURL, placeholder: "http://100.x.x.x:11434",
                          status: tailscaleURL.isEmpty ? nil : serverStatus[tailscaleURL], onTest: testConnections)
                if testingConnection {
                    HStack { Spacer(); ProgressView().scaleEffect(0.8); Spacer() }
                }
            } header: {
                Text("Server")
            } footer: {
                Text("Tailscale IP is used as a fallback when you're away from home Wi-Fi. Ollama listens on port 11434. Green means reachable.")
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
                GoogleSignInRow(auth: googleAuth)
            } header: {
                Text("Google")
            } footer: {
                Text("Connect your Google account for Gmail access.")
            }
            Section {
                GitHubRow(token: $githubToken, connected: $githubConnected)
            } header: {
                Text("GitHub")
            } footer: {
                Text("Create a token at github.com/settings/tokens with 'repo' scope.")
            }
            Section {
                VoicePickerRow(voices: availableVoices, selection: $callVoiceIdentifier)
            } header: {
                Text("Call Voice")
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

    private func testConnections() {
        testingConnection = true
        Task {
            var results: [String: Bool] = [:]
            for raw in [serverURL, tailscaleURL] {
                let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
                guard !trimmed.isEmpty else { continue }
                var s = trimmed
                if !s.hasPrefix("http") { s = "http://" + s }
                guard let url = URL(string: s + "/api/tags") else {
                    results[raw] = false
                    continue
                }
                do {
                    var req = URLRequest(url: url)
                    req.timeoutInterval = 5
                    let (_, resp) = try await URLSession.shared.data(for: req)
                    results[raw] = (resp as? HTTPURLResponse)?.statusCode == 200
                } catch {
                    results[raw] = false
                }
            }
            await MainActor.run {
                serverStatus = results
                testingConnection = false
            }
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

private struct GoogleSignInRow: View {
    @ObservedObject var auth: GoogleAuthService

    var body: some View {
        if auth.isSignedIn {
            HStack {
                Text(auth.userEmail ?? "Signed in")
                Spacer()
                Button("Sign Out") { auth.signOut() }
                    .foregroundStyle(.red)
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Button("Connect Google Account") { auth.signIn() }
                if let err = auth.errorMessage {
                    Text(err)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }
        }
    }
}

private struct VoicePickerRow: View {
    let voices: [AVSpeechSynthesisVoice]
    @Binding var selection: String

    var body: some View {
        if voices.isEmpty {
            Text("No voices found")
                .foregroundStyle(.secondary)
        } else {
            Picker("Voice", selection: $selection) {
                Text("Auto (best available)").tag("")
                ForEach(voices, id: \.identifier) { voice in
                    Text("\(voice.name) (\(voice.quality == .premium ? "Premium" : voice.quality == .enhanced ? "Enhanced" : "Default"))").tag(voice.identifier)
                }
            }
            .pickerStyle(.menu)
        }
    }
}

private struct GitHubRow: View {
    @Binding var token: String
    @Binding var connected: Bool

    var body: some View {
        if connected {
            HStack {
                Text("Connected")
                Spacer()
                Button("Disconnect") {
                    GitHubService.shared.clearToken()
                    connected = false
                    token = ""
                }
                .foregroundStyle(.red)
            }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                SecureField("Personal access token", text: $token)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Connect GitHub") {
                    let t = token.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !t.isEmpty else { return }
                    GitHubService.shared.saveToken(t)
                    connected = true
                    token = ""
                }
                .disabled(token.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }
}

private struct ServerRow: View {
    let label: String
    @Binding var url: String
    let placeholder: String
    let status: Bool?
    let onTest: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Circle()
                    .fill(status == nil ? Color.gray : (status! ? Color.green : Color.red))
                    .frame(width: 8, height: 8)
                Text(label)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            HStack {
                TextField(placeholder, text: $url)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                Button("Test", action: onTest)
                    .font(.footnote)
            }
        }
        .padding(.vertical, 4)
    }
}
