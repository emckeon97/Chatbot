import Foundation

/// "Brief me" / "morning briefing" — weather + calendar + headlines in one shot, Jarvis-style.
final class BriefingService {
    static let shared = BriefingService()

    func parse(_ text: String) -> Bool {
        let low = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        return low.contains("brief") || low == "morning update" || low == "daily briefing"
    }

    func build() async -> String {
        async let weather = WeatherService.shared.fetch(location: "")
        async let calendar = CalendarService.shared.listToday()
        async let headlines = InfoService.shared.news()
        let (w, c, n) = await (weather, calendar, headlines)

        let fmt = DateFormatter()
        fmt.dateFormat = "EEEE, MMMM d"
        var out = ["Good morning, sir. Here's your briefing for \(fmt.string(from: Date())).", ""]
        out.append("WEATHER")
        out.append(w)
        out.append("")
        out.append("CALENDAR")
        out.append(c)
        out.append("")
        out.append("HEADLINES")
        // Take first 3 headlines to keep it tight
        let newsLines = n.components(separatedBy: "\n")
        out.append(newsLines.prefix(4).joined(separator: "\n"))
        return out.joined(separator: "\n")
    }
}
