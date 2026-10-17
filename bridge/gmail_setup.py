#!/usr/bin/env python3
"""
One-time Gmail setup for the iMessage bridge.
Run: python3 gmail_setup.py
Opens your browser for Google sign-in, saves a refresh token to ~/.chatbot/gmail_token.json
"""
import json
import os
import urllib.parse
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

CLIENT_ID = "294125895152-cmamrfum6a6eu44gp3nd52h7amtl9q08.apps.googleusercontent.com"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly",
          "https://www.googleapis.com/auth/gmail.send"]
REDIRECT = "http://127.0.0.1:8765/"
TOKEN_PATH = os.path.expanduser("~/.chatbot/gmail_token.json")

code_holder = {}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if "code" in q:
            code_holder["code"] = q["code"][0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>Done! You can close this tab.</h1>")
        else:
            self.send_response(400)
            self.end_headers()

    def log_message(self, *a):
        pass


def main():
    params = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
    })
    url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"
    print("Opening browser for Google sign-in...")
    webbrowser.open(url)

    server = HTTPServer(("127.0.0.1", 8765), Handler)
    server.timeout = 300
    server.handle_request()
    if "code" not in code_holder:
        print("Timed out waiting for sign-in.")
        return

    body = urllib.parse.urlencode({
        "client_id": CLIENT_ID,
        "code": code_holder["code"],
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body)
    with urllib.request.urlopen(req, timeout=30) as resp:
        token = json.load(resp)

    os.makedirs(os.path.dirname(TOKEN_PATH), exist_ok=True)
    with open(TOKEN_PATH, "w") as f:
        json.dump(token, f)
    print(f"Saved to {TOKEN_PATH}. Gmail is connected.")


if __name__ == "__main__":
    main()
