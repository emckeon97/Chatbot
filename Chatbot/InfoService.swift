import Foundation

/// Free info connectors: stocks (Stooq), crypto (CoinGecko), news (RSS), dictionary.
/// No API keys needed.
final class InfoService {
    static let shared = InfoService()

    // MARK: - Parse

    /// Returns ("stock", symbol) | ("crypto", id) | ("news", "") | ("define", word)
    func parse(_ text: String) -> (String, String)? {
        let low = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)

        // "stock AAPL" / "price of TSLA" / "AAPL stock"
        if low.hasPrefix("stock ") {
            return ("stock", String(low.dropFirst(6)).trimmingCharacters(in: .whitespaces).uppercased())
        }
        if low.hasPrefix("price of ") {
            let sym = String(low.dropFirst(9)).trimmingCharacters(in: .whitespaces).uppercased()
            if sym.count <= 5 { return ("stock", sym) }
        }
        if let m = low.range(of: #"^([a-z]{1,5}) stock$"#, options: .regularExpression) {
            _ = m
            let sym = low.replacingOccurrences(of: " stock", with: "").uppercased()
            return ("stock", sym)
        }

        // "crypto bitcoin" / "BTC price" / "price of ethereum"
        if low.hasPrefix("crypto ") {
            return ("crypto", String(low.dropFirst(7)).trimmingCharacters(in: .whitespaces))
        }
        let cryptoNames = ["bitcoin": "bitcoin", "btc": "bitcoin", "ethereum": "ethereum", "eth": "ethereum",
                           "dogecoin": "dogecoin", "doge": "dogecoin", "solana": "solana", "sol": "solana"]
        for (alias, id) in cryptoNames {
            if low == "\(alias) price" || low == "price of \(alias)" { return ("crypto", id) }
        }

        // "news" / "headlines" / "top news"
        if low == "news" || low == "headlines" || low == "top news" || low == "latest news" {
            return ("news", "")
        }

        // "define serendipity" / "meaning of ..."
        if low.hasPrefix("define ") {
            return ("define", String(low.dropFirst(7)).trimmingCharacters(in: .whitespaces))
        }
        if low.hasPrefix("meaning of ") {
            return ("define", String(low.dropFirst(11)).trimmingCharacters(in: .whitespaces))
        }
        return nil
    }

    // MARK: - Stocks (Stooq free CSV)

    func stock(_ symbol: String) async -> String {
        let sym = symbol.hasSuffix(".US") ? symbol : symbol + ".US"
        guard let url = URL(string: "https://stooq.com/q/l/?s=\(sym.lowercased())&f=sd2t2ohlcv&h&e=csv"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let csv = String(data: data, encoding: .utf8) else {
            return "Couldn't fetch that stock."
        }
        let lines = csv.components(separatedBy: "\n").filter { !$0.isEmpty }
        guard lines.count >= 2 else { return "No data for \(symbol)." }
        let vals = lines[1].components(separatedBy: ",")
        guard vals.count >= 7, let price = Double(vals[6]), price > 0 else {
            return "No data for \(symbol)."
        }
        let prev = Double(vals[3]) ?? price
        let chg = price - prev
        let pct = prev != 0 ? chg / prev * 100 : 0
        let arrow = chg >= 0 ? "▲" : "▼"
        return "\(symbol): $\(String(format: "%.2f", price)) \(arrow) \(String(format: "%.2f", abs(chg))) (\(String(format: "%.2f", abs(pct)))%)"
    }

    // MARK: - Crypto (CoinGecko free)

    func crypto(_ id: String) async -> String {
        guard let url = URL(string: "https://api.coingecko.com/api/v3/simple/price?ids=\(id)&vs_currencies=usd&include_24hr_change=true"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let coin = json[id] as? [String: Any],
              let price = coin["usd"] as? Double else {
            return "Couldn't fetch that crypto."
        }
        let chg = coin["usd_24h_change"] as? Double ?? 0
        let arrow = chg >= 0 ? "▲" : "▼"
        let name = id.prefix(1).uppercased() + id.dropFirst()
        if price < 1 {
            return "\(name): $\(String(format: "%.4f", price)) \(arrow) \(String(format: "%.2f", abs(chg)))% (24h)"
        }
        return "\(name): $\(String(format: "%.2f", price)) \(arrow) \(String(format: "%.2f", abs(chg)))% (24h)"
    }

    // MARK: - News (BBC RSS)

    func news() async -> String {
        guard let url = URL(string: "https://feeds.bbci.co.uk/news/rss.xml"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let xml = String(data: data, encoding: .utf8) else {
            return "Couldn't fetch headlines."
        }
        var titles: [String] = []
        var searchFrom = xml.startIndex
        while titles.count < 5,
              let s = xml.range(of: "<title>", range: searchFrom..<xml.endIndex),
              let e = xml.range(of: "</title>", range: s.upperBound..<xml.endIndex) {
            var t = String(xml[s.upperBound..<e.lowerBound])
            t = t.replacingOccurrences(of: "<![CDATA[", with: "").replacingOccurrences(of: "]]>", with: "")
            // Skip the feed title itself
            if !t.lowercased().contains("bbc news") { titles.append(t.trimmingCharacters(in: .whitespacesAndNewlines)) }
            searchFrom = e.upperBound
        }
        if titles.isEmpty { return "No headlines right now." }
        return "Top headlines:\n" + titles.enumerated().map { "\($0 + 1). \($1)" }.joined(separator: "\n")
    }

    // MARK: - Dictionary (dictionaryapi.dev)

    func define(_ word: String) async -> String {
        let w = word.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? word
        guard let url = URL(string: "https://api.dictionaryapi.dev/api/v2/entries/en/\(w)"),
              let (data, _) = try? await URLSession.shared.data(from: url),
              let arr = try? JSONSerialization.jsonObject(with: data) as? [[String: Any]],
              let entry = arr.first,
              let meanings = entry["meanings"] as? [[String: Any]],
              let first = meanings.first,
              let defs = first["definitions"] as? [[String: Any]],
              let def = defs.first?["definition"] as? String else {
            return "No definition found for '\(word)'."
        }
        let pos = first["partOfSpeech"] as? String ?? ""
        return "\(word)\(pos.isEmpty ? "" : " (\(pos))"): \(def)"
    }
}
