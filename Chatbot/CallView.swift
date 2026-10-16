import SwiftUI

/// Full-screen voice call UI for talking to the chatbot.
struct CallView: View {
    @StateObject private var callManager = CallManager()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()
            VStack(spacing: 32) {
                Spacer()
                // Avatar / waveform
                ZStack {
                    Circle()
                        .fill(Color.blue.opacity(0.2))
                        .frame(width: 160, height: 160)
                    if callManager.isSpeaking {
                        Circle()
                            .stroke(Color.blue, lineWidth: 3)
                            .frame(width: 160, height: 160)
                            .scaleEffect(1.15)
                            .opacity(0.6)
                            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: callManager.isSpeaking)
                    }
                    Image(systemName: callManager.isListening ? "mic.fill" : "mic.slash.fill")
                        .font(.system(size: 56))
                        .foregroundStyle(.white)
                }
                Text(callManager.status)
                    .font(.headline)
                    .foregroundStyle(.white.opacity(0.8))
                if !callManager.transcript.isEmpty {
                    Text(callManager.transcript)
                        .font(.body)
                        .foregroundStyle(.white.opacity(0.6))
                        .padding(.horizontal, 32)
                        .lineLimit(3)
                }
                Spacer()
                // End call
                Button {
                    callManager.endCall()
                    dismiss()
                } label: {
                    Image(systemName: "phone.down.fill")
                        .font(.system(size: 28))
                        .foregroundStyle(.white)
                        .padding(24)
                        .background(Color.red, in: Circle())
                }
                .padding(.bottom, 48)
            }
        }
        .onAppear { callManager.startCall() }
        .onDisappear { callManager.endCall() }
    }
}
