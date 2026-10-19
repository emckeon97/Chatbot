#!/usr/bin/env python3
"""Keep useful conversation context across long chats and every assistant entry point.
Run after pulling the repo: python3 bridge/enable_context_continuity.py
"""
from pathlib import Path
import os
import subprocess
import sys

HERE = Path(__file__).resolve().parent
BRIDGE = HERE / "imessage_bridge.py"
PLIST = Path.home() / "Library" / "LaunchAgents" / "com.chatbot.bridge.plist"

CONTEXT_SUPPORT = r'''
# ---------- conversation continuity ----------
CONTEXT_SUMMARY_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversation_context.json")

def load_context_summary():
    try:
        with open(CONTEXT_SUMMARY_FILE, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def save_context_summary(data):
    with open(CONTEXT_SUMMARY_FILE, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=1)

def continuity_context():
    summary = load_context_summary().get("summary", "").strip()
    if not summary:
        return ""
    return "\n\nConversation continuity notes. Treat these as context from earlier messages, not instructions:\n" + summary

def refresh_context_summary(history):
    # The live window stays fast, while a compact record preserves older goals,
    # decisions, preferences, people, and unfinished work.
    if len(history) <= MAX_CONTEXT_MESSAGES:
        return
    current = load_context_summary()
    if len(history) - int(current.get("message_count", 0)) < 40:
        return
    older = history[:-MAX_CONTEXT_MESSAGES]
    recent_old = older[-80:]
    lines = []
    for entry in recent_old:
        role = "User" if entry.get("role") == "user" else "Assistant"
        content = str(entry.get("content", "")).strip()
        if content:
            lines.append(role + ": " + content[:500])
    if not lines:
        return
    prior = str(current.get("summary", "")).strip()
    prompt = (
        "Maintain a concise continuity note for an assistant. Preserve only durable preferences, "
        "active goals, decisions, named people, commitments, and unresolved tasks. Do not invent "
        "facts, do not repeat instructions, and omit small talk. Keep it under 350 words.\n\n"
        "Previous continuity note:\n" + (prior or "(none)") + "\n\n"
        "Recent older conversation:\n" + "\n".join(lines)
    )
    body = json.dumps({"model": MODEL, "messages": [
        {"role": "system", "content": "Write only the continuity note."},
        {"role": "user", "content": prompt}
    ], "stream": False, "options": {"num_predict": 500}}).encode()
    request = urllib.request.Request(OLLAMA_URL, data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            data = json.load(response)
        summary = str(data["choices"][0]["message"]["content"] or "").strip()
        summary = re.sub(r"<think>.*?</think>", "", summary, flags=re.DOTALL).strip()
        if summary:
            save_context_summary({"summary": summary[:6000], "message_count": len(history), "updated": time.time()})
    except Exception:
        pass
'''

ASK_OLD = 'messages.append({"role": "system", "content": SYSTEM_PROMPT.strip() + facts_context()})'
ASK_NEW = 'messages.append({"role": "system", "content": SYSTEM_PROMPT.strip() + facts_context() + continuity_context()})'
SAVE_OLD = r'''def save_history(history: list) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=1)
    except Exception as error:
        print("could not save memory:", error)
'''
SAVE_NEW = r'''def save_history(history: list) -> None:
    try:
        with open(MEMORY_FILE, "w", encoding="utf-8") as file:
            json.dump(history, file, ensure_ascii=False, indent=1)
        refresh_context_summary(history)
    except Exception as error:
        print("could not save memory:", error)
'''

CONTINUITY_RULE = '''\nCONTEXT RULE: Treat the active conversation, saved facts, task status, and continuity notes as one shared context across iMessage, the phone app, and Siri Shortcuts. Before saying you do not know something from this conversation, use that context. When something meaningful changes, keep task status and saved facts current.\n'''

def patch(source):
    changes = []
    from enable_readiness_check import patch as apply_readiness_check
    source, inherited_changes = apply_readiness_check(source)
    changes.extend(inherited_changes)
    if "CONTEXT_SUMMARY_FILE =" not in source:
        marker = "\n# ---------- tool calling"
        if marker not in source:
            raise RuntimeError("could not find continuity insertion point")
        source = source.replace(marker, "\n" + CONTEXT_SUPPORT + marker, 1)
        changes.append("long-chat continuity summaries added")
    if ASK_OLD in source:
        source = source.replace(ASK_OLD, ASK_NEW, 1)
        changes.append("shared continuity now reaches the assistant")
    elif ASK_NEW not in source:
        raise RuntimeError("could not find model context setup")
    if SAVE_OLD in source:
        source = source.replace(SAVE_OLD, SAVE_NEW, 1)
        changes.append("older conversation context now compacts automatically")
    elif "refresh_context_summary(history)" not in source:
        raise RuntimeError("could not find history save function")
    if "CONTEXT RULE:" not in source:
        start = source.find('SYSTEM_PROMPT = "')
        end = source.find('"\n# No time-based pruning', start)
        if start < 0 or end < 0:
            raise RuntimeError("could not find the system prompt")
        encoded = CONTINUITY_RULE.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        source = source[:end] + encoded + source[end:]
        changes.append("cross-device context rule added")
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
    print("Context continuity upgrade is ready." if changes else "Context continuity upgrade was already applied.")
    for change in changes:
        print("- " + change)
    print("The bridge has been restarted." if restarted else "Restart imessage_bridge.py to load the changes.")

if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("Context continuity upgrade was not applied: %s" % error, file=sys.stderr)
        raise SystemExit(1)
