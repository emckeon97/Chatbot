import Foundation
import UserNotifications

/// Handles AI-initiated contact: scheduled texts via the Mac bridge,
/// scheduled calls via local notifications.
@MainActor
final class ProactiveService: ObservableObject {
    static let shared = ProactiveService()

    private func schedulerURL(path: String) -> URL? {
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
        return URL(string: "\(scheme)://\(host):11435/\(path)")
    }

    private func post(_ payload: [String: Any], path: String) async throws {
        guard let url = schedulerURL(path: path) else { throw ProactiveError.noScheduler }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: payload)
        request.timeoutInterval = 15
        let (data, response) = try await URLSession.shared.data(for: request)
        let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
        guard let http = response as? HTTPURLResponse, (200...299).contains(http.statusCode) else {
            throw ProactiveError.scheduleFailed(json?["error"] as? String ?? "Scheduler returned an error.")
        }
        guard json?["ok"] as? Bool == true else {
            throw ProactiveError.scheduleFailed(json?["error"] as? String ?? "Scheduler did not confirm the request.")
        }
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
        var payload: [String: Any] = ["message": message, "at": date.timeIntervalSince1970]
        if let recurring { payload["recurring"] = recurring }
        try await post(payload, path: "schedule_text")
    }

    /// Schedule an instruction to run with the bridge's tools at the requested time.
    func scheduleTask(instruction: String, at date: Date, recurring: String? = nil) async throws {
        try await post([
            "name": "Scheduled email check",
            "instruction": instruction,
            "at": date.timeIntervalSince1970,
            "recurring": recurring ?? "none"
        ], path: "create_task")
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
        let isText = lower.contains("text me")
        let isCall = lower.contains("call me")
        let isRemind = lower.contains("remind me") || lower.contains("notify me")
        let mentionsEmail = ["email", "mail", "inbox", "gmail", "outlook", "icloud"].contains { lower.contains($0) }

        var recurring: String?
        if lower.contains("every day") || lower.contains("daily") || lower.contains("every morning") {
            recurring = "daily"
        } else if lower.contains("every week") || lower.contains("weekly") {
            recurring = "weekly"
        }
        let hasScheduledTime = recurring != nil || lower.contains("tomorrow") ||
            extractNumber(after: "in", before: "minute", in: lower) != nil ||
            extractNumber(after: "in", before: "hour", in: lower) != nil ||
            parseAtTime(in: lower) != nil
        let isScheduledEmail = mentionsEmail && hasScheduledTime
        guard isText || isCall || isRemind || isScheduledEmail else { return nil }

        let action: ScheduleAction
        if isScheduledEmail { action = .task }
        else if isText { action = .text }
        else if isCall { action = .call }
        else { action = .notify }

        var date: Date?
        if let minutes = extractNumber(after: "in", before: "minute", in: lower) {
            date = Date().addingTimeInterval(Double(minutes) * 60)
        } else if let hours = extractNumber(after: "in", before: "hour", in: lower) {
            date = Date().addingTimeInterval(Double(hours) * 3600)
        } else if let timeDate = parseAtTime(in: lower) {
            date = timeDate
        } else if lower.contains("every morning") {
            var components = Calendar.current.dateComponents([.year, .month, .day], from: Date())
            components.hour = 8
            components.minute = 0
            if let morning = Calendar.current.date(from: components) {
                date = morning < Date() ? Calendar.current.date(byAdding: .day, value: 1, to: morning) : morning
            }
        } else if lower.contains("tomorrow") {
            date = Calendar.current.date(byAdding: .day, value: 1, to: Date())
        }
        guard let targetDate = date else { return nil }

        var message = ""
        if isScheduledEmail {
            message = input.trimmingCharacters(in: .whitespacesAndNewlines)
        } else if let colonRange = input.range(of: ":") {
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
        // Parse a local clock time, honoring an explicit today or tomorrow.
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
        var hour24 = hour
        if ampm == "pm", hour24 != 12 { hour24 += 12 }
        if ampm == "am", hour24 == 12 { hour24 = 0 }

        let calendar = Calendar.current
        let now = Date()
        let dayOffset = text.contains("tomorrow") ? 1 : 0
        let baseDate = calendar.date(byAdding: .day, value: dayOffset, to: now) ?? now
        var components = calendar.dateComponents([.year, .month, .day], from: baseDate)
        components.hour = hour24
        components.minute = minute
        guard let date = calendar.date(from: components) else { return nil }

        if !text.contains("today"), !text.contains("tomorrow"), date < now {
            return calendar.date(byAdding: .day, value: 1, to: date) ?? date
        }
        return date
    }
}

enum ScheduleAction {
    case text, call, notify, task
}

enum ProactiveError: Error, LocalizedError {
    case noScheduler, scheduleFailed(String)
    var errorDescription: String? {
        switch self {
        case .noScheduler: return "Could not reach the scheduler."
        case .scheduleFailed(let reason): return "Failed to schedule: \(reason)"
        }
    }
}
