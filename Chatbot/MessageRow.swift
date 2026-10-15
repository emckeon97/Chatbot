import SwiftUI
import UIKit

struct MessageRow: View {
    let message: ChatMessage

    var body: some View {
        HStack {
            if message.isUser { Spacer(minLength: 56) }
            Text(message.content)
                .textSelection(.enabled)
                .padding(.horizontal, 13)
                .padding(.vertical, 9)
                .background(bubble, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
                .foregroundStyle(message.isUser ? Color.white : Color.primary)
                .contextMenu {
                    ShareLink(item: message.content)
                    Button {
                        UIPasteboard.general.string = message.content
                    } label: {
                        Label("Copy", systemImage: "doc.on.doc")
                    }
                }
            if !message.isUser { Spacer(minLength: 56) }
        }
    }

    private var bubble: some ShapeStyle {
        message.isUser ? AnyShapeStyle(Color.accentColor) : AnyShapeStyle(Color(uiColor: .secondarySystemGroupedBackground))
    }
}
