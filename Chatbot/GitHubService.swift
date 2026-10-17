import Foundation
import Security

/// GitHub API via personal access token (stored in Keychain).
/// Create a token at github.com/settings/tokens with `repo` scope.
final class GitHubService {
    static let shared = GitHubService()
    private let keychainKey = "chatbot.github.pat"

    var hasToken: Bool { loadToken() != nil }

    func saveToken(_ token: String) {
        let data = Data(token.utf8)
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrAccount: keychainKey] as CFDictionary)
        SecItemAdd([kSecClass: kSecClassGenericPassword,
                    kSecAttrAccount: keychainKey,
                    kSecValueData: data] as CFDictionary, nil)
    }

    func clearToken() {
        SecItemDelete([kSecClass: kSecClassGenericPassword, kSecAttrAccount: keychainKey] as CFDictionary)
    }

    private func loadToken() -> String? {
        var result: AnyObject?
        let status = SecItemCopyMatching(
            [kSecClass: kSecClassGenericPassword,
             kSecAttrAccount: keychainKey,
             kSecReturnData: true] as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }

    private func request(_ path: String, method: String = "GET", body: [String: Any]? = nil) async throws -> Any {
        guard let token = loadToken() else { throw GitHubError.noToken }
        guard let url = URL(string: "https://api.github.com/\(path)") else { throw URLError(.badURL) }
        var req = URLRequest(url: url)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        req.setValue("Chatbot-iOS", forHTTPHeaderField: "User-Agent")
        if let body {
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.httpBody = try JSONSerialization.data(withJSONObject: body)
        }
        let (data, resp) = try await URLSession.shared.data(for: req)
        if let http = resp as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            let msg = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["message"] as? String ?? "HTTP \(http.statusCode)"
            throw GitHubError.api(msg)
        }
        return try JSONSerialization.jsonObject(with: data)
    }

    func listRepos() async -> String {
        do {
            let json = try await request("user/repos?per_page=50&sort=updated") as? [[String: Any]] ?? []
            if json.isEmpty { return "No repos found." }
            return json.compactMap { r -> String? in
                guard let name = r["full_name"] as? String else { return nil }
                let desc = (r["description"] as? String ?? "").prefix(60)
                return "- \(name)\(desc.isEmpty ? "" : ": \(desc)")"
            }.joined(separator: "\n")
        } catch { return "Failed: \(error.localizedDescription)" }
    }

    func readFile(repo: String, path: String, ref: String = "main") async -> String {
        do {
            let json = try await request("repos/\(repo)/contents/\(path)?ref=\(ref)") as? [String: Any] ?? [:]
            guard let b64 = json["content"] as? String,
                  let data = Data(base64Encoded: b64.replacingOccurrences(of: "\n", with: "")),
                  let text = String(data: data, encoding: .utf8) else {
                return "Couldn't read that file."
            }
            if text.count > 8000 { return String(text.prefix(8000)) + "\n... (truncated)" }
            return text
        } catch { return "Failed: \(error.localizedDescription)" }
    }

    func writeFile(repo: String, path: String, content: String, message: String, branch: String = "main") async -> String {
        do {
            // Get current SHA if file exists
            var sha: String?
            if let json = try? await request("repos/\(repo)/contents/\(path)?ref=\(branch)") as? [String: Any] {
                sha = json["sha"] as? String
            }
            var body: [String: Any] = [
                "message": message,
                "content": Data(content.utf8).base64EncodedString(),
                "branch": branch,
            ]
            if let sha { body["sha"] = sha }
            _ = try await request("repos/\(repo)/contents/\(path)", method: "PUT", body: body)
            return "Pushed to \(repo)/\(path) on \(branch), sir."
        } catch { return "Failed: \(error.localizedDescription)" }
    }

    func listCommits(repo: String, count: Int = 5) async -> String {
        do {
            let json = try await request("repos/\(repo)/commits?per_page=\(count)") as? [[String: Any]] ?? []
            if json.isEmpty { return "No commits found." }
            return json.compactMap { c -> String? in
                guard let sha = (c["sha"] as? String)?.prefix(7),
                      let msg = (c["commit"] as? [String: Any])?["message"] as? String else { return nil }
                return "\(sha) - \(msg.components(separatedBy: "\n").first ?? "")"
            }.joined(separator: "\n")
        } catch { return "Failed: \(error.localizedDescription)" }
    }
}

enum GitHubError: Error, LocalizedError {
    case noToken
    case api(String)
    var errorDescription: String? {
        switch self {
        case .noToken: return "GitHub token not set. Add one in Settings."
        case .api(let m): return m
        }
    }
}
