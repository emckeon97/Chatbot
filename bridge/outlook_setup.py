#!/usr/bin/env python3
"""
One-time Outlook Mail setup for the iMessage bridge.
Run: pip install msal && python3 outlook_setup.py
Uses device-code flow: go to https://microsoft.com/devicelogin and enter the code.
Saves token to ~/.chatbot/outlook_token.json (auto-refreshes after).
"""
import json
import os

CLIENT_ID = "9ae67231-da3a-476b-a3f4-1f89ccb7eb5b"
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
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print("Device flow not supported:", flow.get("error_description", flow))
        return
    print(flow["message"])
    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        print("Failed:", result.get("error_description", result))
        return
    token = {
        "client_id": CLIENT_ID,
        "access_token": result["access_token"],
        "refresh_token": result.get("refresh_token"),
    }
    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(token, f)
    print(f"Saved to {TOKEN_PATH}. Outlook Mail is connected.")


if __name__ == "__main__":
    main()
