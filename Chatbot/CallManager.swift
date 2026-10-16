import Foundation
import Speech
import AVFoundation

/// Manages a voice call with the chatbot: speech-to-text in, TTS out.
@MainActor
final class CallManager: ObservableObject {
    @Published private(set) var isListening = false
    @Published private(set) var isSpeaking = false
    @Published private(set) var transcript = ""
    @Published private(set) var status = "Connecting..."

    private let speechRecognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
    private var recognitionRequest: SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask: SFSpeechRecognitionTask?
    private let audioEngine = AVAudioEngine()
    private let synthesizer = AVSpeechSynthesizer()
    private let llama = LlamaService()
    private var conversation: [ChatMessage] = []
    private var callActive = false
    private var speechDelegate: SpeechDelegate?

    func startCall() {
        // Configure audio session for both recording and playback
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
            try session.setActive(true)
        } catch {
            status = "Audio setup failed: \(error.localizedDescription)"
            return
        }

        SFSpeechRecognizer.requestAuthorization { [weak self] authStatus in
            Task { @MainActor in
                guard authStatus == .authorized else {
                    self?.status = "Speech recognition not authorized."
                    return
                }
                AVAudioApplication.requestRecordPermission { granted in
                    Task { @MainActor in
                        guard granted else {
                            self?.status = "Microphone not authorized."
                            return
                        }
                        self?.beginConversation()
                    }
                }
            }
        }
    }

    private func beginConversation() {
        callActive = true
        status = "Listening..."
        // Greet the user
        speak("Hey, I'm listening. What do you want to talk about?")
    }

    func endCall() {
        callActive = false
        stopListening()
        synthesizer.stopSpeaking(at: .immediate)
        conversation = []
        transcript = ""
        status = "Call ended"
        try? AVAudioSession.sharedInstance().setActive(false)
    }

    private func startListening() {
        guard callActive, !isListening, !isSpeaking else { return }

        // Ensure audio session is active for recording
        try? AVAudioSession.sharedInstance().setActive(true)

        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let request = recognitionRequest else { return }
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = false

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        audioEngine.prepare()
        do {
            try audioEngine.start()
        } catch {
            status = "Could not start audio engine."
            return
        }

        isListening = true
        status = "Listening..."

        var finalHandled = false
        recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in
                    self.transcript = text
                    if result.isFinal, !text.trimmingCharacters(in: .whitespaces).isEmpty, !finalHandled {
                        finalHandled = true
                        self.handleUserSpeech(text)
                    }
                }
            }
            if error != nil, !finalHandled {
                // Restart listening on error (e.g. timeout)
                Task { @MainActor in
                    self.stopListening()
                    if self.callActive, !self.isSpeaking {
                        // Brief pause before restarting
                        try? await Task.sleep(nanoseconds: 500_000_000)
                        self.startListening()
                    }
                }
            }
        }
    }

    private func stopListening() {
        if audioEngine.isRunning {
            audioEngine.stop()
            audioEngine.inputNode.removeTap(onBus: 0)
        }
        recognitionRequest?.endAudio()
        recognitionTask?.cancel()
        recognitionRequest = nil
        recognitionTask = nil
        isListening = false
    }

    private func handleUserSpeech(_ text: String) {
        stopListening()
        status = "Thinking..."
        transcript = ""
        conversation.append(ChatMessage(role: .user, content: text))
        // Keep conversation manageable
        if conversation.count > 20 {
            conversation = Array(conversation.suffix(20))
        }
        Task {
            do {
                var fullResponse = ""
                for try await token in llama.stream(messages: conversation) {
                    fullResponse += token
                }
                let response = fullResponse.trimmingCharacters(in: .whitespacesAndNewlines)
                conversation.append(ChatMessage(role: .assistant, content: response))
                await MainActor.run { self.speak(response.isEmpty ? "Sorry, I didn't catch that." : response) }
            } catch {
                await MainActor.run {
                    self.status = "Error: \(error.localizedDescription)"
                    self.speak("Sorry, something went wrong.")
                }
            }
        }
    }

    private func speak(_ text: String) {
        stopListening()
        status = "Speaking..."
        isSpeaking = true
        transcript = ""
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = 0.52
        utterance.volume = 1.0

        let delegate = SpeechDelegate(
            onDone: { [weak self] in
                Task { @MainActor in
                    self?.isSpeaking = false
                    self?.speechDelegate = nil
                    if self?.callActive == true {
                        self?.startListening()
                    }
                }
            }
        )
        speechDelegate = delegate
        synthesizer.delegate = delegate
        synthesizer.speak(utterance)
    }
}

private final class SpeechDelegate: NSObject, AVSpeechSynthesizerDelegate {
    private let onDone: () -> Void
    init(onDone: @escaping () -> Void) { self.onDone = onDone }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        onDone()
    }
    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        onDone()
    }
}
