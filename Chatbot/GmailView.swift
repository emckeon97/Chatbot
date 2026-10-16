import SwiftUI

/// Gmail inbox and compose UI.
struct GmailView: View {
    @StateObject private var gmail = GmailService.shared
    @State private var showCompose = false
    @State private var selectedMessage: GmailMessage?
    @State private var messageBody: String?
    @State private var showSignInHelp = false

    var body: some View {
        NavigationStack {
            Group {
                if !gmail.isSignedIn {
                    signInPrompt
                } else if gmail.isLoading && gmail.messages.isEmpty {
                    ProgressView("Loading inbox...")
                } else {
                    inboxList
                }
            }
            .navigationTitle("Gmail")
            .toolbar {
                if gmail.isSignedIn {
                    ToolbarItemGroup(placement: .topBarTrailing) {
                        Button { showCompose = true } label: {
                            Image(systemName: "square.and.pencil")
                        }
                        Button { gmail.signOut() } label: {
                            Image(systemName: "rectangle.portrait.and.arrow.right")
                        }
                    }
                }
            }
            .sheet(isPresented: $showCompose) {
                GmailComposeView()
            }
            .sheet(item: $selectedMessage) { message in
                GmailDetailView(message: message)
            }
            .alert("Sign in required", isPresented: $showSignInHelp) {
                Button("OK", role: .cancel) {}
            } message: {
                Text("Add the GoogleSignIn SDK via Swift Package Manager in Xcode, then wire up the sign-in button. See GmailService.swift for instructions.")
            }
            .refreshable {
                await gmail.fetchInbox()
            }
        }
        .task {
            if !gmail.isSignedIn {
                gmail.restoreSignIn()
            } else {
                await gmail.fetchInbox()
            }
        }
    }

    private var signInPrompt: some View {
        VStack(spacing: 16) {
            Image(systemName: "envelope.fill")
                .font(.system(size: 48))
                .foregroundStyle(.blue)
            Text("Connect Gmail")
                .font(.headline)
            Text("Sign in with your Google account to read and send email.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            Button("Sign in with Google") {
                gmail.signIn()
            }
            .buttonStyle(.borderedProminent)
        }
    }

    private var inboxList: some View {
        List(gmail.messages) { message in
            Button { selectedMessage = message } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(message.subject.isEmpty ? "(no subject)" : message.subject)
                        .font(.headline)
                        .lineLimit(1)
                    Text(message.from)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                    Text(message.snippet)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                        .lineLimit(2)
                }
                .padding(.vertical, 4)
            }
        }
    }
}

struct GmailDetailView: View {
    let message: GmailMessage
    @State private var messageBody: String?
    @State private var loading = true

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    Text(message.subject.isEmpty ? "(no subject)" : message.subject)
                        .font(.title2.bold())
                    Text("From: \(message.from)")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                    Text(message.date)
                        .font(.caption)
                        .foregroundStyle(.tertiary)
                    Divider()
                    if loading {
                        ProgressView()
                    } else {
                        Text(messageBody ?? "Could not load message body.")
                            .font(.body)
                    }
                }
                .padding()
            }
            .navigationTitle("Message")
            .navigationBarTitleDisplayMode(.inline)
        }
        .task {
            messageBody = await GmailService.shared.fetchBody(for: message)
            loading = false
        }
    }
}

struct GmailComposeView: View {
    @Environment(\.dismiss) private var dismiss
    @State private var to = ""
    @State private var subject = ""
    @State private var emailBody = ""
    @State private var sending = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            Form {
                TextField("To", text: $to)
                    .keyboardType(.emailAddress)
                    .textInputAutocapitalization(.never)
                TextField("Subject", text: $subject)
                TextEditor(text: $emailBody)
                    .frame(minHeight: 200)
                if let error {
                    Text(error).foregroundStyle(.red).font(.caption)
                }
            }
            .navigationTitle("New Email")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Send") { Task { await send() } }
                        .disabled(to.isEmpty || sending)
                }
            }
        }
    }

    private func send() async {
        sending = true
        defer { sending = false }
        do {
            try await GmailService.shared.send(to: to, subject: subject, body: emailBody)
            dismiss()
        } catch {
            self.error = error.localizedDescription
        }
    }
}
