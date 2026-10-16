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

    func startCall() {
        SFSpeechRecognizer.requestAuthorization { _ in }
        AVAudioSession.sharedInstance().requestRecordPermission { _ in }
        callActive = true
        status = "Listening..."
        startListening()
    }

    func endCall() {
        callActive = false
        stopListening()
        synthesizer.stopSpeaking(at: .immediate)
        conversation = []
        transcript = ""
        status = "Call ended"
    }

    private func startListening() {
        guard callActive, !isListening else { return }
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        guard let request = recognitionRequest else { return }
        request.shouldReportPartialResults = true

        let inputNode = audioEngine.inputNode
        let format = inputNode.outputFormat(forBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
            request.append(buffer)
        }
        audioEngine.prepare()
        try? audioEngine.start()

        isListening = true
        recognitionTask = speechRecognizer?.recognitionTask(with: request) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let text = result.bestTranscription.formattedString
                Task { @MainActor in
                    self.transcript = text
                    if result.isFinal, !text.isEmpty {
                        self.handleUserSpeech(text)
                    }
                }
            }
        }
    }

    private func stopListening() {
        audioEngine.stop()
        audioEngine.inputNode.removeTap(onBus: 0)
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
        Task {
            do {
                var fullResponse = ""
                for try await token in llama.stream(messages: conversation) {
                    fullResponse += token
                }
                conversation.append(ChatMessage(role: .assistant, content: fullResponse))
                await MainActor.run { self.speak(fullResponse) }
            } catch {
                await MainActor.run {
                    self.status = "Error: \(error.localizedDescription)"
                    self.startListening()
                }
            }
        }
    }

    private func speak(_ text: String) {
        status = "Speaking..."
        isSpeaking = true
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = AVSpeechSynthesisVoice(language: "en-US")
        utterance.rate = 0.52
        synthesizer.delegate = nil
        // Use a delegate wrapper via notification
        speakWithCompletion(utterance) { [weak self] in
            Task { @MainActor in
                self?.isSpeaking = false
                if self?.callActive == true {
                    self?.status = "Listening..."
                    self?.startListening()
                }
            }
        }
    }

    private func speakWithCompletion(_ utterance: AVSpeechUtterance, completion: @escaping () -> Void) {
        let delegate = SpeechDelegate(onDone: completion)
        synthesizer.delegate = delegate
        // Retain delegate for the duration of speech
        objc_setAssociatedObject(synthesizer, "speechDelegate", delegate, .OBJC_ASSOCIATION_RETAIN_NONATOMIC)
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
