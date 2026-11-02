#!/usr/bin/env python3
"""Local status feed for the user-installed AI browser activity overlay."""

import html
import json
import secrets
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlencode, urlsplit

HOST, PORT = "127.0.0.1", 17391
ROOT = Path(__file__).resolve().parent
TOKEN = secrets.token_urlsafe(32)
LOCK = Lock()
STATE = {"version": 2, "channels": {}}


def page_key(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Enter an HTTP or HTTPS page URL.")
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body, kind="text/html; charset=utf-8", status=200):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get("Host") in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def do_GET(self):
        if not self.allowed_host():
            return self.reply("Invalid host", status=403)
        path = urlsplit(self.path).path
        if path == "/status":
            with LOCK:
                body = json.dumps(STATE)
            return self.reply(body, "application/json")
        if path == "/ai-overlay.user.js":
            return self.reply((ROOT / "ai-overlay.user.js").read_bytes(), "application/javascript; charset=utf-8")
        if path != "/":
            return self.reply("Not found", status=404)
        query = parse_qs(urlsplit(self.path).query)
        agent_value = html.escape(query.get("agent", [""])[0], quote=True)
        session_value = html.escape(query.get("session", [""])[0], quote=True)
        with LOCK:
            entries = [
                {"agent": channel["agent"], "session": channel["session"], **entry}
                for channel in STATE["channels"].values()
                for entry in [channel["global"], *channel["pages"].values()] if entry
            ]
        updates = "".join(
            "<li><strong>" + html.escape(entry["agent"]) + " · " + html.escape(entry["session"])
            + "</strong> — " + html.escape(entry["scope"] or "All pages") + ": "
            + html.escape(entry["message"]) + " <small>" + html.escape(entry["updated"]) + "</small></li>"
            for entry in entries if entry
        )
        self.reply('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI - browser activity</title>
<style>body{font:16px system-ui;max-width:760px;margin:48px auto;padding:0 24px;background:#f4f6f8;color:#18252d}
label{display:block;margin:20px 0 8px}input,textarea{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #a4b2b8;border-radius:8px}
button,a.install{display:inline-block;background:#136c58;color:white;padding:12px 18px;border:0;border-radius:8px;font:inherit;margin-top:16px;text-decoration:none}small{color:#53666f}li{margin:16px 0;overflow-wrap:anywhere}</style>
<h1>AI - browser activity</h1><p>Local feed for the AI browser overlay.</p>
<a class="install" href="/ai-overlay.user.js">Install userscript in Violentmonkey</a>
<form method="post" action="/update"><input type="hidden" name="token" value="''' + TOKEN + '''">
<label for="agent">Agent</label>
<input id="agent" name="agent" maxlength="80" required value="''' + agent_value + '''" placeholder="Codex">
<label for="session">Session ID</label>
<input id="session" name="session" maxlength="160" required value="''' + session_value + '''" placeholder="Stable chat or session ID">
<small>Use the same agent/session for this chat. Each session has its own updates.</small>
<label for="scope">Page URL (leave empty for this session’s fallback)</label>
<input id="scope" name="scope" type="url" placeholder="https://example.com/page">
<small>Matches origin and path. Query strings and fragments are ignored.</small>
<label for="message">Assistant update</label>
<textarea id="message" name="message" rows="3" maxlength="600" required></textarea>
<button type="submit">Publish local update</button></form>
<h2>Current updates</h2><ul>''' + (updates or "<li>No updates yet.</li>") + '''</ul>
<p><small>Memory only. Restarting this server clears updates. No page content or input values are collected.</small></p></html>''')

    def do_POST(self):
        if not self.allowed_host() or urlsplit(self.path).path != "/update":
            return self.reply("Forbidden", status=403)
        if self.headers.get("Origin") not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"):
            return self.reply("Invalid origin", status=403)
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192:
                raise ValueError("Invalid request size")
            fields = parse_qs(self.rfile.read(size).decode())
            if not secrets.compare_digest(fields.get("token", [""])[0], TOKEN):
                return self.reply("Invalid token", status=403)
            agent = fields.get("agent", [""])[0].strip()
            session = fields.get("session", [""])[0].strip()
            if not 0 < len(agent) <= 80 or not 0 < len(session) <= 160:
                raise ValueError("Enter an agent name (1–80 characters) and session ID (1–160 characters).")
            if any(ord(char) < 32 or ord(char) == 127 for char in agent + session):
                raise ValueError("Agent and session must not contain control characters.")
            scope = fields.get("scope", [""])[0].strip()
            scope = page_key(scope) if scope else ""
            message = fields.get("message", [""])[0].strip()
            if not message or len(message) > 600:
                raise ValueError("Enter an update of 1–600 characters.")
        except (ValueError, UnicodeError) as error:
            return self.reply(html.escape(str(error)), status=400)
        entry = {"scope": scope, "message": message, "updated": datetime.now(timezone.utc).isoformat()}
        # Encode the pair structurally: separators inside a name cannot collide.
        channel_key = json.dumps([agent, session], ensure_ascii=False)
        with LOCK:
            channel = STATE["channels"].setdefault(channel_key, {
                "agent": agent, "session": session, "global": None, "pages": {}})
            if scope:
                channel["pages"][scope] = entry
            else:
                channel["global"] = entry
        self.send_response(303)
        self.send_header("Location", "/?" + urlencode({"agent": agent, "session": session}))
        self.send_header("Content-Length", "0")
        self.end_headers()


if __name__ == "__main__":
    print(f"AI browser activity feed: http://{HOST}:{PORT}/", flush=True)
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
