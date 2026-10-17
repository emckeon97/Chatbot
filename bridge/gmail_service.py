#!/usr/bin/env python3
"""
Gmail service for the iMessage bridge.
One-time setup: python3 gmail_setup.py (opens browser for Google sign-in)
Then: list_inbox(), read_email(), send_email() just work (token auto-refreshes).
"""
import base64
import json
import os
import urllib.parse
import urllib.request

def _token_path():
    """Check ai user dir first, then current user dir."""
    candidates = [
        "/Users/AI/.chatbot/gmail_token.json",
        os.path.expanduser("~/.chatbot/gmail_token.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

TOKEN_PATH = _token_path()
CLIENT_ID = "294125895152-j6iicnapr17dip79nogi23amqb1i8d6d.apps.googleusercontent.com"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send"]


def _load_token():
    if not os.path.exists(TOKEN_PATH):
        return None
    with open(TOKEN_PATH) as f:
        return json.load(f)


def _save_token(data):
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(data, f)


def _refresh(token):
    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "refresh_token": token["refresh_token"],
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.load(resp)
    token["access_token"] = data["access_token"]
    _save_token(token)
    return token["access_token"]


def access_token():
    token = _load_token()
    if not token or "refresh_token" not in token:
        raise RuntimeError("Gmail not connected. Run: python3 gmail_setup.py")
    return _refresh(token)


def _api(path, method="GET", body=None):
    token = access_token()
    url = f"https://gmail.googleapis.com/gmail/v1/users/me/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def _headers(payload):
    out = {}
    for h in payload.get("headers", []):
        out[h["name"].lower()] = h["value"]
    return out


def _decode_body(payload):
    def b64url(s):
        s = s.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
        return base64.b64decode(s).decode("utf-8", errors="replace")
    body = payload.get("body", {})
    if body.get("data"):
        return b64url(body["data"])
    for part in payload.get("parts", []):
        if part.get("mimeType", "").startswith("text/plain"):
            d = part.get("body", {}).get("data")
            if d:
                return b64url(d)
    return ""


def list_inbox(max_results=5):
    _imap_result = _imap_list(max_results)
    if _imap_result is not None:
        return _imap_result
    # Try app-password IMAP first (simpler, no OAuth)
    try:
        result = _imap_list(max_results)
        if result is not None:
            return result
    except Exception:
        pass
    # Fall back to OAuth
    """Returns [(id, from, subject, date, snippet)]."""
    data = _api(f"messages?maxResults={max_results}&q=in:inbox")
    out = []
    for item in data.get("messages", []):
        msg = _api(f"messages/{item['id']}?format=metadata&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date")
        h = _headers(msg.get("payload", {}))
        out.append((item["id"], h.get("from", "?"), h.get("subject", "(no subject)"),
                    h.get("date", ""), msg.get("snippet", "")))
    return out


def read_email(msg_id):
    _imap_result = _imap_read(msg_id)
    if _imap_result is not None:
        return _imap_result
    """Returns (from, subject, date, body)."""
    msg = _api(f"messages/{msg_id}?format=full")
    h = _headers(msg.get("payload", {}))
    return (h.get("from", "?"), h.get("subject", "(no subject)"),
            h.get("date", ""), _decode_body(msg.get("payload", {})))


def send_email(to, subject, body):
    _imap_result = _imap_send(to, subject, body)
    if _imap_result is not None:
        return _imap_result
    raw = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    b64 = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    _api("messages/send", method="POST", body={"raw": b64})
    return True


# ---------- IMAP fallback (app password) ----------

def _app_creds():
    """Check for app-password creds at ~/.chatbot/gmail_imap.json or /Users/AI/.chatbot/."""
    for p in ["/Users/AI/.chatbot/gmail_imap.json", os.path.expanduser("~/.chatbot/gmail_imap.json")]:
        if os.path.exists(p):
            with open(p) as f:
                return json.load(f)
    return None


def _imap_list(count=5):
    import imaplib, email as email_lib
    creds = _app_creds()
    if not creds:
        return None
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        conn.login(creds["email"], creds["password"])
        conn.select("INBOX")
        _, data = conn.search(None, "ALL")
        uids = data[0].split()[-count:]
        out = []
        for uid in reversed(uids):
            _, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            raw = b"".join(p[1] for p in msg_data if isinstance(p, tuple))
            msg = email_lib.message_from_bytes(raw)
            out.append({
                "id": uid.decode(),
                "from": str(msg.get("From", "?")),
                "subject": str(msg.get("Subject", "(no subject)")),
            })
        return out
    finally:
        try: conn.logout()
        except: pass


def _imap_read(uid):
    import imaplib, email as email_lib
    creds = _app_creds()
    if not creds:
        return None
    conn = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    try:
        conn.login(creds["email"], creds["password"])
        conn.select("INBOX")
        _, msg_data = conn.fetch(uid.encode(), "(RFC822)")
        raw = b"".join(p[1] for p in msg_data if isinstance(p, tuple))
        msg = email_lib.message_from_bytes(raw)
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    body = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    break
        else:
            payload = msg.get_payload(decode=True)
            body = payload.decode("utf-8", errors="replace") if payload else ""
        return "From: %s\nSubject: %s\nDate: %s\n\n%s" % (
            msg.get("From", "?"), msg.get("Subject", "(no subject)"), msg.get("Date", ""), body[:3000])
    finally:
        try: conn.logout()
        except: pass


def _imap_send(to, subject, body):
    import smtplib
    from email.mime.text import MIMEText
    creds = _app_creds()
    if not creds:
        return None
    msg = MIMEText(body)
    msg["From"] = creds["email"]
    msg["To"] = to
    msg["Subject"] = subject
    with smtplib.SMTP("smtp.gmail.com", 587) as s:
        s.starttls()
        s.login(creds["email"], creds["password"])
        s.send_message(msg)
    return True
