#!/usr/bin/env python3
"""Make JARVIS friendlier: one-line done confirmations, automatic preference memory,
plain-English confirm-before-acting. Run once after pulling the repo; it patches
imessage_bridge.py in place (same approach as enable_manual_vital_email_mode.py),
syntax-checks the result, and restarts the bridge LaunchAgent when one is installed."""

from pathlib import Path
import os
import re
import subprocess
import sys

BS = chr(92)
NL = chr(10)
BSN = BS + "n"

HERE = Path(__file__).resolve().parent
BRIDGE_LABEL = "com.chatbot.bridge"
BRIDGE_PLIST = Path.home() / "Library" / "LaunchAgents" / (BRIDGE_LABEL + ".plist")

NEW_RULE = "BEFORE-ACTING RULE: If an instruction is vague or could mean several things, restate in one plain line what you heard and ask 'shall I?' before acting. If he gives a clear, explicit command, just do it and report the outcome. Anything irreversible or outward-facing (sending email, pushing to GitHub) gets one short confirmation line first, never a paragraph. Read-only actions (searching, reading, checking email) never need confirmation. DONE RULE: When an action finishes, confirm in ONE short line naming the real outcome ('Done, sir. Pushed to main.'). No recaps, no essays. If a tool is missing or something fails, say exactly what is missing in one plain line and stop there. MEMORY RULE: When he tells you something about himself, his people, his plans, or how he likes things done, save it with remember_fact without being asked. Use what you remember naturally in conversation; never announce that you remembered."

PHASE2_TAIL = ' If the tool result shows success, open with a one-line done confirmation naming the outcome, then stop. If a tool failed or is missing, name exactly what is missing in one plain line. Never narrate your process.'

NEW_REMEMBER = '        facts = load_facts()' + NL + '        if any(f.strip().lower() == fact.lower() for f in facts):' + NL + '            return "Already in memory, sir."' + NL + '        facts.append(fact)' + NL + '        save_facts(facts)' + NL

NEW_FACTS_CTX = 'def facts_context():' + NL + '    facts = load_facts()' + NL + '    if not facts:' + NL + '        return ""' + NL + '    recent = facts[-30:]' + NL + '    return "' + BSN + BSN + 'Things to remember about him:' + BSN + '" + "' + BSN + '".join("- %s" % f for f in recent)'

def splice(source, start_marker, end_marker, replacement, label):
    start = source.find(start_marker)
    if start < 0:
        raise RuntimeError("could not find " + label)
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError("could not find the end of " + label)
    end += len(end_marker)
    return source[:start] + replacement + source[end:]

def patch(source):
    changes = []
    if "DONE RULE:" not in source:
        source = splice(source, "CRITICAL RULE: Before ANY action", "need confirmation.", NEW_RULE, "the confirm rule")
        changes.append("confirm-before-acting rule updated")
    if "Never narrate your process." not in source:
        old_phase2 = "CRITICAL: NEVER claim you completed an action (added event, sent email, etc.) unless the tool result above confirms it. If you did not call a tool, say so honestly. Making up confirmations is the worst thing you can do."
        if old_phase2 not in source:
            raise RuntimeError("could not find the tool-answer instruction")
        source = source.replace(old_phase2, old_phase2 + PHASE2_TAIL, 1)
        changes.append("done-confirmation rule added to tool answers")
    if "Already in memory, sir." not in source:
        old_rem = "        facts = load_facts()" + NL + "        facts.append(fact)" + NL + "        save_facts(facts)" + NL
        if old_rem not in source:
            raise RuntimeError("could not find the remember_fact handler")
        source = source.replace(old_rem, NEW_REMEMBER, 1)
        changes.append("memory now skips duplicates")
    if "recent = facts[-30:]" not in source:
        old_ctx = "def facts_context():" + NL + "    facts = load_facts()" + NL + "    if not facts:" + NL + '        return ""' + NL + '    return "' + BSN + BSN + "Things to remember about him:" + BSN + '" + "' + BSN + '".join("- %s" % f for f in facts)'
        if old_ctx not in source:
            raise RuntimeError("could not find facts_context")
        source = source.replace(old_ctx, NEW_FACTS_CTX, 1)
        changes.append("prompt now carries the 30 most recent facts")
    return source, changes

def restart_bridge():
    if not BRIDGE_PLIST.exists():
        return False
    domain = "gui/%d" % os.getuid()
    subprocess.run(["launchctl", "bootout", domain, str(BRIDGE_PLIST)], capture_output=True)
    result = subprocess.run(["launchctl", "bootstrap", domain, str(BRIDGE_PLIST)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Could not restart the bridge LaunchAgent")
    return True

def main():
    bridge = HERE / "imessage_bridge.py"
    if not bridge.exists():
        raise RuntimeError("Missing %s" % bridge)
    source = bridge.read_text(encoding="utf-8")
    source, changes = patch(source)
    if changes:
        bridge.write_text(source, encoding="utf-8")
        import py_compile
        py_compile.compile(str(bridge), doraise=True)
    restarted = restart_bridge()
    print("JARVIS polish is ready." if changes else "JARVIS polish was already applied.")
    for change in changes:
        print("- " + change)
    if restarted:
        print("The bridge LaunchAgent has been restarted.")
    elif changes:
        print("Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("JARVIS polish was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
