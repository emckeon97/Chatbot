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

TOKEN_PATH = os.path.expanduser("~/.chatbot/outlook_token.json")
# Same app registration used by Muse's Outlook connector
CLIENT_ID = "9ae67231-da3a-476b-a3f4-1f89ccb7eb5b"
SCOPES = ["Mail.Read", "Mail.Send", "offline_access"]
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
    _api("sendMail", method="POST", body={
        "message": {
            "subject": subject,
            "body": {"contentType": "Text", "content": body},
            "toRecipients": [{"emailAddress": {"address": to}}],
        }
    })
    return True
