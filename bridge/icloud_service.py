#!/usr/bin/env python3
"""
iCloud Mail service for the iMessage bridge.
Setup: python3 icloud_setup.py (asks for Apple ID + app-specific password)
  Get an app-specific password at https://appleid.apple.com > Sign-In and Security
Then: list_inbox(), read_email(), send_email() just work.
"""
import email as email_lib
import imaplib
import json
import os
import smtplib
from email.message import EmailMessage
from email.utils import parsedate_to_datetime

def _find_token(filename):
    """Check ai user dir first, then current user dir."""
    candidates = [
        "/Users/AI/.chatbot/" + filename,
        os.path.expanduser("~/.chatbot/" + filename),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]


CREDS_PATH = os.path.expanduser("~/.chatbot/icloud.json")
IMAP_HOST, IMAP_PORT = "imap.mail.me.com", 993
SMTP_HOST, SMTP_PORT = "smtp.mail.me.com", 587


def _creds():
    if not os.path.exists(CREDS_PATH):
        raise RuntimeError("iCloud not connected. Run: python3 icloud_setup.py")
    with open(CREDS_PATH) as f:
        return json.load(f)


def _imap():
    c = _creds()
    conn = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
    conn.login(c["apple_id"], c["app_password"])
    conn.select("INBOX")
    return conn


def list_inbox(max_results=5):
    """Returns [(uid, from, subject, date, snippet)]."""
    conn = _imap()
    try:
        _, data = conn.search(None, "ALL")
        uids = data[0].split()[-max_results:]
        out = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)] BODY.PEEK[TEXT])")
            raw = b"".join(p[1] for p in msg_data if isinstance(p, tuple))
            msg = email_lib.message_from_bytes(raw)
            frm = str(msg.get("From", "?"))
            subj = str(msg.get("Subject", "(no subject)"))
            date = str(msg.get("Date", ""))
            try:
                body = msg.get_payload(decode=True) or b""
                snippet = body.decode("utf-8", errors="replace")[:120].replace("\n", " ")
            except Exception:
                snippet = ""
            out.append((uid.decode(), frm, subj, date, snippet))
        return out
    finally:
        conn.logout()


def read_email(uid):
    """Returns (from, subject, date, body)."""
    conn = _imap()
    try:
        _, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        raw = b"".join(p[1] for p in msg_data if isinstance(p, tuple))
        msg = email_lib.message_from_bytes(raw)
        frm = str(msg.get("From", "?"))
        subj = str(msg.get("Subject", "(no subject)"))
        date = str(msg.get("Date", ""))
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            body = msg.get_payload(decode=True).decode("utf-8", errors="replace")
        return (frm, subj, date, body)
    finally:
        conn.logout()


def send_email(to, subject, body):
    c = _creds()
    msg = EmailMessage()
    msg["From"] = c["apple_id"]
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.starttls()
        s.login(c["apple_id"], c["app_password"])
        s.send_message(msg)
    return True
