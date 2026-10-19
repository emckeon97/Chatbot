#!/usr/bin/env python3
"""Install the vital-email watcher as a per-user macOS LaunchAgent."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LABEL = "com.chatbot.emailwatcher"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")
LOG_DIR = Path.home() / ".chatbot"


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    watcher = HERE / "email_watcher.py"
    if not watcher.exists():
        raise SystemExit("Missing %s" % watcher)

    plist = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(watcher)],
        "WorkingDirectory": str(HERE),
        "RunAtLoad": True,
        "KeepAlive": True,
        "StandardOutPath": str(LOG_DIR / "email_watcher.out.log"),
        "StandardErrorPath": str(LOG_DIR / "email_watcher.err.log"),
    }
    with PLIST_PATH.open("wb") as handle:
        plistlib.dump(plist, handle)

    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(result.stderr.strip() or "launchctl could not start the watcher")
    print("Vital email watcher is running.")


if __name__ == "__main__":
    main()
