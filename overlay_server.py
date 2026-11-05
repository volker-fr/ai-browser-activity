#!/usr/bin/env python3
"""Local status feed for the user-installed AI browser activity overlay."""

import html
import json
import math
import os
import re
import socket
import sys
import tempfile
import time
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
FILE_CHANNEL_READY = False


def generate_file_userscript():
    """Atomically create the optional file channel without following target symlinks."""
    target = ROOT / "ai-overlay-file.user.js"
    source = (ROOT / "ai-overlay.user.js").read_text(encoding="utf-8")
    source = re.sub(r"(?m)^(// @(?:updateURL|downloadURL)\s+)\S+$",
                    lambda match: match.group(1) + target.as_uri(), source)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=ROOT, delete=False) as f:
            temporary = Path(f.name)
            f.write(source)
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def page_key(url):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError("Enter an HTTP or HTTPS page URL.")
    return f"{parts.scheme}://{parts.netloc}{parts.path or '/'}"


class IdleHTTPServer(ThreadingHTTPServer):
    """Track real requests and let active handlers finish before idle exit."""
    def __init__(self, *args, **kwargs):
        self.activity_lock = Lock()
        self.last_request = time.monotonic()
        self.active = 0
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        request.settimeout(30)
        with self.activity_lock:
            self.active += 1
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self.activity_lock:
                self.active -= 1
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            with self.activity_lock:
                self.active -= 1

    def note_request(self):
        with self.activity_lock:
            self.last_request = time.monotonic()

    def serve_until_idle(self, idle_seconds):
        self.timeout = min(0.5, idle_seconds)
        while True:
            with self.activity_lock:
                if not self.active and time.monotonic() - self.last_request >= idle_seconds:
                    print(f"AI browser activity feed: exiting after {idle_seconds:g} seconds without HTTP requests", flush=True)
                    return
            self.handle_request()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def reply(self, body, kind="text/html; charset=utf-8", status=200, nonce=None):
        data = body.encode() if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        csp = "default-src 'none'; connect-src 'self'; style-src 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        if nonce:
            csp += f"; script-src 'nonce-{nonce}'"
        self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(data)

    def allowed_host(self):
        return self.headers.get("Host") in (f"{HOST}:{PORT}", f"localhost:{PORT}")

    def parse_request(self):
        parsed = super().parse_request()
        if parsed:
            self.server.note_request()
        return parsed

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
        if path not in ("/", "/ai-overlay-http.user.js"):
            return self.reply("Not found", status=404)
        source = (ROOT / "ai-overlay.user.js").read_text(encoding="utf-8")
        if path == "/ai-overlay-http.user.js":
            http_url = f"http://{HOST}:{PORT}/ai-overlay-http.user.js"
            source = re.sub(r"(?m)^(// @(?:updateURL|downloadURL)\s+)\S+$",
                            lambda match: match.group(1) + http_url, source)
            return self.reply(source, "application/javascript; charset=utf-8")
        version_match = re.search(r"(?m)^// @version\s+([0-9]+(?:\.[0-9]+)*)\s*$", source)
        version = version_match.group(1) if version_match else "unknown"
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
        nonce = secrets.token_urlsafe(24)
        self.reply('''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AI - browser activity</title>
<style>body{font:16px system-ui;max-width:760px;margin:48px auto;padding:0 24px;background:#f4f6f8;color:#18252d}
label{display:block;margin:20px 0 8px}input,textarea{box-sizing:border-box;width:100%;padding:12px;font:inherit;border:1px solid #a4b2b8;border-radius:8px}
button,a.install{display:inline-block;background:#e3e9e6;color:#18252d;padding:12px 18px;border:0;border-radius:8px;font:inherit;margin-top:16px;text-decoration:none}button,a.install.primary{background:#136c58;color:white}small{color:#53666f}li{margin:16px 0;overflow-wrap:anywhere}</style>
<h1>AI - browser activity</h1><p>Local feed for the AI browser overlay.</p>
<p><strong id="userscript-status" data-source-version="''' + html.escape(version, quote=True) + '''">Installation not detected in this browser/profile; it may be absent, disabled, or an older version.</strong></p>
<p>Available version: ''' + html.escape(version) + '''</p>
<a class="install" id="userscript-http-action" href="/ai-overlay-http.user.js">Install / update via HTTP</a>
<button type="button" id="userscript-disk-action"''' + ('' if FILE_CHANNEL_READY else ' disabled') + '''>Copy disk update URL</button>
<label for="userscript-disk-url">Disk update URL</label>
<input id="userscript-disk-url" readonly value="''' + html.escape((ROOT / 'ai-overlay-file.user.js').as_uri(), quote=True) + '''">
<small id="userscript-copy-status" aria-live="polite">''' + ('Paste this URL into the browser address bar.' if FILE_CHANNEL_READY else 'File channel unavailable; check the server log or use HTTP.') + '''</small>
<p>For local-file tracking, drag <code>''' + html.escape(str(ROOT / 'ai-overlay-file.user.js')) + '''</code> into Violentmonkey and select Track external edits; leave the installer open. Browsers generally block file links from this HTTP page.</p>
<p>Choose one update channel. Both options replace the same userscript; neither is an automatic fallback for the other.</p>
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
<button type="submit">Publish local update</button><small id="publish-status" aria-live="polite"></small></form>
<h2>Current updates</h2><ul>''' + (updates or "<li>No updates yet.</li>") + '''</ul>
<p><small>Memory only. Restarting this server clears updates. No page content or input values are collected.</small></p>
<script nonce="''' + nonce + '''">
(() => {
  const status = document.getElementById('userscript-status');
  const action = document.getElementById('userscript-http-action');
  const publishForm = document.querySelector('form[action="/update"]');
  const publishStatus = document.getElementById('publish-status');
  function updateAction() {
    const match = /^(?:Update available|Up to date|Newer than source): installed (\\d+(?:\\.\\d+)*), available (\\d+(?:\\.\\d+)*)\\.$/.exec(status.textContent);
    let comparison = 0;
    if (match) {
      const installed = match[1].split('.').map(Number), available = match[2].split('.').map(Number);
      for (let i = 0; i < Math.max(installed.length, available.length); i++) {
        comparison = (installed[i] || 0) - (available[i] || 0);
        if (comparison) break;
      }
    }
    action.textContent = !match ? 'Install / update via HTTP' : comparison < 0 ? 'Update via HTTP'
      : comparison > 0 ? 'Install older version via HTTP' : 'Reinstall via HTTP';
    action.classList.toggle('primary', !!match && comparison < 0);
  }
  const diskUrl = document.getElementById('userscript-disk-url');
  const copyStatus = document.getElementById('userscript-copy-status');
  document.getElementById('userscript-disk-action').addEventListener('click', async () => {
    try {
      if (!navigator.clipboard?.writeText) throw new Error('Clipboard unavailable');
      await navigator.clipboard.writeText(diskUrl.value);
      copyStatus.textContent = 'Copied. Paste into the browser address bar.';
    } catch (_) {
      diskUrl.focus(); diskUrl.select();
      copyStatus.textContent = 'URL selected. Press Ctrl+C, then paste into the address bar.';
    }
  });
  publishForm.addEventListener('submit', async event => {
    event.preventDefault();
    try {
      const response = await fetch('/', {cache: 'no-store', credentials: 'same-origin'});
      if (!response.ok) throw new Error('Could not refresh publish token');
      const page = new DOMParser().parseFromString(await response.text(), 'text/html');
      const token = page.querySelector('form[action="/update"] input[name="token"]');
      if (!token) throw new Error('Publish token missing');
      publishForm.querySelector('input[name="token"]').value = token.value;
      publishForm.submit();
    } catch (_) {
      publishStatus.textContent = 'Could not refresh the publish token. Reload the page and try again.';
    }
  });
  new MutationObserver(updateAction).observe(status, { childList: true, characterData: true, subtree: true });
  updateAction();
})();
</script></html>''', nonce=nonce)

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


def make_server():
    """Use systemd's one inherited listener, or bind normally outside systemd."""
    pid, count = os.environ.pop("LISTEN_PID", None), os.environ.pop("LISTEN_FDS", None)
    os.environ.pop("LISTEN_FDNAMES", None)
    if pid is None and count is None:
        return IdleHTTPServer((HOST, PORT), Handler)
    if pid != str(os.getpid()) or count != "1":
        raise ValueError("Expected one systemd listener for this process")
    listener = socket.socket(fileno=3)
    try:
        if (listener.family != socket.AF_INET or listener.type != socket.SOCK_STREAM
                or not listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN)
                or listener.getsockname() != (HOST, PORT)):
            raise ValueError("Invalid systemd listener: expected the local overlay address")
        listener.set_inheritable(False)
        httpd = IdleHTTPServer((HOST, PORT), Handler, bind_and_activate=False)
        httpd.socket.close()
        httpd.socket = listener
        httpd.server_address = listener.getsockname()
        return httpd
    except BaseException:
        listener.close()
        raise


if __name__ == "__main__":
    print(f"AI browser activity feed: http://{HOST}:{PORT}/", flush=True)
    minutes = float(os.environ.get("AI_OVERLAY_IDLE_MINUTES", "5"))
    if not math.isfinite(minutes) or minutes <= 0:
        raise ValueError("AI_OVERLAY_IDLE_MINUTES must be a positive finite number")
    try:
        generate_file_userscript()
        FILE_CHANNEL_READY = True
    except OSError as error:
        print(f"File userscript unavailable: {error}", file=sys.stderr, flush=True)
    with make_server() as httpd:
        httpd.serve_until_idle(minutes * 60)
