import Foundation
import JavaScriptCore

/// Tool calling for the chat model (OpenAI function format).
/// The model decides which tools to use; results feed back into the conversation.
final class ToolService {
    static let shared = ToolService()

    struct ToolDef {
        let name: String
        let description: String
        let parameters: [String: Any]
    }

    /// All tools exposed to the model.
    static let tools: [ToolDef] = [
        ToolDef(name: "get_weather",
                description: "Get current weather. Location optional, defaults to Charlotte.",
                parameters: ["type": "object",
                             "properties": ["location": ["type": "string", "description": "City name"]]]),
        ToolDef(name: "get_calendar",
                description: "List today's calendar events.",
                parameters: ["type": "object", "properties": [:]]),
        ToolDef(name: "add_calendar_event",
                description: "Add a calendar event. Describe it naturally, e.g. 'Dentist tomorrow at 3pm'.",
                parameters: ["type": "object",
                             "properties": ["description": ["type": "string"]],
                             "required": ["description"]]),
        ToolDef(name: "get_stock",
                description: "Get a stock price and daily change.",
                parameters: ["type": "object",
                             "properties": ["symbol": ["type": "string", "description": "Ticker like AAPL"]],
                             "required": ["symbol"]]),
        ToolDef(name: "get_crypto",
                description: "Get a cryptocurrency price and 24h change.",
                parameters: ["type": "object",
                             "properties": ["coin": ["type": "string", "description": "e.g. bitcoin, ethereum"]],
                             "required": ["coin"]]),
        ToolDef(name: "get_news",
                description: "Get top news headlines.",
                parameters: ["type": "object", "properties": [:]]),
        ToolDef(name: "define_word",
                description: "Get the dictionary definition of a word.",
                parameters: ["type": "object",
                             "properties": ["word": ["type": "string"]],
                             "required": ["word"]]),
        ToolDef(name: "web_search",
                description: "Search the web for current information.",
                parameters: ["type": "object",
                             "properties": ["query": ["type": "string"]],
                             "required": ["query"]]),
        ToolDef(name: "run_javascript",
                description: "Execute JavaScript code and return the result. Use for math, data processing, algorithms, etc. The last expression value is returned.",
                parameters: ["type": "object",
                             "properties": ["code": ["type": "string", "description": "JavaScript code to run"]],
                             "required": ["code"]]),
        ToolDef(name: "check_gmail",
                description: "List recent Gmail inbox messages.",
                parameters: ["type": "object", "properties": [:]]),
        ToolDef(name: "get_briefing",
                description: "Get a combined briefing: weather, today's calendar, and top headlines.",
                parameters: ["type": "object", "properties": [:]]),
        ToolDef(name: "github_list_repos",
                description: "List the user's GitHub repositories.",
                parameters: ["type": "object", "properties": [:]]),
        ToolDef(name: "github_read_file",
                description: "Read a file from a GitHub repo.",
                parameters: ["type": "object",
                             "properties": ["repo": ["type": "string", "description": "e.g. emckeon97/Chatbot"],
                                            "path": ["type": "string", "description": "file path in repo"]],
                             "required": ["repo", "path"]]),
        ToolDef(name: "github_write_file",
                description: "Create or update a file in a GitHub repo. Commits directly to the branch.",
                parameters: ["type": "object",
                             "properties": ["repo": ["type": "string"],
                                            "path": ["type": "string"],
                                            "content": ["type": "string", "description": "full file content"],
                                            "message": ["type": "string", "description": "commit message"]],
                             "required": ["repo", "path", "content", "message"]]),
        ToolDef(name: "propose_plan",
                description: "Present a plan to the user BEFORE taking any state-changing action. Call this first, then wait for the user to confirm before proceeding with writes, sends, or other mutations.",
                parameters: ["type": "object",
                             "properties": ["title": ["type": "string", "description": "Short plan title"],
                                            "steps": ["type": "string", "description": "Numbered list of exactly what you will do"]],
                             "required": ["title", "steps"]]),
        ToolDef(name: "github_list_files",
                description: "List all files in a GitHub repo (recursive). Use this first to understand a project's structure.",
                parameters: ["type": "object",
                             "properties": ["repo": ["type": "string", "description": "e.g. emckeon97/Chatbot"]],
                             "required": ["repo"]]),
        ToolDef(name: "github_commits",
                description: "List recent commits on a GitHub repo.",
                parameters: ["type": "object",
                             "properties": ["repo": ["type": "string"]],
                             "required": ["repo"]]),
    ]

    /// OpenAI-format tools array for the request.
    static func openAITools() -> [[String: Any]] {
        tools.map { t in
            ["type": "function",
             "function": ["name": t.name, "description": t.description, "parameters": t.parameters]]
        }
    }

    /// Execute a tool call. Returns the result string.
    func execute(name: String, arguments: [String: Any]) async -> String {
        switch name {
        case "get_weather":
            let loc = arguments["location"] as? String ?? ""
            return await WeatherService.shared.fetch(location: loc)
        case "get_calendar":
            return await CalendarService.shared.listToday()
        case "add_calendar_event":
            let desc = arguments["description"] as? String ?? ""
            return await CalendarService.shared.addEvent(desc)
        case "get_stock":
            let sym = (arguments["symbol"] as? String ?? "").uppercased()
            return await InfoService.shared.stock(sym)
        case "get_crypto":
            let coin = (arguments["coin"] as? String ?? "").lowercased()
            return await InfoService.shared.crypto(coin)
        case "get_news":
            return await InfoService.shared.news()
        case "define_word":
            let word = arguments["word"] as? String ?? ""
            return await InfoService.shared.define(word)
        case "web_search":
            let q = arguments["query"] as? String ?? ""
            do {
                let results = try await WebSearchService.shared.search(q)
                return results
            } catch {
                return "Search failed."
            }
        case "run_javascript":
            let code = arguments["code"] as? String ?? ""
            return runJS(code)
        case "check_gmail":
            guard GoogleAuthService.shared.isSignedIn else {
                return "Gmail not connected. Connect in Settings first."
            }
            let emails = await GmailClient.shared.listInbox()
            return GmailClient.shared.formatList(emails)
        case "get_briefing":
            return await BriefingService.shared.build()
        case "github_list_repos":
            return await GitHubService.shared.listRepos()
        case "github_read_file":
            return await GitHubService.shared.readFile(
                repo: arguments["repo"] as? String ?? "",
                path: arguments["path"] as? String ?? "")
        case "github_write_file":
            return await GitHubService.shared.writeFile(
                repo: arguments["repo"] as? String ?? "",
                path: arguments["path"] as? String ?? "",
                content: arguments["content"] as? String ?? "",
                message: arguments["message"] as? String ?? "Update via Chatbot")
        case "propose_plan":
            let title = arguments["title"] as? String ?? "Plan"
            let steps = arguments["steps"] as? String ?? ""
            return "PLAN PROPOSED — present this to the user and WAIT for explicit confirmation before doing anything:\n\n**\(title)**\n\(steps)\n\nDo not proceed until the user says yes."
        case "github_list_files":
            return await GitHubService.shared.listFiles(
                repo: arguments["repo"] as? String ?? "")
        case "github_commits":
            return await GitHubService.shared.listCommits(
                repo: arguments["repo"] as? String ?? "")
        default:
            return "Unknown tool: \(name)"
        }
    }

    // MARK: - JavaScript

    private func runJS(_ code: String) -> String {
        guard let ctx = JSContext() else { return "JS engine unavailable." }
        ctx.exceptionHandler = { _, exc in
            // captured below
        }
        // Capture console.log
        var logs: [String] = []
        let logFn: @convention(block) (String) -> Void = { msg in logs.append(msg) }
        ctx.objectForKeyedSubscript("console")?.setObject(logFn, forKeyedSubscript: "log" as NSString)

        let result = ctx.evaluateScript(code)
        if let exc = ctx.exception {
            return "Error: \(exc.toString() ?? "unknown")"
        }
        var out = ""
        if !logs.isEmpty { out += logs.joined(separator: "\n") + "\n" }
        if let r = result, !r.isUndefined {
            out += "=> \(r.toString() ?? "")"
        }
        return out.isEmpty ? "Done (no output)." : out.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}
