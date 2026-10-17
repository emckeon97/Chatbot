#!/usr/bin/env python3
"""
GitHub API via personal access token.
Token lives at ~/.chatbot/github_token.txt (chmod 600).
Create one at github.com/settings/tokens with 'repo' scope.
Never share the token in chat — write it to the file directly on the Mac:
    mkdir -p ~/.chatbot && cat > ~/.chatbot/github_token.txt
    (paste token, Ctrl-D) && chmod 600 ~/.chatbot/github_token.txt
"""

import base64
import json
import os
import urllib.request
import urllib.error

TOKEN_FILE = os.path.expanduser("~/.chatbot/github_token.txt")
API = "https://api.github.com"


def _token():
    try:
        with open(TOKEN_FILE, "r") as f:
            t = f.read().strip()
            return t or None
    except FileNotFoundError:
        return None


def has_token():
    return _token() is not None


def _request(path, method="GET", body=None):
    token = _token()
    if not token:
        raise RuntimeError("GitHub token not set. Write it to ~/.chatbot/github_token.txt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path, data=data, method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Chatbot-Bridge",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e)["message"]
        except Exception:
            msg = f"HTTP {e.code}"
        raise RuntimeError(msg)


def list_repos():
    try:
        repos = _request("/user/repos?per_page=50&sort=updated")
        if not repos:
            return "No repos found."
        lines = []
        for r in repos:
            desc = (r.get("description") or "")[:60]
            lines.append(f"- {r['full_name']}" + (f": {desc}" if desc else ""))
        return "\n".join(lines)
    except Exception as e:
        return f"Failed: {e}"


def list_files(repo):
    try:
        tree = _request(f"/repos/{repo}/git/trees/main?recursive=1")
        files = sorted(
            t["path"] for t in tree.get("tree", [])
            if t.get("type") == "blob" and ".git/" not in t.get("path", "")
        )
        if not files:
            return "No files found."
        shown = files[:100]
        out = f"Files in {repo}:\n" + "\n".join(shown)
        if len(files) > 100:
            out += f"\n... ({len(files) - 100} more)"
        return out
    except Exception as e:
        return f"Failed: {e}"


def read_file(repo, path, ref="main"):
    try:
        info = _request(f"/repos/{repo}/contents/{path}?ref={ref}")
        content = info.get("content", "")
        text = base64.b64decode(content).decode("utf-8", errors="replace")
        if len(text) > 8000:
            text = text[:8000] + "\n... (truncated)"
        return text
    except Exception as e:
        return f"Failed: {e}"


def write_file(repo, path, content, message, branch="main"):
    try:
        sha = None
        try:
            info = _request(f"/repos/{repo}/contents/{path}?ref={branch}")
            sha = info.get("sha")
        except Exception:
            pass  # new file
        body = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if sha:
            body["sha"] = sha
        _request(f"/repos/{repo}/contents/{path}", method="PUT", body=body)
        return f"Pushed to {repo}/{path} on {branch}, sir."
    except Exception as e:
        return f"Failed: {e}"


def list_commits(repo, count=5):
    try:
        commits = _request(f"/repos/{repo}/commits?per_page={count}")
        if not commits:
            return "No commits found."
        return "\n".join(
            f"{c['sha'][:7]} - {(c['commit']['message'].split(chr(10))[0])}"
            for c in commits
        )
    except Exception as e:
        return f"Failed: {e}"
