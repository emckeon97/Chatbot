#!/usr/bin/env python3
"""Add a plain-language control center and notification controls to the iMessage bridge.
Run after pulling the repo: python3 bridge/enable_control_center.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

CONTROL_CENTER_SUPPORT = r'''
# ---------- control center and notification preferences ----------
NOTIFICATION_PREFS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notification_prefs.json")

def load_notification_prefs():
    try:
        with open(NOTIFICATION_PREFS_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, dict) and data.get("mode") in ("urgent_only", "normal", "quiet"):
            return data
    except Exception:
        pass
    return {"mode": "urgent_only"}

def save_notification_prefs(prefs):
    with open(NOTIFICATION_PREFS_FILE, "w", encoding="utf-8") as handle:
        json.dump(prefs, handle, ensure_ascii=False, indent=2)

def set_notification_mode(mode):
    mode = str(mode or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"urgent": "urgent_only", "important_only": "urgent_only", "silent": "quiet"}
    mode = aliases.get(mode, mode)
    if mode not in ("urgent_only", "normal", "quiet"):
        return "Choose urgent_only, normal, or quiet."
    prefs = {"mode": mode, "updated": time.time()}
    save_notification_prefs(prefs)
    labels = {
        "urgent_only": "Only urgent, time-sensitive updates will interrupt you.",
        "normal": "Useful updates can come through as they happen.",
        "quiet": "No proactive updates unless something is genuinely urgent.",
    }
    return labels[mode]

def control_center_text():
    prefs = load_notification_prefs()
    items = load_task_statuses()
    if not items:
        return "Control center: no tasks are being tracked yet. Notifications are set to %s." % prefs["mode"]
    active = [item for item in items if item.get("status") in ("in_progress", "awaiting_approval", "blocked")]
    recent = list(reversed(items[-5:]))
    lines = ["Control center. Notifications: %s." % prefs["mode"]]
    if active:
        lines.append("Active:")
        for item in reversed(active[-5:]):
            detail = (": " + item.get("detail", "")) if item.get("detail") else ""
            lines.append("- %s is %s%s" % (item.get("title", "Untitled task"), item.get("status", "unknown"), detail))
    else:
        lines.append("Nothing is waiting on you.")
    lines.append("Recent:")
    for item in recent:
        detail = (": " + item.get("detail", "")) if item.get("detail") else ""
        lines.append("- %s is %s%s" % (item.get("title", "Untitled task"), item.get("status", "unknown"), detail))
    return "\n".join(lines)
'''

TOOL_LINES = '''- control_center | {} - Show what is active, waiting for approval, blocked, and recently finished.
- notification_mode | {} - Show the current notification setting.
- set_notification_mode | {"mode": "urgent_only/normal/quiet"} - Change when proactive updates interrupt him.
'''

CONTROL_CENTER_RULE = '''\nCONTROL CENTER RULE: Use task_update whenever a multi-step job starts, reaches an approval point, becomes blocked, or finishes. Use status awaiting_approval before asking permission for an external action. If he asks what is happening, call control_center and answer from it. Default notifications are urgent_only: interrupt him only for genuinely urgent, time-sensitive updates. Use normal only when he asks for more proactive updates, and quiet when he asks not to be interrupted.\n'''

DISPATCH = r'''
    if name == "control_center":
        return control_center_text()
    if name == "notification_mode":
        return "Notifications are set to %s." % load_notification_prefs()["mode"]
    if name == "set_notification_mode":
        return set_notification_mode(args.get("mode", ""))
'''

def patch(source):
    changes = []
    from enable_reliability_upgrade import patch as apply_reliability_upgrade
    source, reliability_changes = apply_reliability_upgrade(source)
    changes.extend(reliability_changes)

    if "NOTIFICATION_PREFS_FILE =" not in source:
        marker = "\n# ---------- tool calling"
        if marker not in source:
            raise RuntimeError("could not find the tool section")
        source = source.replace(marker, "\n" + CONTROL_CENTER_SUPPORT + marker, 1)
        changes.append("control center and notification preferences added")
    if "- control_center |" not in source:
        if "Tools:\n" not in source:
            raise RuntimeError("could not find the tool list")
        source = source.replace("Tools:\n", "Tools:\n" + TOOL_LINES, 1)
        changes.append("control-center commands added")
    if "CONTROL CENTER RULE:" not in source:
        start = source.find('SYSTEM_PROMPT = "')
        end = source.find('"\n# No time-based pruning', start)
        if start < 0 or end < 0:
            raise RuntimeError("could not find the system prompt")
        encoded = CONTROL_CENTER_RULE.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        source = source[:end] + encoded + source[end:]
        changes.append("control-center behavior rules added")
    if 'if name == "control_center":' not in source:
        marker = '    if name == "propose_plan":'
        if marker not in source:
            raise RuntimeError("could not find the tool dispatcher")
        source = source.replace(marker, DISPATCH + marker, 1)
        changes.append("control-center commands wired in")
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
    print("Control center upgrade is ready." if changes else "Control center upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Control center upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
