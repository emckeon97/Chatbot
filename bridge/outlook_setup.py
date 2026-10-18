#!/usr/bin/env python3
"""
One-time Outlook Mail setup for the iMessage bridge.
Run: pip install msal && python3 outlook_setup.py
Opens browser for Microsoft login. Saves token to ~/.chatbot/outlook_token.json.
Saves token to ~/.chatbot/outlook_token.json (auto-refreshes after).
"""
import json
import os

CLIENT_ID = "dfb2f028-c13c-433a-98e2-c0c104318d7d"
SCOPES = ["Mail.Read", "Mail.Send"]
AUTHORITY = "https://login.microsoftonline.com/common"
TOKEN_PATH = os.path.expanduser("~/.chatbot/outlook_token.json")


def main():
    try:
        from msal import PublicClientApplication
    except ImportError:
        print("Run first: pip install msal")
        return

    app = PublicClientApplication(CLIENT_ID, authority=AUTHORITY)
    # Interactive browser login - simplest method
    print("Opening browser for Microsoft login...")
    result = app.acquire_token_interactive(scopes=SCOPES, redirect_uri="https://login.microsoftonline.com/common/oauth2/nativeclient")
    if "access_token" not in result:
        print("Failed:", result.get("error_description", result))
        return
    print("Login successful!")
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(token, f)
    print(f"Saved to {TOKEN_PATH}. Outlook Mail is connected.")


if __name__ == "__main__":
    main()
