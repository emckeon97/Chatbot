import Foundation
import EventKit

/// On-device calendar via EventKit. "what's on my calendar" / "add event ...".
final class CalendarService: ObservableObject {
    static let shared = CalendarService()
    private let store = EKEventStore()

    /// Returns a query string if text is a calendar request, else nil.
    /// Returns ("list", "") for listing, ("add", rest) for adding.
    func parse(_ text: String) -> (String, String)? {
        let low = text.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        if low.contains("what's on my calendar") || low.contains("whats on my calendar") ||
           low.contains("my schedule") || low == "calendar" {
            return ("list", "")
        }
        for prefix in ["add event ", "add to calendar ", "schedule "] {
            if low.hasPrefix(prefix) {
                return ("add", String(text[text.index(text.startIndex, offsetBy: prefix.count)...]))
            }
        }
        return nil
    }

    private func ensureAccess() async -> Bool {
        let status = EKEventStore.authorizationStatus(for: .event)
        switch status {
        case .authorized, .fullAccess: return true
        case .notDetermined:
            return (try? await store.requestFullAccessToEvents()) ?? false
        default: return false
        }
    }

    func listToday() async -> String {
        guard await ensureAccess() else { return "Calendar access not granted. Enable it in Settings > Privacy." }
        let start = Calendar.current.startOfDay(for: Date())
        let end = Calendar.current.date(byAdding: .day, value: 1, to: start)!
        let pred = store.predicateForEvents(withStart: start, end: end, calendars: nil)
        let events = store.events(matching: pred).sorted { $0.startDate < $1.startDate }
        if events.isEmpty { return "Nothing on your calendar today." }
        let fmt = DateFormatter()
        fmt.timeStyle = .short
        return "Today:\n" + events.map { "- \(fmt.string(from: $0.startDate)): \($0.title ?? "(no title)")" }.joined(separator: "\n")
    }

    /// Parse "Dentist tomorrow at 3pm" — best effort.
    func addEvent(_ text: String) async -> String {
        guard await ensureAccess() else { return "Calendar access not granted. Enable it in Settings > Privacy." }
        // Simple parse: look for time like "3pm" / "15:00", date like "tomorrow"/"today"/weekday
        let low = text.lowercased()
        var date = Date()
        if low.contains("tomorrow") {
            date = Calendar.current.date(byAdding: .day, value: 1, to: Date()) ?? Date()
        }
        // Time
        let timePattern = try? NSRegularExpression(pattern: #"(\d{1,2})(?::(\d{2}))?\s*(am|pm)"#)
        if let m = timePattern?.firstMatch(in: low, range: NSRange(low.startIndex..., in: low)) {
            var hour = Int((low as NSString).substring(with: m.range(at: 1))) ?? 9
            let minute = m.range(at: 2).location != NSNotFound ? Int((low as NSString).substring(with: m.range(at: 2))) ?? 0 : 0
            let ampm = (low as NSString).substring(with: m.range(at: 3))
            if ampm == "pm", hour < 12 { hour += 12 }
            if ampm == "am", hour == 12 { hour = 0 }
            var comps = Calendar.current.dateComponents([.year, .month, .day], from: date)
            comps.hour = hour; comps.minute = minute
            date = Calendar.current.date(from: comps) ?? date
        }
        // Title = text minus date/time words
        var title = text
        for w in ["tomorrow", "today"] { title = title.replacingOccurrences(of: w, with: "", options: .caseInsensitive) }
        if let r = timePattern?.firstMatch(in: title, range: NSRange(title.startIndex..., in: title)) {
            title = (title as NSString).replacingCharacters(in: r.range, with: "")
        }
        title = title.trimmingCharacters(in: .whitespacesAndNewlines)
        if title.isEmpty { title = "Event" }

        let event = EKEvent(eventStore: store)
        event.title = title
        event.startDate = date
        event.endDate = date.addingTimeInterval(3600)
        event.calendar = store.defaultCalendarForNewEvents
        do {
            try store.save(event, span: .thisEvent)
            let fmt = DateFormatter()
            fmt.dateStyle = .medium; fmt.timeStyle = .short
            return "Added '\(title)' on \(fmt.string(from: date))."
        } catch {
            return "Couldn't save that event."
        }
    }
}
