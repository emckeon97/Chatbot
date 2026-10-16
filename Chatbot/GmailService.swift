import Foundation
import GoogleSignIn
import UIKit

/// Gmail API service. Requires GoogleSignIn SDK added via SPM in Xcode.
/// Add the reversed client ID as a URL scheme in Info.plist.
@MainActor
final class GmailService: ObservableObject {
    static let shared = GmailService()

    @Published private(set) var isSignedIn = false
    @Published private(set) var userEmail: String?
    @Published private(set) var messages: [GmailMessage] = []
    @Published private(set) var isLoading = false
    @Published var errorMessage: String?

    private var accessToken: String?

    private init() {
        // Configure Google Sign-In with the client ID
        let config = GIDConfiguration(clientID: "294125895152-cmamrfum6a6eu44gp3nd52h7amtl9q08.apps.googleusercontent.com")
        GIDSignIn.sharedInstance.configuration = config
    }

    // MARK: - Auth

    func signIn() {
        guard let rootVC = UIApplication.shared.connectedScenes
            .compactMap({ $0 as? UIWindowScene })
            .flatMap({ $0.windows })
            .first(where: { $0.isKeyWindow })?.rootViewController else {
            errorMessage = "Could not find presenting view controller."
            return
        }
        GIDSignIn.sharedInstance.signIn(
            withPresenting: rootVC,
            hint: nil,
            additionalScopes: [
                "https://www.googleapis.com/auth/gmail.readonly",
                "https://www.googleapis.com/auth/gmail.send"
            ]
        ) { [weak self] result, error in
            Task { @MainActor in
                if let error {
                    self?.errorMessage = error.localizedDescription
                    return
                }
                guard let user = result?.user,
                      let token = user.accessToken.tokenString as String? else {
                    self?.errorMessage = "Sign-in failed."
                    return
                }
                self?.setAccessToken(token, email: user.profile?.email)
                await self?.fetchInbox()
            }
        }
    }

    func setAccessToken(_ token: String, email: String?) {
        self.accessToken = token
        self.userEmail = email
        self.isSignedIn = true
    }

    func signOut() {
        GIDSignIn.sharedInstance.signOut()
        accessToken = nil
        userEmail = nil
        isSignedIn = false
        messages = []
    }

    /// Restore previous sign-in silently.
    func restoreSignIn() {
        GIDSignIn.sharedInstance.restorePreviousSignIn { [weak self] user, error in
            Task { @MainActor in
                if let user, let token = user.accessToken.tokenString as String? {
                    self?.setAccessToken(token, email: user.profile?.email)
                    await self?.fetchInbox()
                }
            }
        }
    }

    // MARK: - API

    private func authorizedRequest(url: URL, method: String = "GET", body: Data? = nil) -> URLRequest? {
        guard let token = accessToken else { return nil }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let body {
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = body
        }
        return request
    }

    /// Fetch recent inbox messages (metadata only).
    func fetchInbox(maxResults: Int = 20) async {
        guard accessToken != nil else { return }
        isLoading = true
        defer { isLoading = false }
        do {
            guard let listURL = URL(string: "https://gmail.googleapis.com/gmail/v1/users/me/messages?maxResults=\(maxResults)&q=in:inbox"),
                  let listRequest = authorizedRequest(url: listURL) else { return }
            let (listData, _) = try await URLSession.shared.data(for: listRequest)
            guard let listJson = try? JSONSerialization.jsonObject(with: listData) as? [String: Any],
                  let items = listJson["messages"] as? [[String: Any]] else { return }

            var fetched: [GmailMessage] = []
            for item in items.prefix(maxResults) {
                guard let id = item["id"] as? String,
                      let msgURL = URL(string: "https://gmail.googleapis.com/gmail/v1/users/me/messages/\(id)?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date"),
                      let msgRequest = authorizedRequest(url: msgURL) else { continue }
                let (msgData, _) = try await URLSession.shared.data(for: msgRequest)
                if let msg = GmailMessage(from: msgData, id: id) {
                    fetched.append(msg)
                }
            }
            messages = fetched
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    /// Fetch the full body of a message.
    func fetchBody(for message: GmailMessage) async -> String? {
        guard let url = URL(string: "https://gmail.googleapis.com/gmail/v1/users/me/messages/\(message.id)?format=full"),
              let request = authorizedRequest(url: url) else { return nil }
        do {
            let (data, _) = try await URLSession.shared.data(for: request)
            return GmailMessage.extractBody(from: data)
        } catch {
            return nil
        }
    }

    /// Send an email.
    func send(to: String, subject: String, body: String) async throws {
        let raw = "To: \(to)\r\nSubject: \(subject)\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n\(body)"
        guard let rawData = raw.data(using: .utf8) else { throw GmailError.encodingFailed }
        let base64 = rawData.base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
        let payload = try JSONSerialization.data(withJSONObject: ["raw": base64])
        guard let url = URL(string: "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"),
              let request = authorizedRequest(url: url, method: "POST", body: payload) else {
            throw GmailError.notSignedIn
        }
        let (_, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw GmailError.sendFailed(http.statusCode)
        }
    }
}

enum GmailError: Error, LocalizedError {
    case notSignedIn, encodingFailed, sendFailed(Int)
    var errorDescription: String? {
        switch self {
        case .notSignedIn: return "Not signed in to Gmail."
        case .encodingFailed: return "Could not encode message."
        case .sendFailed(let code): return "Send failed (HTTP \(code))."
        }
    }
}

struct GmailMessage: Identifiable {
    let id: String
    let from: String
    let subject: String
    let date: String
    let snippet: String

    init?(from data: Data, id: String) {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        self.id = id
        self.snippet = (json["snippet"] as? String) ?? ""
        var from = "", subject = "", date = ""
        if let payload = json["payload"] as? [String: Any],
           let headers = payload["headers"] as? [[String: Any]] {
            for h in headers {
                let name = (h["name"] as? String) ?? ""
                let value = (h["value"] as? String) ?? ""
                switch name.lowercased() {
                case "from": from = value
                case "subject": subject = value
                case "date": date = value
                default: break
                }
            }
        }
        self.from = from
        self.subject = subject
        self.date = date
    }

    static func extractBody(from data: Data) -> String? {
        guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let payload = json["payload"] as? [String: Any] else { return nil }
        // Try body.data directly, then parts
        if let body = payload["body"] as? [String: Any],
           let b64 = body["data"] as? String,
           let decoded = decodeBase64URL(b64) {
            return decoded
        }
        if let parts = payload["parts"] as? [[String: Any]] {
            for part in parts {
                if let mime = part["mimeType"] as? String, mime.hasPrefix("text/plain"),
                   let body = part["body"] as? [String: Any],
                   let b64 = body["data"] as? String,
                   let decoded = decodeBase64URL(b64) {
                    return decoded
                }
            }
        }
        return nil
    }

    private static func decodeBase64URL(_ s: String) -> String? {
        var b64 = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
        while b64.count % 4 != 0 { b64 += "=" }
        guard let data = Data(base64Encoded: b64) else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
