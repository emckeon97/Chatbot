#!/usr/bin/env python3
"""
One-time iCloud Mail setup for the iMessage bridge.
Run: python3 icloud_setup.py
You'll need an app-specific password from https://appleid.apple.com
  > Sign-In and Security > App-Specific Passwords
"""
import getpass
import json
import os

CREDS_PATH = os.path.expanduser("~/.chatbot/icloud.json")


def main():
    print("Get an app-specific password at https://appleid.apple.com")
    print("  Sign-In and Security > App-Specific Passwords > Generate")
    print()
    apple_id = input("Apple ID (email): ").strip()
    app_password = getpass.getpass("App-specific password: ").strip()
    if not apple_id or not app_password:
        print("Both are required.")
        return
    os.makedirs(os.path.dirname(CREDS_PATH), exist_ok=True)
    with open(CREDS_PATH, "w") as f:
        json.dump({"apple_id": apple_id, "app_password": app_password}, f)
    os.chmod(CREDS_PATH, 0o600)
    print(f"Saved to {CREDS_PATH}. iCloud Mail is connected.")


if __name__ == "__main__":
    main()
