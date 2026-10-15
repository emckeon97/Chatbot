import SwiftUI

struct ConversationsView: View {
    @ObservedObject private var store = ChatStore.shared

    var body: some View {
        NavigationStack {
            Group {
                if store.conversations.isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "bubble.left.and.text.bubble.right")
                            .font(.system(size: 44, weight: .light))
                            .foregroundStyle(.tint)
                        Text("No chats yet")
                            .font(.headline)
                        Text("Tap the pencil to start one.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else {
                    List {
                        ForEach(store.conversations) { convo in
                            NavigationLink(value: convo.id) {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(convo.title.isEmpty ? "New chat" : convo.title)
                                        .fontWeight(.medium)
                                        .lineLimit(1)
                                    Text(convo.createdAt, style: .date)
                                        .font(.footnote)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                Button(role: .destructive) {
                                    store.deleteConversation(convo.id)
                                } label: {
                                    Label("Delete", systemImage: "trash")
                                }
                            }
                        }
                    }
                }
            }
            .navigationTitle("Chats")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    NavigationLink {
                        ChatView(conversationID: nil)
                    } label: {
                        Image(systemName: "square.and.pencil")
                    }
                }
            }
            .navigationDestination(for: UUID.self) { id in
                ChatView(conversationID: id)
            }
        }
    }
}
