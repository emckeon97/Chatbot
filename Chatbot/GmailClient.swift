import Foundation

/// Gmail API via the GoogleAuthService token. Mirrors the bridge's email commands.
final class GmailClient {
    static let shared = GmailClient()

    struct Email: Identifiable {
        let id: String
        let from: String
        let subject: String
        let date: String
        let snippet: String
    }

    func parse(_ text: String) -> (String, String)? {
        let low = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        guard low.contains("gmail") || low.contains("email") || low.contains("mail") || low.contains("inbox") else { return nil }
        // Don't hijack outlook/icloud requests
        if low.contains("outlook") || low.contains("icloud") { return nil }
        if low.contains("check") || low.contains("new") || low.contains("latest") || low.contains("inbox") || low.contains("unread") {
            return ("list", "")
        }
        if let m = low.range(of: #"^(read|open)\s+(\d+)$"#, options: .regularExpression) {
            _ = m
            let num = low.components(separatedBy: .whitespaces).last ?? "1"
            return ("read", num)
        }
        return nil
    }

    private func token() async -> String? {
        await GoogleAuthService.shared.accessToken
    }

    private func api(_ path: String, method: String = "GET", body: Data? = nil) async throws -> [String: Any] {
        guard let t = await token() else { throw GmailClientError.notSignedIn }
        guard let url = URL(string: "https://gmail.googleapis.com/gmail/v1/users/me/\(path)") else {
            throw URLError(.badURL)
        }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(t)", forHTTPHeaderField: "Authorization")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = body
        }
        let (data, _) = try await URLSession.shared.data(for: req)
        return (try? JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:]
    }

    func listInbox(_ max: Int = 5) async -> [Email] {
        do {
            let list = try await api("messages?maxResults=\(max)&q=in:inbox")
            guard let items = list["messages"] as? [[String: Any]] else { return [] }
            var out: [Email] = []
            for item in items.prefix(max) {
                guard let id = item["id"] as? String else { continue }
                let msg = try await api("messages/\(id)?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
                let payload = msg["payload"] as? [String: Any]
                let headers = (payload?["headers"] as? [[String: Any]]) ?? []
                var from = "?", subject = "(no subject)", date = ""
                for h in headers {
                    let n = (h["name"] as? String ?? "").lowercased()
                    let v = h["value"] as? String ?? ""
                    if n == "from" { from = v }
                    else if n == "subject" { subject = v }
                    else if n == "date" { date = v }
                }
                out.append(Email(id: id, from: from, subject: subject, date: date,
                                 snippet: msg["snippet"] as? String ?? ""))
            }
            return out
        } catch { return [] }
    }

    func readBody(id: String) async -> String? {
        do {
            let msg = try await api("messages/\(id)?format=full")
            guard let payload = msg["payload"] as? [String: Any] else { return nil }
            return extractBody(payload)
        } catch { return nil }
    }

    private func extractBody(_ payload: [String: Any]) -> String? {
        func decode(_ s: String) -> String? {
            var b64 = s.replacingOccurrences(of: "-", with: "+").replacingOccurrences(of: "_", with: "/")
            while b64.count % 4 != 0 { b64 += "=" }
            guard let d = Data(base64Encoded: b64) else { return nil }
            return String(data: d, encoding: .utf8)
        }
        if let body = payload["body"] as? [String: Any], let d = body["data"] as? String,
           let t = decode(d) { return t }
        if let parts = payload["parts"] as? [[String: Any]] {
            for p in parts where (p["mimeType"] as? String ?? "").hasPrefix("text/plain") {
                if let d = (p["body"] as? [String: Any])?["data"] as? String, let t = decode(d) { return t }
            }
        }
        return nil
    }

    func formatList(_ emails: [Email]) -> String {
        if emails.isEmpty { return "Inbox is empty, sir. Enjoy the silence." }
        let lines = emails.enumerated().map { i, e in
            let short = e.from.split(separator: "<").first.map(String.init)?.trimmingCharacters(in: .whitespaces).trimmingCharacters(in: CharacterSet(charactersIn: "\"")) ?? e.from
            return "\(i + 1). \(short): \(e.subject)"
        }
        return "Latest emails, sir:\n" + lines.joined(separator: "\n") + "\n\nSay 'read 2' to open one."
    }
}

enum GmailClientError: Error {
    case notSignedIn
}
