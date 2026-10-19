#!/usr/bin/env python3
"""Add a one-command readiness and connector health check to the iMessage bridge.
Run after pulling the repo: python3 bridge/enable_readiness_check.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

READINESS_SUPPORT = r'''
# ---------- readiness and connector health ----------
def readiness_report():
    checks = []
    # Model server and selected model
    try:
        base = OLLAMA_URL.split("/v1/")[0].rstrip("/")
        with urllib.request.urlopen(base + "/api/tags", timeout=8) as response:
            payload = json.load(response)
        models = [item.get("name", "") for item in payload.get("models", []) if isinstance(item, dict)]
        model_name = MODEL.split(":")[0]
        matched = any(name == MODEL or name.split(":")[0] == model_name for name in models)
        checks.append("Model server: online" + (" (selected model found)" if matched else " (server online; selected model was not listed)"))
    except Exception as error:
        checks.append("Model server: unavailable (%s)" % str(error)[:100])
    # Messages permission and database access
    try:
        if not os.path.exists(MESSAGE_DB):
            raise RuntimeError("Messages database not found")
        last_messages(1)
        checks.append("Messages: ready")
    except Exception as error:
        checks.append("Messages: needs attention (%s)" % str(error)[:100])
    # Connector modules and stored setup files
    checks.append("Gmail: " + ("available" if GMAIL_AVAILABLE else "not configured"))
    checks.append("Outlook: " + ("available" if OUTLOOK_AVAILABLE else "not configured"))
    checks.append("iCloud: " + ("available" if ICLOUD_AVAILABLE else "not configured"))
    checks.append("GitHub: " + ("available" if GITHUB_AVAILABLE else "not configured"))
    # Writable local state is required for memory, tasks, and schedules.
    try:
        state_dir = os.path.dirname(MEMORY_FILE)
        probe = os.path.join(state_dir, ".chatbot_write_probe")
        with open(probe, "w", encoding="utf-8") as handle:
            handle.write("ok")
        os.remove(probe)
        checks.append("Memory and task storage: ready")
    except Exception as error:
        checks.append("Memory and task storage: needs attention (%s)" % str(error)[:100])
    return "Readiness check:\n" + "\n".join("- " + check for check in checks)
'''

TOOL_LINES = '''- readiness_check | {} - Check whether the model, Messages, connectors, and local memory storage are ready to use.
'''

READINESS_RULE = '''\nREADINESS RULE: When he asks whether the assistant is ready, whether a connector works, or what is broken, call readiness_check before answering. Report only what the check returns, in plain language.\n'''

DISPATCH = r'''
    if name == "readiness_check":
        return readiness_report()
'''

def patch(source):
    changes = []
    from enable_unified_assistant import patch_bridge as apply_unified_assistant
    source, inherited_changes = apply_unified_assistant(source)
    changes.extend(inherited_changes)
    if "def readiness_report" not in source:
        marker = "\n# ---------- delegated tasks (picked up by main Jarvis via Tailscale)"
        if marker not in source:
            raise RuntimeError("could not find readiness insertion point")
        source = source.replace(marker, "\n" + READINESS_SUPPORT + marker, 1)
        changes.append("readiness and connector health checks added")
    if "- readiness_check |" not in source:
        if "Tools:\n" not in source:
            raise RuntimeError("could not find the tool list")
        source = source.replace("Tools:\n", "Tools:\n" + TOOL_LINES, 1)
        changes.append("readiness command added")
    if "READINESS RULE:" not in source:
        start = source.find('SYSTEM_PROMPT = "')
        end = source.find('"\n# No time-based pruning', start)
        if start < 0 or end < 0:
            raise RuntimeError("could not find the system prompt")
        encoded = READINESS_RULE.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        source = source[:end] + encoded + source[end:]
        changes.append("readiness behavior rule added")
    if 'if name == "readiness_check":' not in source:
        marker = '    if name == "propose_plan":'
        if marker not in source:
            raise RuntimeError("could not find the tool dispatcher")
        source = source.replace(marker, DISPATCH + marker, 1)
        changes.append("readiness command wired in")
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
    print("Readiness check upgrade is ready." if changes else "Readiness check upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Readiness check upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
