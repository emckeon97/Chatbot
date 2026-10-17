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
    """Returns (from, subject, date, body)."""
    msg = _api(f"messages/{msg_id}?format=full")
    h = _headers(msg.get("payload", {}))
    return (h.get("from", "?"), h.get("subject", "(no subject)"),
            h.get("date", ""), _decode_body(msg.get("payload", {})))


def send_email(to, subject, body):
    raw = f"To: {to}\r\nSubject: {subject}\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n{body}"
    b64 = base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")
    _api("messages/send", method="POST", body={"raw": b64})
    return True
