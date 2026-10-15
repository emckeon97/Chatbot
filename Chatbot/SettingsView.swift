import SwiftUI

struct SettingsView: View {
    @AppStorage("serverURL") private var serverURL = "http://localhost:11434"
    @AppStorage("modelName") private var modelName = "llama3.1"
    @AppStorage("temperature") private var temperature = 0.7
    @AppStorage("systemPrompt") private var systemPrompt = ""

    var body: some View {
        Form {
            Section {
                TextField("http://localhost:11434", text: $serverURL)
                    .keyboardType(.URL)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } header: {
                Text("Server")
            } footer: {
                Text("Ollama listens on port 11434. On your iPhone, use your Mac's network address, like http://192.168.1.20:11434. A llama.cpp server works too (port 8080); both speak the same API.")
            }
            Section("Model") {
                TextField("llama3.1", text: $modelName)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                HStack {
                    Text("Temperature")
                    Slider(value: $temperature, in: 0...1.5, step: 0.1)
                    Text(String(format: "%.1f", temperature))
                        .monospacedDigit()
                        .frame(width: 34)
                }
            }
            Section("System prompt") {
                TextEditor(text: $systemPrompt)
                    .frame(minHeight: 90)
            }
            Section {
                Text("Changes apply from your next message.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
        .navigationTitle("Settings")
        .navigationBarTitleDisplayMode(.inline)
    }

    static var endpointSummary: String {
        let settings = UserDefaults.standard
        let server = settings.string(forKey: "serverURL") ?? "http://localhost:11434"
        let model = settings.string(forKey: "modelName") ?? "llama3.1"
        return "\(model) at \(server)"
    }
}
