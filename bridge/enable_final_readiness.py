#!/usr/bin/env python3
"""Final Chatbot readiness upgrade: applies every current bridge improvement in one run.
Run after pulling the repo: python3 bridge/enable_final_readiness.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

TOOL_CONTEXT_OLD = 'sys = SYSTEM_PROMPT.strip() + "\\n\\n" + TOOL_DESCRIPTIONS + facts_context()'
TOOL_CONTEXT_NEW = 'sys = SYSTEM_PROMPT.strip() + "\\n\\n" + TOOL_DESCRIPTIONS + facts_context() + continuity_context()'

def patch(source):
    from enable_startup_self_test import patch as apply_startup_self_test
    source, changes = apply_startup_self_test(source)
    if TOOL_CONTEXT_OLD in source:
        source = source.replace(TOOL_CONTEXT_OLD, TOOL_CONTEXT_NEW, 1)
        changes.append("context continuity now reaches tool-using conversations")
    elif TOOL_CONTEXT_NEW not in source:
        raise RuntimeError("could not find tool conversation context")
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
    print("Final readiness upgrade is ready." if changes else "Final readiness upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")
    print("Open the project in Xcode and build once to install the updated phone app.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Final readiness upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
