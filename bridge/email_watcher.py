#!/usr/bin/env python3
"""Watch connected inboxes and text Elijah about vital new email.

This is deliberately separate from imessage_bridge.py so the message responder
stays responsive while this process runs continuously as a LaunchAgent.
"""

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import imessage_bridge as bridge

POLL_SECONDS = 60
MAX_MESSAGES = 25
STATE_FILE = Path.home() / ".chatbot" / "email_watcher_state.json"
LOG_FILE = Path.home() / ".chatbot" / "email_watcher.log"

# Promotional mail is muted unless it also has a stronger account, security, or
# financial signal. The goal is a useful interruption, not another newsletter.
PROMOTIONAL_TERMS = (
    "unsubscribe", "newsletter", "weekly digest", "daily digest", "sale",
    "deal", "promo", "promotion", "coupon", "clearance", "black friday",
    "cyber monday", "marketing", "sponsored",
)
VITAL_TERMS = (
    "security alert", "unusual activity", "suspicious activity", "sign-in",
    "signin", "login attempt", "password", "verification", "verify your",
    "one-time code", "one time code", "passcode", "access code",
    "account locked", "account suspended", "account disabled",
    "payment failed", "payment declined", "payment overdue", "past due",
    "overdraft", "fraud", "chargeback", "bank notice", "tax notice",
    "interview", "job offer", "application update", "recruiter",
    "background check", "onboarding", "benefits enrollment",
    "court", "legal notice", "summons", "lease", "eviction",
    "medical", "doctor", "appointment reminder", "prescription",
    "action required", "response required", "important account",
)


def _configure_logging():
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=LOG_FILE,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


def _load_state():
    if not STATE_FILE.exists():
        return {"version": 1, "providers": {}, "pending": []}
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("version", 1)
            data.setdefault("providers", {})
            data.setdefault("pending", [])
            return data
    except Exception:
        logging.exception("Could not load email watcher state")
    return {"version": 1, "providers": {}, "pending": []}


def _save_state(state):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_FILE)


def _provider_services():
    return (
        ("gmail", getattr(bridge, "gmail_service", None), getattr(bridge, "GMAIL_AVAILABLE", False)),
        ("outlook", getattr(bridge, "outlook_service", None), getattr(bridge, "OUTLOOK_AVAILABLE", False)),
        ("icloud", getattr(bridge, "icloud_service", None), getattr(bridge, "ICLOUD_AVAILABLE", False)),
    )


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _normalise(provider, raw):
    item = bridge._norm_email(raw)
    message_id = _clean(item.get("id"))
    if not message_id or message_id == "?":
        return None
    return {
        "key": "%s:%s" % (provider, message_id),
        "provider": provider,
        "id": message_id,
        "from": _clean(item.get("from")) or "Unknown sender",
        "subject": _clean(item.get("subject")) or "(no subject)",
    }


def _is_vital(message):
    haystack = (message["from"] + " " + message["subject"]).lower()
    has_vital_signal = any(term in haystack for term in VITAL_TERMS)
    if any(term in haystack for term in PROMOTIONAL_TERMS) and not has_vital_signal:
        return False
    return has_vital_signal


def _provider_state(state, provider):
    providers = state.setdefault("providers", {})
    entry = providers.setdefault(provider, {"initialized": False, "seen": []})
    entry.setdefault("initialized", False)
    entry.setdefault("seen", [])
    return entry


def _remember(entry, key):
    seen = entry["seen"]
    if key not in seen:
        seen.append(key)
    if len(seen) > 1000:
        del seen[:-1000]


def _notification_text(message):
    sender = re.sub(r"\s*<[^>]+>", "", message["from"]).strip() or message["from"]
    return "Important %s email from %s: %s" % (message["provider"], sender, message["subject"])


def _discover_new_messages(state):
    changed = False
    for provider, service, available in _provider_services():
        if not available or service is None:
            continue
        entry = _provider_state(state, provider)
        try:
            raw_messages = service.list_inbox(MAX_MESSAGES) or []
        except Exception as error:
            logging.warning("%s inbox poll failed: %s", provider, error)
            continue

        # Services return newest first. Reverse it so several alerts arrive in
        # their original order instead of backwards.
        messages = []
        for raw in raw_messages:
            message = _normalise(provider, raw)
            if message:
                messages.append(message)
        messages.reverse()

        if not entry["initialized"]:
            for message in messages:
                _remember(entry, message["key"])
            entry["initialized"] = True
            changed = True
            logging.info("Baselined %s inbox with %d messages", provider, len(messages))
            continue

        known = set(entry["seen"])
        for message in messages:
            if message["key"] in known:
                continue
            _remember(entry, message["key"])
            known.add(message["key"])
            changed = True
            if _is_vital(message):
                state.setdefault("pending", []).append({
                    "key": message["key"],
                    "text": _notification_text(message),
                    "created": time.time(),
                })
                logging.info("Queued vital email alert: %s", message["key"])
    return changed


def _ensure_chat():
    if getattr(bridge, "CHAT_ID", ""):
        return True
    chat_id = bridge.resolve_chat_id()
    if not chat_id:
        logging.warning("Could not find the configured iMessage chat")
        return False
    bridge.CHAT_ID = chat_id
    return True


def _deliver_pending(state):
    pending = list(state.get("pending", []))
    if not pending or not _ensure_chat():
        return False
    delivered = False
    remaining = []
    for alert in pending:
        try:
            bridge.send(alert["text"])
            delivered = True
            logging.info("Sent vital email alert: %s", alert["key"])
        except Exception as error:
            logging.warning("Could not send email alert %s: %s", alert.get("key"), error)
            remaining.append(alert)
    state["pending"] = remaining
    return delivered


def poll_once():
    state = _load_state()
    changed = _discover_new_messages(state)
    if changed:
        _save_state(state)
    delivered = _deliver_pending(state)
    if delivered:
        _save_state(state)


def main():
    _configure_logging()
    logging.info("Vital email watcher started")
    while True:
        try:
            poll_once()
        except Exception:
            logging.exception("Unexpected email watcher error")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
