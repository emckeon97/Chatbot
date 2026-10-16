import Foundation
import UserNotifications

/// Handles AI-initiated contact: scheduled texts via the Mac bridge,
/// scheduled calls via local notifications.
@MainActor
final class ProactiveService: ObservableObject {
    static let shared = ProactiveService()

    private var schedulerURL: URL? {
        let settings = UserDefaults.standard
        var server = (settings.string(forKey: "serverURL") ?? "http://192.168.0.102:11434")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        if !server.hasPrefix("http://") && !server.hasPrefix("https://") {
            server = "http://" + server
        }
        // Scheduler runs on port 11435, same host as Ollama
        guard let ollamaURL = URL(string: server),
              let host = ollamaURL.host else { return nil }
        let scheme = ollamaURL.scheme ?? "http"
        return URL(string: "\(scheme)://\(host):11435/schedule_text")
    }

    private init() {
        requestNotificationPermission()
    }

    private func requestNotificationPermission() {
        UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]) { _, _ in }
    }

    // MARK: - Scheduled texts (via Mac bridge)

    /// Schedule a text to be sent by the bridge at the given date.
    /// - Parameter recurring: "daily", "weekly", or nil for one-time.
    func scheduleText(message: String, at date: Date, recurring: String? = nil) async throws {
        guard let url = schedulerURL else { throw ProactiveError.noScheduler }
        var payload: [String: Any] = [
            "message": message,
            "at": date.timeIntervalSince1970
        ]
        if let recurring { payload["recurring"] = recurring }
        let body = try JSONSerialization.data(withJSONObject: payload)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = body
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        if let http = response as? HTTPURLResponse, !(200...299).contains(http.statusCode) {
            throw ProactiveError.scheduleFailed
        }
        if let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           (json["ok"] as? Bool) != true {
            throw ProactiveError.scheduleFailed
        }
    }

    // MARK: - Scheduled calls (local notification)

    /// Schedule a local notification that opens the call UI.
    func scheduleCall(at date: Date, message: String = "Time for your AI call") async throws {
        let content = UNMutableNotificationContent()
        content.title = "AI Call Reminder"
        content.body = message
        content.sound = .default
        content.userInfo = ["action": "startCall"]

        let trigger = UNCalendarNotificationTrigger(
            dateMatching: Calendar.current.dateComponents([.year, .month, .day, .hour, .minute], from: date),
            repeats: false
        )
        let request = UNNotificationRequest(
            identifier: "aicall-\(date.timeIntervalSince1970)",
            content: content,
            trigger: trigger
        )
        try await UNUserNotificationCenter.current().add(request)
    }

    // MARK: - Natural language parsing

    /// Parse "text me in X minutes/hours", "text me every day at 9am", etc.
    /// Returns (action, message, date, recurring) if matched, nil otherwise.
    func parseScheduleRequest(_ input: String) -> (action: ScheduleAction, message: String, date: Date, recurring: String?)? {
        let lower = input.lowercased()

        // Determine action
        let isText = lower.contains("text me")
        let isCall = lower.contains("call me")
        let isRemind = lower.contains("remind me") || lower.contains("notify me")
        guard isText || isCall || isRemind else { return nil }
        let action: ScheduleAction
        if isText { action = .text }
        else if isCall { action = .call }
        else { action = .notify }

        // Detect recurring: "every day", "every morning", "daily", "every week", "weekly"
        var recurring: String?
        if lower.contains("every day") || lower.contains("daily") || lower.contains("every morning") {
            recurring = "daily"
        } else if lower.contains("every week") || lower.contains("weekly") {
            recurring = "weekly"
        }

        // Parse time: "in X minutes", "in X hours", "at 9am", "tomorrow at 9am", "every morning"
        var date: Date?
        if let minutes = extractNumber(after: "in", before: "minute", in: lower) {
            date = Date().addingTimeInterval(Double(minutes) * 60)
        } else if let hours = extractNumber(after: "in", before: "hour", in: lower) {
            date = Date().addingTimeInterval(Double(hours) * 3600)
        } else if let timeDate = parseAtTime(in: lower) {
            date = timeDate
        } else if lower.contains("every morning") {
            // Default to 8am
            var components = Calendar.current.dateComponents([.year, .month, .day], from: Date())
            components.hour = 8
            components.minute = 0
            if let morning = Calendar.current.date(from: components) {
                date = morning < Date() ? Calendar.current.date(byAdding: .day, value: 1, to: morning) : morning
            }
        }
        guard let targetDate = date else { return nil }

        // Extract message: after ":" or after the time expression
        var message = ""
        if let colonRange = input.range(of: ":") {
            message = String(input[colonRange.upperBound...]).trimmingCharacters(in: .whitespacesAndNewlines)
        }
        if message.isEmpty {
            message = isText ? "Scheduled reminder" : "Time for your AI call"
        }

        return (action, message, targetDate, recurring)
    }

    private func extractNumber(after: String, before: String, in text: String) -> Int? {
        guard let afterRange = text.range(of: after),
              let beforeRange = text.range(of: before, range: afterRange.upperBound..<text.endIndex) else {
            return nil
        }
        let numberStr = text[afterRange.upperBound..<beforeRange.lowerBound].trimmingCharacters(in: .whitespaces)
        return Int(numberStr)
    }

    private func parseAtTime(in text: String) -> Date? {
        // Simple "at 9am" / "at 2:30pm" parser
        let pattern = #"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)"#
        guard let regex = try? NSRegularExpression(pattern: pattern),
              let match = regex.firstMatch(in: text, range: NSRange(text.startIndex..., in: text)) else {
            return nil
        }
        guard let hourRange = Range(match.range(at: 1), in: text),
              let hour = Int(text[hourRange]) else { return nil }
        var minute = 0
        if match.range(at: 2).location != NSNotFound,
           let minRange = Range(match.range(at: 2), in: text) {
            minute = Int(text[minRange]) ?? 0
        }
        guard let ampmRange = Range(match.range(at: 3), in: text) else { return nil }
        let ampm = String(text[ampmRange])
        var h = hour
        if ampm == "pm", h != 12 { h += 12 }
        if ampm == "am", h == 12 { h = 0 }

        var components = Calendar.current.dateComponents([.year, .month, .day], from: Date())
        components.hour = h
        components.minute = minute
        guard var date = Calendar.current.date(from: components) else { return nil }
        // If time already passed today, schedule for tomorrow
        if date < Date() {
            date = Calendar.current.date(byAdding: .day, value: 1, to: date) ?? date
        }
        // If "tomorrow" explicitly mentioned, push to tomorrow
        if text.contains("tomorrow") {
            if let tomorrowDate = Calendar.current.date(byAdding: .day, value: 1, to: date) {
                return tomorrowDate
            }
        }
        return date
    }
}

enum ScheduleAction {
    case text, call, notify
}

enum ProactiveError: Error, LocalizedError {
    case noScheduler, scheduleFailed
    var errorDescription: String? {
        switch self {
        case .noScheduler: return "Could not reach the scheduler."
        case .scheduleFailed: return "Failed to schedule."
        }
    }
}
