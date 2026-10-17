import SwiftUI

/// iOS phone-call style UI for talking to the chatbot.
struct CallView: View {
    @StateObject private var callManager = CallManager()
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack {
            // Dark gradient background like the Phone app
            LinearGradient(
                colors: [Color(white: 0.12), Color(white: 0.05)],
                startPoint: .top, endPoint: .bottom
            )
            .ignoresSafeArea()

            VStack(spacing: 0) {
                Spacer().frame(height: 60)

                // Contact avatar
                AvatarView(isSpeaking: callManager.isSpeaking)

                Text("JARVIS")
                    .font(.title2)
                    .fontWeight(.semibold)
                    .foregroundStyle(.white)
                    .padding(.top, 16)

                // Status / timer
                CallTimerView(manager: callManager)
                    .padding(.top, 4)
                Text(callManager.status)
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.5))
                    .padding(.top, 2)

                // Live transcript
                if !callManager.transcript.isEmpty {
                    Text(callManager.transcript)
                        .font(.body)
                        .foregroundStyle(.white.opacity(0.75))
                        .padding(.horizontal, 32)
                        .padding(.top, 16)
                        .lineLimit(3)
                        .multilineTextAlignment(.center)
                }

                Spacer()

                // Call controls grid
                LazyVGrid(columns: [GridItem(.flexible()), GridItem(.flexible()), GridItem(.flexible())], spacing: 24) {
                    CallButton(icon: callManager.isMuted ? "mic.slash.fill" : "mic.fill",
                               label: callManager.isMuted ? "unmute" : "mute",
                               isActive: callManager.isMuted) {
                        callManager.toggleMute()
                    }
                    CallButton(icon: "speaker.wave.2.fill", label: "speaker",
                               isActive: callManager.speakerOn) {
                        callManager.toggleSpeaker()
                    }
                    CallButton(icon: "keyboard.fill", label: "keypad") {
                        // keypad placeholder
                    }
                }
                .padding(.horizontal, 48)

                // End call
                Button {
                    callManager.endCall()
                    dismiss()
                } label: {
                    Image(systemName: "phone.down.fill")
                        .font(.system(size: 28))
                        .foregroundStyle(.white)
                        .padding(22)
                        .background(Color.red, in: Circle())
                }
                .padding(.top, 36)
                .padding(.bottom, 48)
            }
        }
        .onAppear { callManager.startCall() }
        .onDisappear { callManager.endCall() }
    }
}

private struct CallButton: View {
    let icon: String
    let label: String
    var isActive = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: icon)
                    .font(.system(size: 24))
                    .foregroundStyle(isActive ? .black : .white)
                    .frame(width: 64, height: 64)
                    .background(isActive ? Color.white : Color.white.opacity(0.15), in: Circle())
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.8))
            }
        }
    }
}

/// Isolated avatar so the speaking pulse doesn't re-render with the timer.
private struct AvatarView: View {
    let isSpeaking: Bool
    @State private var pulsing = false

    var body: some View {
        ZStack {
            Circle()
                .fill(Color.blue.opacity(0.25))
                .frame(width: 110, height: 110)
            if isSpeaking {
                Circle()
                    .stroke(Color.blue.opacity(0.7), lineWidth: 2)
                    .frame(width: 110, height: 110)
                    .scaleEffect(pulsing ? 1.12 : 1.0)
                    .opacity(pulsing ? 0.5 : 0.8)
                    .animation(.easeInOut(duration: 0.9), value: pulsing)
                    .onAppear {
                        pulsing = true
                    }
                    .onChange(of: isSpeaking) { _, new in
                        pulsing = new
                    }
            }
            Text("J")
                .font(.system(size: 44, weight: .medium))
                .foregroundStyle(.white)
        }
    }
}

/// Isolated timer so only this text updates every second.
private struct CallTimerView: View {
    @ObservedObject var manager: CallManager

    var body: some View {
        Text(manager.callTimeFormatted)
            .font(.body)
            .foregroundStyle(.white.opacity(0.7))
            .monospacedDigit()
    }
}
