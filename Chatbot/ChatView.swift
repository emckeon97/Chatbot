import SwiftUI

struct ChatView: View {
    @State private var conversationID: UUID?
    @ObservedObject private var store = ChatStore.shared

    init(conversationID: UUID?) {
        _conversationID = State(initialValue: conversationID)
    }

    private var conversation: Conversation? {
        store.conversation(id: conversationID)
    }

    var body: some View {
        VStack(spacing: 0) {
            if (conversation?.messages.isEmpty ?? true) && !store.isGenerating {
                emptyState
            } else {
                ScrollView {
                    LazyVStack(spacing: 10) {
                        ForEach(conversation?.messages ?? []) { message in
                            MessageRow(message: message)
                        }
                        if store.isGenerating && store.generatingIn == conversationID {
                            MessageRow(message: ChatMessage(
                                role: .assistant,
                                content: store.streamingText.isEmpty ? "..." : store.streamingText
                            ))
                        }
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                }
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
            }
            Divider()
            InputBarView(
                isGenerating: store.isGenerating && store.generatingIn == conversationID,
                onSend: { text in
                    conversationID = store.send(text, in: conversationID)
                },
                onStop: {
                    store.stop()
                }
            )
        }
        .background(Color(uiColor: .systemGroupedBackground))
        .navigationBarTitleDisplayMode(.inline)
        .navigationTitle(titleText)
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                Button {
                    store.clearMessages(in: conversationID)
                } label: {
                    Image(systemName: "arrow.uturn.backward.circle")
                }
                .disabled(conversation?.messages.isEmpty ?? true)
                NavigationLink {
                    SettingsView()
                } label: {
                    Image(systemName: "gearshape")
                }
            }
        }
    }

    private var titleText: String {
        if let title = conversation?.title, !title.isEmpty { return title }
        return "Chatbot"
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            Image(systemName: "bubble.left.and.text.bubble.right")
                .font(.system(size: 44, weight: .light))
                .foregroundStyle(.tint)
            Text("Say something to your local llama")
                .font(.headline)
            Text(SettingsView.endpointSummary)
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

}

/// Isolated input bar so keystrokes don't re-render on every streaming update.
private struct InputBarView: View {
    let isGenerating: Bool
    let onSend: (String) -> Void
    let onStop: () -> Void

    @State private var draft = ""
    @FocusState private var isFocused: Bool

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField("Message", text: $draft, axis: .vertical)
                .lineLimit(1...4)
                .padding(10)
                .background(Color(uiColor: .secondarySystemGroupedBackground), in: RoundedRectangle(cornerRadius: 18))
                .focused($isFocused)
            if isGenerating {
                Button {
                    onStop()
                } label: {
                    Image(systemName: "stop.circle.fill")
                        .font(.system(size: 26))
                        .foregroundStyle(.red)
                }
            } else {
                Button {
                    let text = draft
                    draft = ""
                    onSend(text)
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.system(size: 26))
                }
                .disabled(draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .animation(.easeOut(duration: 0.15), value: isGenerating)
    }
}
