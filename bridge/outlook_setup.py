#!/usr/bin/env python3
"""
One-time Outlook Mail setup for the iMessage bridge.
Run: pip install msal && python3 outlook_setup.py
Opens browser for Microsoft login. Usage: python3 outlook_setup.py [email] - saves per-account token.
Saves token to ~/.chatbot/outlook_token.json (auto-refreshes after).
"""
import json
import os
import sys
import glob
import re

CLIENT_ID = "dfb2f028-c13c-433a-98e2-c0c104318d7d"
SCOPES = ["Mail.Read", "Mail.Send"]
AUTHORITY = "https://login.microsoftonline.com/consumers"
def _token_path(account=None):
    base = os.path.expanduser("~/.chatbot")
    if account:
        safe = re.sub(r"[^a-zA-Z0-9@._-]", "_", account)
        return os.path.join(base, f"outlook_token_{safe}.json")
    return os.path.join(base, "outlook_token.json")


def main():
    account = sys.argv[1] if len(sys.argv) > 1 else None
    token_path = _token_path(account)
    try:
        from msal import PublicClientApplication
    except ImportError:
        print("Run first: pip install msal")
        return

    app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    # Interactive browser login - simplest method
    print("Opening browser for Microsoft login...")
    result = app.acquire_token_interactive(scopes=SCOPES)
    if "access_token" not in result:
        print("Failed:", result.get("error_description", result))
        return
    print("Login successful!")
    token = {
        "client_id": CLIENT_ID,
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
    }
    os.makedirs(os.path.dirname(token_path), exist_ok=True)
    with open(token_path, "w") as f:
        json.dump(token, f)
    print(f"Saved to {token_path}. Outlook Mail is connected.")


if __name__ == "__main__":
    main()
