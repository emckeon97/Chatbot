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
    @Published private(set) var isMuted = false
    @Published private(set) var speakerOn = true
    @Published private(set) var callTimeFormatted = "00:00"

    private var callStart: Date?
    private var timerTask: Task<Void, Never>?

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
            try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker])
        } catch {
            status = "Audio setup failed: \(error.localizedDescription)"
            return
        }
        Task.detached {
            try? AVAudioSession.sharedInstance().setActive(true)
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
        callStart = Date()
        startTimer()
        status = "Listening..."
        // Greet the user
        speak("Hey, I'm listening. What do you want to talk about?")
    }

    func endCall() {
        callActive = false
        timerTask?.cancel()
        timerTask = nil
        stopListening()
        synthesizer.stopSpeaking(at: .immediate)
        conversation = []
        transcript = ""
        status = "Call ended"
        Task.detached {
            try? AVAudioSession.sharedInstance().setActive(false)
        }
    }

    private var silenceTimer: Task<Void, Never>?

    /// Manually submit the current transcript (for the call UI button).
    func submitTranscript() {
        let text = transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        silenceTimer?.cancel()
        handleUserSpeech(text)
    }

    private func startListening() {
        guard callActive, !isListening, !isSpeaking else { return }

        // Ensure audio session is active for recording
        if !AVAudioSession.sharedInstance().isOtherAudioPlaying {
            // Already configured, just ensure active off-main-thread
        }
        Task.detached {
            try? AVAudioSession.sharedInstance().setActive(true)
        }

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
                        self.silenceTimer?.cancel()
                        self.handleUserSpeech(text)
                    } else if !text.trimmingCharacters(in: .whitespaces).isEmpty, !finalHandled {
                        // Reset silence timer on new speech; auto-submit after 1.5s of silence
                        self.silenceTimer?.cancel()
                        self.silenceTimer = Task {
                            try? await Task.sleep(nanoseconds: 1_500_000_000)
                            guard !Task.isCancelled else { return }
                            await MainActor.run {
                                if !finalHandled {
                                    finalHandled = true
                                    self.handleUserSpeech(text)
                                }
                            }
                        }
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
        silenceTimer?.cancel()
        silenceTimer = nil
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
                var sentenceBuffer = ""
                var firstToken = true
                for try await token in llama.stream(messages: conversation) {
                    if firstToken {
                        firstToken = false
                        // Start speaking as soon as we have a sentence
                        await MainActor.run { self.status = "Speaking..." ; self.isSpeaking = true }
                    }
                    fullResponse += token
                    sentenceBuffer += token
                    // Speak complete sentences as they arrive
                    if sentenceBuffer.contains(where: { ".!?".contains($0) }) {
                        let sentences = splitSentences(sentenceBuffer)
                        if sentences.count > 1 {
                            // Speak all but the last (incomplete) sentence
                            for sentence in sentences.dropLast() {
                                let trimmed = sentence.trimmingCharacters(in: .whitespacesAndNewlines)
                                if !trimmed.isEmpty {
                                    await MainActor.run { self.speakChunk(trimmed) }
                                }
                            }
                            sentenceBuffer = sentences.last ?? ""
                        }
                    }
                }
                // Speak any remaining text
                let remaining = sentenceBuffer.trimmingCharacters(in: .whitespacesAndNewlines)
                if !remaining.isEmpty {
                    await MainActor.run { self.speakChunk(remaining) }
                }
                let response = fullResponse.trimmingCharacters(in: .whitespacesAndNewlines)
                conversation.append(ChatMessage(role: .assistant, content: response))
                // Wait for speech to finish, then listen again
                await MainActor.run { self.finishSpeaking() }
            } catch {
                await MainActor.run {
                    self.status = "Error: \(error.localizedDescription)"
                    self.speak("Sorry, something went wrong.")
                }
            }
        }
    }

    private func splitSentences(_ text: String) -> [String] {
        // Simple sentence splitter on . ! ?
        var sentences: [String] = []
        var current = ""
        for char in text {
            current.append(char)
            if ".!?".contains(char) {
                sentences.append(current)
                current = ""
            }
        }
        if !current.isEmpty {
            sentences.append(current)
        }
        return sentences
    }

    /// Speak a chunk without stopping (for streaming).
    private func speakChunk(_ text: String) {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = bestVoice()
        utterance.rate = 0.5
        utterance.pitchMultiplier = 1.05
        utterance.volume = 1.0
        synthesizer.speak(utterance)
    }

    /// Called after all chunks are queued; resumes listening when done.
    private func finishSpeaking() {
        let delegate = SpeechDelegate(
            onDone: { [weak self] in
                Task { @MainActor in
                    self?.isSpeaking = false
                    self?.speechDelegate = nil
                    self?.status = "Listening..."
                    if self?.callActive == true {
                        self?.startListening()
                    }
                }
            }
        )
        speechDelegate = delegate
        synthesizer.delegate = delegate
        // Enqueue a silent marker to trigger completion
        let marker = AVSpeechUtterance(string: " ")
        marker.volume = 0
        synthesizer.speak(marker)
    }

    /// Full speak (for greetings/errors) — replaces old speak().
    private func speak(_ text: String) {
        stopListening()
        status = "Speaking..."
        isSpeaking = true
        transcript = ""
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = bestVoice()
        utterance.rate = 0.5
        utterance.pitchMultiplier = 1.05
        utterance.volume = 1.0

        let delegate = SpeechDelegate(
            onDone: { [weak self] in
                Task { @MainActor in
                    self?.isSpeaking = false
                    self?.speechDelegate = nil
                    if self?.callActive == true {
                        self?.status = "Listening..."
                        self?.startListening()
                    }
                }
            }
        )
        speechDelegate = delegate
        synthesizer.delegate = delegate
        synthesizer.speak(utterance)
    }

    private func startTimer() {
        timerTask?.cancel()
        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                guard let self, let start = self.callStart else { break }
                let elapsed = Int(Date().timeIntervalSince(start))
                let m = elapsed / 60, s = elapsed % 60
                await MainActor.run {
                    self.callTimeFormatted = String(format: "%02d:%02d", m, s)
                }
            }
        }
    }

    func toggleMute() {
        isMuted.toggle()
        audioEngine.inputNode.removeTap(onBus: 0)
        // Re-install tap only when unmuted and listening
        if !isMuted, isListening {
            guard let request = recognitionRequest else { return }
            let inputNode = audioEngine.inputNode
            let format = inputNode.outputFormat(forBus: 0)
            inputNode.installTap(onBus: 0, bufferSize: 1024, format: format) { buffer, _ in
                request.append(buffer)
            }
        }
    }

    func toggleSpeaker() {
        speakerOn.toggle()
        do {
            let session = AVAudioSession.sharedInstance()
            try session.overrideOutputAudioPort(speakerOn ? .speaker : .none)
        } catch {
            status = "Speaker toggle failed."
        }
    }

    /// Pick the voice: user-selected identifier first, else best available English.
    private func selectedVoiceIdentifier() -> String? {
        UserDefaults.standard.string(forKey: "callVoiceIdentifier")
    }

    /// Pick the best available English voice (prefers premium/enhanced).
    private func bestVoice() -> AVSpeechSynthesisVoice? {
        if let id = selectedVoiceIdentifier(),
           let v = AVSpeechSynthesisVoice(identifier: id) {
            return v
        }
        let voices = AVSpeechSynthesisVoice.speechVoices().filter { $0.language.hasPrefix("en") }
        // Prefer premium quality, then enhanced, then default
        if let premium = voices.first(where: { $0.quality == .premium }) {
            return premium
        }
        if let enhanced = voices.first(where: { $0.quality == .enhanced }) {
            return enhanced
        }
        return AVSpeechSynthesisVoice(language: "en-US")
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
