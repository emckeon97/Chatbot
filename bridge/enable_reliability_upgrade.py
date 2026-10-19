#!/usr/bin/env python3
"""Add task status, editable memory, and clearer failures to the iMessage bridge.
Run after pulling the repo: python3 bridge/enable_reliability_upgrade.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

TASK_SUPPORT = r'''
# ---------- reliability: task status and editable memory ----------
TASK_STATUS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "task_status.json")

def load_task_statuses():
    try:
        with open(TASK_STATUS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, list) else []
    except Exception:
        return []

def save_task_statuses(items):
    with open(TASK_STATUS_FILE, "w", encoding="utf-8") as handle:
        json.dump(items, handle, ensure_ascii=False, indent=2)

def update_task_status(title, status="in_progress", detail="", task_id=""):
    items = load_task_statuses()
    now = time.time()
    target = next((item for item in items if task_id and item.get("id") == task_id), None)
    if target is None and title:
        target = next((item for item in reversed(items)
                       if item.get("title", "").strip().lower() == title.strip().lower()
                       and item.get("status") != "done"), None)
    if target is None:
        target = {"id": "task_%d" % int(now * 1000), "title": title or "Untitled task", "created": now}
        items.append(target)
    target.update({"status": status, "detail": detail, "updated": now})
    save_task_statuses(items)
    return target

def task_status_text():
    items = load_task_statuses()
    if not items:
        return "No tracked tasks yet, sir."
    lines = []
    for item in reversed(items[-12:]):
        detail = (": " + item.get("detail", "")) if item.get("detail") else ""
        lines.append("- %s is %s%s" % (item.get("title", "Untitled task"), item.get("status", "unknown"), detail))
    return "Task status:\n" + "\n".join(lines)

def replace_saved_fact(query, replacement):
    query, replacement = query.strip().lower(), replacement.strip()
    if not query or not replacement:
        return "I need both the old fact and the corrected one."
    facts = load_facts()
    matches = [i for i, fact in enumerate(facts) if query in fact.lower()]
    if matches:
        facts[matches[-1]] = replacement
        result = "Updated that memory, sir."
    else:
        facts.append(replacement)
        result = "Saved that as a new memory, sir."
    save_facts(facts)
    return result
'''

TOOL_LINES = '''- task_update | {"title": "...", "status": "in_progress/done/blocked", "detail": "...", "task_id": "optional"} - Track a task when work starts, finishes, or is blocked.
- task_status | {} - Show the current task list.
- replace_fact | {"query": "old wording", "replacement": "correct wording"} - Correct one saved memory.
'''

RELIABILITY_RULE = '''\nRELIABILITY RULE: When he asks you to do work that takes more than one step, call task_update with status in_progress. Call it again with done only after the result proves it finished, or blocked with one plain detail when it cannot continue. If he asks what is happening, call task_status instead of guessing. For any tool failure, tell him what did not happen in one short sentence and what he needs to do next. Never say something is done unless the tool result says it is done. When he corrects a saved preference or fact, use replace_fact so the old version does not keep coming back.\n'''

DISPATCH = r'''
    if name == "task_update":
        item = update_task_status(args.get("title", ""), args.get("status", "in_progress"), args.get("detail", ""), args.get("task_id", ""))
        return "Task updated: %s is %s." % (item["title"], item["status"])
    if name == "task_status":
        return task_status_text()
    if name == "replace_fact":
        return replace_saved_fact(args.get("query", ""), args.get("replacement", ""))
'''

def patch(source):
    changes = []
    from enable_jarvis_polish import patch as apply_jarvis_polish
    source, polish_changes = apply_jarvis_polish(source)
    changes.extend(polish_changes)

    if "TASK_STATUS_FILE =" not in source:
        marker = "\n# ---------- tool calling"
        if marker not in source: raise RuntimeError("could not find the tool section")
        source = source.replace(marker, "\n" + TASK_SUPPORT + marker, 1)
        changes.append("persistent task status added")
    if "- task_update |" not in source:
        if "Tools:\n" not in source: raise RuntimeError("could not find the tool list")
        source = source.replace("Tools:\n", "Tools:\n" + TOOL_LINES, 1)
        changes.append("task and memory correction commands added")
    if "RELIABILITY RULE:" not in source:
        start = source.find('SYSTEM_PROMPT = "')
        end = source.find('"\n# No time-based pruning', start)
        if start < 0 or end < 0: raise RuntimeError("could not find the system prompt")
        encoded = RELIABILITY_RULE.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        source = source[:end] + encoded + source[end:]
        changes.append("reliability rules added")
    if 'if name == "task_update":' not in source:
        marker = '    if name == "propose_plan":'
        if marker not in source: raise RuntimeError("could not find the tool dispatcher")
        source = source.replace(marker, DISPATCH + marker, 1)
        changes.append("task-status commands wired in")
    old = 'return f"{msg} It\'s logged and I\'ll get it fixed. (Error: {e})"'
    if old in source:
        source = source.replace(old, 'return msg + " Please try again, or tell me what you want to do next."', 1)
        changes.append("tool failures now stay plain and actionable")
    return source, changes

def restart_bridge():
    if not PLIST.exists(): return False
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, str(PLIST)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(PLIST)], capture_output=True, text=True)
    if result.returncode != 0: raise RuntimeError(result.stderr.strip() or "could not restart the bridge")
    return True

def main():
    if not BRIDGE.exists(): raise RuntimeError("missing %s" % BRIDGE)
    source, changes = patch(BRIDGE.read_text(encoding="utf-8"))
    if changes:
        BRIDGE.write_text(source, encoding="utf-8")
        import py_compile
        py_compile.compile(str(BRIDGE), doraise=True)
    restarted = restart_bridge()
    print("Reliability upgrade is ready." if changes else "Reliability upgrade was already applied.")
    for change in changes: print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try: main()
    except Exception as error:
        print("Reliability upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
