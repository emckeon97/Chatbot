import Foundation

/// Web search via DuckDuckGo. No API key required.
/// Say "search for..." or "look up..." to trigger.
final class WebSearchService {
    static let shared = WebSearchService()

    private init() {}

    struct SearchResult {
        let title: String
        let snippet: String
        let url: String
    }

    /// Check if input is a search request. Returns the query if so.
    func parseQuery(_ input: String) -> String? {
        let lower = input.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        let prefixes = ["search for ", "search the web for ", "look up ", "google "]
        for prefix in prefixes {
            if lower.hasPrefix(prefix) {
                let query = String(input.dropFirst(prefix.count))
                    .trimmingCharacters(in: .whitespacesAndNewlines)
                return query.isEmpty ? nil : query
            }
        }
        // "what's the latest on..." / "current..." triggers
        if lower.hasPrefix("what's the latest") || lower.hasPrefix("whats the latest") ||
           lower.hasPrefix("current events") {
            return input
        }
        return nil
    }

    /// Perform a search and return formatted results for the LLM.
    func search(_ query: String) async throws -> String {
        // Use DuckDuckGo instant answer API + HTML fallback
        let encoded = query.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) ?? query

        // Try instant answer API first
        if let instant = await fetchInstantAnswer(query: encoded), !instant.isEmpty {
            return instant
        }

        // Fallback: fetch DuckDuckGo HTML results
        return await fetchHTMLResults(query: encoded) ?? "No results found."
    }

    private func fetchInstantAnswer(query: String) async -> String? {
        guard let url = URL(string: "https://api.duckduckgo.com/?q=\(query)&format=json&no_html=1&skip_disambig=1") else {
            return nil
        }
        do {
            let (data, _) = try await URLSession.shared.data(from: url)
            guard let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                return nil
            }
            var parts: [String] = []
            if let abstract = json["AbstractText"] as? String, !abstract.isEmpty {
                parts.append(abstract)
                if let source = json["AbstractSource"] as? String, !source.isEmpty {
                    parts.append("(Source: \(source))")
                }
            }
            if let answer = json["Answer"] as? String, !answer.isEmpty {
                parts.append("Answer: \(answer)")
            }
            if let topics = json["RelatedTopics"] as? [[String: Any]] {
                let snippets = topics.prefix(3).compactMap { $0["Text"] as? String }
                if !snippets.isEmpty {
                    parts.append("Related: " + snippets.joined(separator: " | "))
                }
            }
            return parts.isEmpty ? nil : parts.joined(separator: "\n")
        } catch {
            return nil
        }
    }

    private func fetchHTMLResults(query: String) async -> String? {
        guard let url = URL(string: "https://html.duckduckgo.com/html/?q=\(query)") else {
            return nil
        }
        do {
            var request = URLRequest(url: url)
            request.setValue("Mozilla/5.0", forHTTPHeaderField: "User-Agent")
            let (data, _) = try await URLSession.shared.data(for: request)
            guard let html = String(data: data, encoding: .utf8) else { return nil }
            // Simple extraction of result snippets
            let pattern = #"class="result__snippet"[^>]*>(.*?)</"#
            guard let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) else {
                return nil
            }
            let matches = regex.matches(in: html, range: NSRange(html.startIndex..., in: html))
            let snippets = matches.prefix(5).compactMap { match -> String? in
                guard let range = Range(match.range(at: 1), in: html) else { return nil }
                var text = String(html[range])
                text = text.replacingOccurrences(of: "<[^>]+>", with: "", options: .regularExpression)
                return text.trimmingCharacters(in: .whitespacesAndNewlines)
            }.filter { !$0.isEmpty }
            return snippets.isEmpty ? nil : "Search results:\n" + snippets.map { "- \($0)" }.joined(separator: "\n")
        } catch {
            return nil
        }
    }
}
