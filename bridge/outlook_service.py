#!/usr/bin/env python3
"""
Outlook Mail service for the iMessage bridge (Microsoft Graph).
Setup: python3 outlook_setup.py (device-code flow, sign in at microsoft.com/devicelogin)
  Requires: pip install msal
  Uses the Outlook client ID already connected to Muse. Token auto-refreshes.
Then: list_inbox(), read_email(), send_email() just work.
"""
import json
import os
import urllib.request

def _token_path():
    """Check ai user dir first, then current user dir."""
    candidates = [
        "/Users/AI/.chatbot/outlook_token.json",
        os.path.expanduser("~/.chatbot/outlook_token.json"),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return candidates[0]

TOKEN_PATH = _token_path()
# Same app registration used by Muse's Outlook connector
CLIENT_ID = "9ae67231-da3a-476b-a3f4-1f89ccb7eb5b"
SCOPES = ["Mail.Read", "Mail.Send"]
AUTHORITY = "https://login.microsoftonline.com/common"


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
    from msal import PublicClientApplication
    app = PublicClientApplication(token.get("client_id", CLIENT_ID), authority=AUTHORITY)
    result = app.acquire_token_by_refresh_token(token["refresh_token"], scopes=SCOPES)
    if "access_token" not in result:
        raise RuntimeError(f"Token refresh failed: {result.get('error_description', result)}")
    token["access_token"] = result["access_token"]
    if "refresh_token" in result:
        token["refresh_token"] = result["refresh_token"]
    _save_token(token)
    return token["access_token"]


def access_token():
    token = _load_token()
    if not token or "refresh_token" not in token:
        raise RuntimeError("Outlook not connected. Run: python3 outlook_setup.py")
    return _refresh(token)


def _api(path, method="GET", body=None):
    token = access_token()
    url = f"https://graph.microsoft.com/v1.0/me/{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else {}


def list_inbox(max_results=5):
    _imap_result = _imap_list(max_results)
    if _imap_result is not None:
        return _imap_result
    """Returns [(id, from, subject, date, snippet)]."""
    data = _api(f"messages?$top={max_results}&$orderby=receivedDateTime desc"
                "&$select=id,from,subject,receivedDateTime,bodyPreview")
    out = []
    for m in data.get("value", []):
        frm = m.get("from", {}).get("emailAddress", {})
        out.append((m["id"], frm.get("name") or frm.get("address", "?"),
                    m.get("subject", "(no subject)"),
                    m.get("receivedDateTime", "")[:10],
                    m.get("bodyPreview", "")[:120]))
    return out


def read_email(msg_id):
    _imap_result = _imap_read(msg_id)
    if _imap_result is not None:
        return _imap_result
    """Returns (from, subject, date, body)."""
    m = _api(f"messages/{msg_id}?$select=from,subject,receivedDateTime,body")
    frm = m.get("from", {}).get("emailAddress", {})
    body = m.get("body", {})
    text = body.get("content", "")
    if body.get("contentType") == "html":
        import re
        text = re.sub(r"<[^>]+>", "", text)
    return (frm.get("name") or frm.get("address", "?"),
            m.get("subject", "(no subject)"),
            m.get("receivedDateTime", "")[:10],
            text.strip())


def send_email(to, subject, body):
    _imap_result = _imap_send(to, subject, body)
    if _imap_result is not None:
        return _imap_result
    _api("sendMail", method="POST", body={
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
    })
    return True


# ---------- IMAP fallback (app password) ----------

def _app_creds():
    """Return list of {email, password} dicts. Supports single dict or list."""
    for p in ["/Users/AI/.chatbot/outlook_imap.json", os.path.expanduser("~/.chatbot/outlook_imap.json")]:
        if os.path.exists(p):
            with open(p) as f:
                data = json.load(f)
            if isinstance(data, dict):
                return [data]
            return data
    return []


def _imap_list(count=5):
    import imaplib, email as email_lib
    accounts = _app_creds()
    if not accounts:
        return None
    out = []
    for creds in accounts:
        conn = imaplib.IMAP4_SSL("outlook.office365.com", 993)
        try:
            conn.login(creds["email"], creds["password"])
            conn.select("INBOX")
            _, data = conn.search(None, "ALL")
            uids = data[0].split()[-count:]
            for uid in reversed(uids):
                _, msg_data = conn.fetch(uid, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
                raw = b"".join(p[1] for p in msg_data if isinstance(p, tuple))
                msg = email_lib.message_from_bytes(raw)
                out.append({
                    "id": "%s:%s" % (creds["email"], uid.decode()),
                    "from": str(msg.get("From", "?")),
                    "subject": str(msg.get("Subject", "(no subject)")),
                    "account": creds["email"],
                })
        except Exception as e:
            out.append({"id": "?", "from": "?", "subject": "Error on %s: %s" % (creds["email"], e), "account": creds["email"]})
        finally:
            try: conn.logout()
            except: pass
    return out


def _imap_read(uid):
    import imaplib, email as email_lib
    accounts = _app_creds()
    if not accounts:
        return None
    # uid may be "email:uid" for multi-account
    target_email = None
    if ":" in str(uid) and "@" in str(uid).split(":")[0]:
        target_email, uid = str(uid).split(":", 1)
    creds = next((c for c in accounts if c["email"] == target_email), accounts[0])
    conn = imaplib.IMAP4_SSL("outlook.office365.com", 993)
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


def _imap_send(to, subject, body, from_email=None):
    import smtplib
    from email.mime.text import MIMEText
    accounts = _app_creds()
    if not accounts:
        return None
    creds = next((c for c in accounts if c["email"] == from_email), accounts[0]) if from_email else accounts[0]
    msg = MIMEText(body)
    msg["From"] = creds["email"]
    msg["To"] = to
    msg["Subject"] = subject
    with smtplib.SMTP("smtp.office365.com", 587) as s:
        s.starttls()
        s.login(creds["email"], creds["password"])
        s.send_message(msg)
    return True
