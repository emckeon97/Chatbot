#!/usr/bin/env python3
"""Add startup self-test and durable failure receipts to the iMessage bridge.
Run after pulling the repo: python3 bridge/enable_startup_self_test.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

FAILURE_SUPPORT = r'''
# ---------- startup self-test and failure receipts ----------
FAILURE_RECEIPTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "failure_receipts.json")
STARTUP_REPORT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "startup_report.json")

def load_failure_receipts():
    try:
        with open(FAILURE_RECEIPTS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_failure_receipts(items):
    with open(FAILURE_RECEIPTS_FILE, "w", encoding="utf-8") as handle:
        json.dump(items[-50:], handle, ensure_ascii=False, indent=1)

def record_failure_receipt(tool, outcome):
    item = {"tool": tool, "outcome": str(outcome)[:800], "at": time.time()}
    items = load_failure_receipts()
    items.append(item)
    save_failure_receipts(items)
    try:
        update_task_status(tool.replace("_", " "), "blocked", str(outcome)[:300])
    except Exception:
        pass
    return item

def latest_failure_receipt():
    items = load_failure_receipts()
    if not items:
        return "No tool failures have been recorded."
    item = items[-1]
    when = datetime.fromtimestamp(item.get("at", 0)).strftime("%a %b %d at %I:%M %p")
    return "Latest failure: %s at %s. %s" % (item.get("tool", "task"), when, item.get("outcome", ""))

def save_startup_report(report):
    with open(STARTUP_REPORT_FILE, "w", encoding="utf-8") as handle:
        json.dump({"report": report, "checked_at": time.time()}, handle, ensure_ascii=False, indent=1)

def run_startup_self_test():
    report = readiness_report()
    save_startup_report(report)
    return report
'''

TOOL_LINES = '''- failure_receipt | {} - Show the most recent tool failure and what stopped it.
- startup_self_test | {} - Run the assistant readiness check now.
'''

SELF_TEST_RULE = '''\nSELF-TEST RULE: Run startup_self_test when the bridge starts. If a tool fails, give the plain outcome, record_failure_receipt, and mark the related task blocked instead of claiming it worked. If he asks what failed, call failure_receipt.\n'''

DISPATCH = r'''
    if name == "failure_receipt":
        return latest_failure_receipt()
    if name == "startup_self_test":
        return run_startup_self_test()
'''

EXECUTE_TOOL = r'''def execute_tool(name, args):
    """Run one tool, record a usable receipt when it fails, and never invent success."""
    import traceback
    try:
        result = _execute_tool_inner(name, args)
        text = str(result)
        failure_words = ("couldn't", "failed", "error:", "not connected", "not available", "unavailable")
        if any(word in text.lower() for word in failure_words):
            record_failure_receipt(name, text)
        return result
    except Exception as e:
        tb = traceback.format_exc()
        try:
            with open("/Users/AI/.chatbot/tool_errors.log", "a") as handle:
                handle.write("\n[%s] %s\n%s\n" % (name, e, tb))
        except Exception:
            pass
        message = "I couldn't complete %s. %s" % (name.replace("_", " "), str(e)[:240])
        record_failure_receipt(name, message)
        return message
'''

def replace_between(source, start_marker, end_marker, replacement):
    start = source.find(start_marker)
    end = source.find(end_marker, start)
    if start < 0 or end < 0:
        raise RuntimeError("could not find %s" % start_marker)
    return source[:start] + replacement + source[end:]

def patch(source):
    changes = []
    from enable_context_continuity import patch as apply_context_continuity
    source, inherited_changes = apply_context_continuity(source)
    changes.extend(inherited_changes)
    if "FAILURE_RECEIPTS_FILE =" not in source:
        marker = "\n# ---------- tool calling"
        if marker not in source:
            raise RuntimeError("could not find self-test insertion point")
        source = source.replace(marker, "\n" + FAILURE_SUPPORT + marker, 1)
        changes.append("startup self-test and durable failure receipts added")
    if "- failure_receipt |" not in source:
        if "Tools:\n" not in source:
            raise RuntimeError("could not find the tool list")
        source = source.replace("Tools:\n", "Tools:\n" + TOOL_LINES, 1)
        changes.append("self-test commands added")
    if "SELF-TEST RULE:" not in source:
        start = source.find('SYSTEM_PROMPT = "')
        end = source.find('"\n# No time-based pruning', start)
        if start < 0 or end < 0:
            raise RuntimeError("could not find the system prompt")
        encoded = SELF_TEST_RULE.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        source = source[:end] + encoded + source[end:]
        changes.append("startup and failure behavior rule added")
    if 'if name == "failure_receipt":' not in source:
        marker = '    if name == "propose_plan":'
        if marker not in source:
            raise RuntimeError("could not find the tool dispatcher")
        source = source.replace(marker, DISPATCH + marker, 1)
        changes.append("failure receipt commands wired in")
    old_execute = 'def execute_tool(name, args):'
    if "record_failure_receipt(name, text)" not in source:
        source = replace_between(source, old_execute, '\ndef ask_with_tools(', EXECUTE_TOOL + '\n')
        changes.append("tool failures now create plain receipts")
    startup_marker = '    start_scheduler_server()\n'
    if "run_startup_self_test()" not in source:
        if startup_marker not in source:
            raise RuntimeError("could not find bridge startup")
        source = source.replace(startup_marker, startup_marker + '    print(run_startup_self_test())\n', 1)
        changes.append("bridge runs a readiness check at startup")
    return source, changes

def restart_bridge():
    if not PLIST.exists():
        return False
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, str(PLIST)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "could not restart the bridge")
    return True

def main():
    if not BRIDGE.exists():
        raise RuntimeError("missing %s" % BRIDGE)
    source, changes = patch(BRIDGE.read_text(encoding="utf-8"))
    if changes:
        BRIDGE.write_text(source, encoding="utf-8")
        import py_compile
        py_compile.compile(str(BRIDGE), doraise=True)
    restarted = restart_bridge()
    print("Startup self-test upgrade is ready." if changes else "Startup self-test upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Startup self-test upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
