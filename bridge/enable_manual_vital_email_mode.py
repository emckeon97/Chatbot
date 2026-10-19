#!/usr/bin/env python3
"""Switch Chatbot from unsolicited email alerts to manual vital-email checks.

Run this once after pulling the repo update. It stops the old email-watcher
LaunchAgent, teaches the bridge how to handle a text such as "check my vital
email", and restarts the bridge LaunchAgent when one is installed.
"""

from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
WATCHER_LABEL = "com.chatbot.emailwatcher"
BRIDGE_LABEL = "com.chatbot.bridge"
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
WATCHER_PLIST = LAUNCH_AGENTS / (WATCHER_LABEL + ".plist")
BRIDGE_PLIST = LAUNCH_AGENTS / (BRIDGE_LABEL + ".plist")
PROMPT_ANCHOR = "You have tools: propose_plan"
MANUAL_RULE = (
    " MANUAL VITAL EMAIL MODE: Act on email only when Elijah asks. Never run "
    "background inbox polling or send unsolicited inbox alerts. When he asks "
    "to check vital email, check every connected provider and report only mail "
    "that needs attention, such as security, banking, job, legal, medical, "
    "account, or deadline messages. If a provider or tool is unavailable, say "
    "plainly what is missing and why the request could not finish. "
)


def run(*args):
    return subprocess.run(args, capture_output=True, text=True)


def unload(label, plist):
    domain = "gui/%d" % os.getuid()
    result = run("launchctl", "bootout", domain, str(plist))
    return result.returncode == 0


def restart_bridge():
    if not BRIDGE_PLIST.exists():
        return False
    domain = "gui/%d" % os.getuid()
    run("launchctl", "bootout", domain, str(BRIDGE_PLIST))
    result = run("launchctl", "bootstrap", domain, str(BRIDGE_PLIST))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not restart the bridge LaunchAgent")
    return True


def patch_prompt():
    bridge = HERE / "imessage_bridge.py"
    if not bridge.exists():
        raise RuntimeError("Missing %s" % bridge)
    source = bridge.read_text(encoding="utf-8")
    if "MANUAL VITAL EMAIL MODE" in source:
        return False
    if PROMPT_ANCHOR not in source:
        raise RuntimeError("Could not find the bridge prompt to update")
    bridge.write_text(source.replace(PROMPT_ANCHOR, MANUAL_RULE + PROMPT_ANCHOR, 1), encoding="utf-8")
    return True


def main():
    changed = patch_prompt()
    was_running = unload(WATCHER_LABEL, WATCHER_PLIST) if WATCHER_PLIST.exists() else False
    if WATCHER_PLIST.exists():
        WATCHER_PLIST.unlink()
    restarted = restart_bridge()
    print("Manual vital-email mode is ready.")
    if changed:
        print("The bridge now waits for requests like: check my vital email.")
    if was_running:
        print("The old automatic email watcher has been stopped.")
    if restarted:
        print("The bridge LaunchAgent has been restarted.")
    else:
        print("Restart imessage_bridge.py if you run it manually.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Manual vital-email mode was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
