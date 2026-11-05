import importlib.util
import io
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import http.client
import sys
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode, parse_qs, urlsplit

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("shared_overlay_server", ROOT / "overlay_server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class FeedTests(unittest.TestCase):
    def setUp(self):
        state = patch.object(server, "STATE", {"version": 2, "channels": {}})
        state.start()
        self.addCleanup(state.stop)

    def request(self, method, path, fields=None, origin="http://127.0.0.1:17391"):
        handler = object.__new__(server.Handler)
        body = urlencode(fields or {}).encode()
        handler.path = path
        handler.headers = {"Host": "127.0.0.1:17391", "Origin": origin, "Content-Length": str(len(body))}
        handler.rfile, handler.wfile = io.BytesIO(body), io.BytesIO()
        result = {"headers": {}}
        handler.send_response = lambda code: result.update(status=code)
        handler.send_header = lambda name, value: result["headers"].update({name: value})
        handler.end_headers = lambda: None
        getattr(handler, "do_" + method)()
        result["body"] = handler.wfile.getvalue().decode()
        return result

    def publish(self, agent, session, message, scope="https://example.org/page"):
        return self.request("POST", "/update", {
            "token": server.TOKEN, "agent": agent, "session": session,
            "scope": scope, "message": message})

    def channel(self, agent, session):
        return server.STATE["channels"][json.dumps([agent, session], ensure_ascii=False)]

    def test_update_requires_origin_and_token_and_escapes_html(self):
        fields = {"token": server.TOKEN, "agent": "Test", "session": "session-1",
                  "scope": "https://example.com/page?secret=x", "message": "<script>bad()</script>"}
        self.assertEqual(self.request("POST", "/update", fields, "https://other.example")["status"], 403)
        self.assertEqual(self.request("POST", "/update", {**fields, "token": "wrong"})["status"], 403)
        self.assertEqual(server.STATE["channels"], {})
        self.assertEqual(self.request("POST", "/update", fields)["status"], 303)
        self.assertEqual(list(self.channel("Test", "session-1")["pages"]), ["https://example.com/page"])
        self.assertIn("&lt;script&gt;", self.request("GET", "/")["body"])

    def test_neutral_install_route_and_removed_legacy_route(self):
        expected = (ROOT / "ai-overlay.user.js").read_text()
        response = self.request("GET", "/ai-overlay.user.js")
        self.assertEqual(response["status"], 200)
        self.assertEqual(response["body"], expected)
        self.assertEqual(self.request("GET", "/openai-overlay.user.js")["status"], 404)
        panel = self.request("GET", "/")["body"]
        self.assertIn("AI - browser activity", panel)
        self.assertIn('id="userscript-http-action" href="/ai-overlay-http.user.js"', panel)
        self.assertIn('type="button" id="userscript-disk-action" disabled', panel)
        self.assertIn('File channel unavailable', panel)
        self.assertIn(f'id="userscript-disk-url" readonly value="{(ROOT / "ai-overlay-file.user.js").as_uri()}"', panel)
        self.assertIn('data-source-version="1.5.5"', panel)
        self.assertIn("Installation not detected", panel)
        variant = self.request("GET", "/ai-overlay-http.user.js")["body"]
        expected_variant = re.sub(r"(?m)^(// @(?:updateURL|downloadURL)\s+)\S+$",
                                  r"\g<1>http://127.0.0.1:17391/ai-overlay-http.user.js", expected)
        self.assertEqual(variant, expected_variant)
        self.assertEqual(variant.count("http://127.0.0.1:17391/ai-overlay-http.user.js"), 3)
        self.assertIn(str(ROOT / "ai-overlay-file.user.js"), panel)
        self.assertNotIn(".local/share/ai-harness/ai-overlay.user.js", panel)
        for key in ("@updateURL", "@downloadURL"):
            self.assertRegex(expected, rf"{key}\s+http://127\.0\.0\.1:17391/ai-overlay-http\.user\.js")
        self.assertNotIn("OpenAI", panel)

    def test_panel_nonce_and_existing_userscript_status_changes_action(self):
        response = self.request("GET", "/")
        panel = response["body"]
        nonce = re.search(r'<script nonce="([^"]+)">', panel).group(1)
        self.assertIn(f"script-src 'nonce-{nonce}'", response["headers"]["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", response["headers"]["Content-Security-Policy"])
        self.assertNotIn("script-src", self.request("GET", "/status")["headers"]["Content-Security-Policy"])
        script = re.search(r'<script nonce="[^"]+">(.*?)</script>', panel, re.S).group(1)
        harness = r"""
const vm = require('node:vm'); const assert = require('node:assert/strict');
const script = require('node:fs').readFileSync(0, 'utf8');
const status = {textContent: 'Installation not detected'};
const action = { textContent: '', primary: false,
  classList: { toggle(_name, value) { action.primary = value; } } };
let changed; const diskAction = { addEventListener() {} };
const publishForm = {addEventListener() {}, querySelector() {return {value:''};}, submit() {}};
class MutationObserver { constructor(fn) { changed = fn; } observe() {} }
vm.runInNewContext(script, { document: { getElementById: id =>
  id === 'userscript-status' ? status : id === 'userscript-http-action' ? action : diskAction,
  querySelector: () => publishForm }, MutationObserver });
assert.equal(action.textContent, 'Install / update via HTTP'); assert.equal(action.primary, false);
for (const [text, label, primary] of [
  ['Up to date: installed 1.5.4, available 1.5.4.', 'Reinstall via HTTP', false],
  ['Update available: installed 1.5.3, available 1.5.4.', 'Update via HTTP', true],
  ['Newer than source: installed 1.6.0, available 1.5.4.', 'Install older version via HTTP', false]
]) { status.textContent = text; changed(); assert.equal(action.textContent, label); assert.equal(action.primary, primary); }
"""
        subprocess.run(["node", "-e", harness], input=script, text=True, check=True, timeout=5)

    def test_panel_copy_disk_url_success_and_fallbacks(self):
        panel = self.request("GET", "/")["body"]
        script = re.search(r'<script nonce="[^"]+">(.*?)</script>', panel, re.S).group(1)
        harness = r"""
const vm = require('node:vm'); const assert = require('node:assert/strict');
const script = require('node:fs').readFileSync(0, 'utf8');
(async () => {
  for (const mode of ['success', 'denied', 'missing']) {
    let click, copied, focused = false, selected = false;
    const url = {value:'file:///source.user.js', focus() {focused=true;}, select() {selected=true;}};
    const copyStatus = {textContent:''};
    const status = {textContent:''};
    const action = {classList:{toggle(){}}};
    const diskAction = {addEventListener(_event, fn) {click=fn;}};
    const elements = {'userscript-status':status, 'userscript-http-action':action,
      'userscript-disk-url':url, 'userscript-copy-status':copyStatus, 'userscript-disk-action':diskAction,
      'publish-status':{textContent:''}};
    const publishForm = {addEventListener() {}, querySelector() {return {value:''};}, submit() {}};
    const navigator = mode === 'missing' ? {} : {clipboard: {async writeText(value) {
      if (mode === 'denied') throw new Error('denied'); copied = value;
    }}};
    class MutationObserver { observe() {} }
    vm.runInNewContext(script, {document:{getElementById:id => elements[id], querySelector: () => publishForm},
      navigator, MutationObserver});
    await click();
    if (mode === 'success') {assert.equal(copied, url.value); assert.match(copyStatus.textContent, /^Copied/);}
    else {assert.equal(focused, true); assert.equal(selected, true); assert.match(copyStatus.textContent, /^URL selected/);}
  }
})().catch(error => {console.error(error); process.exitCode=1;});
"""
        subprocess.run(["node", "-e", harness], input=script, text=True, check=True, timeout=5)

    def test_open_panel_refreshes_stale_csrf_token_before_publish_after_restart(self):
        old_panel = self.request("GET", "/")["body"]
        old_token = re.search(r'<input type="hidden" name="token" value="([^"]+)"', old_panel).group(1)
        new_token = "fresh-token-after-restart"
        with patch.object(server, "TOKEN", new_token):
            fresh_panel = self.request("GET", "/")["body"]
            script = re.search(r'<script nonce="[^"]+">(.*?)</script>', old_panel, re.S).group(1)
            harness = r"""
const vm = require('node:vm'); const assert = require('node:assert/strict');
const script = require('node:fs').readFileSync(0, 'utf8');
(async () => {
  let submitHandler, submittedToken;
  const token = {value: process.env.OLD_TOKEN};
  const form = {addEventListener(_name, fn) {submitHandler = fn;},
    querySelector() {return token;}, submit() {submittedToken = token.value;}};
  const status = {textContent: 'Installation not detected'};
  const action = {textContent: '', classList: {toggle() {}}};
  const copyStatus = {textContent: ''};
  const diskAction = {addEventListener() {}};
  const publishStatus = {textContent: ''};
  const elements = {'userscript-status':status, 'userscript-http-action':action,
    'userscript-disk-action':diskAction, 'userscript-copy-status':copyStatus,
    'publish-status':publishStatus};
  class MutationObserver { observe() {} }
  class DOMParser { parseFromString() { return {querySelector() {return {value: process.env.FRESH_TOKEN};}}; } }
  let fetched = false, prevented = false;
  const fetch = async (url, options) => {
    assert.equal(url, '/'); assert.deepEqual({...options}, {cache:'no-store', credentials:'same-origin'});
    fetched = true; return {ok:true, text:async () => '<fresh panel>'};
  };
  vm.runInNewContext(script, {document:{getElementById:id => elements[id],
    querySelector:selector => selector === 'form[action="/update"]' ? form : null},
    MutationObserver, DOMParser, fetch});
  await submitHandler({preventDefault() {prevented = true;}});
  assert.equal(prevented, true); assert.equal(fetched, true);
  assert.equal(token.value, process.env.FRESH_TOKEN); assert.equal(submittedToken, process.env.FRESH_TOKEN);
  assert.equal(publishStatus.textContent, '');
})().catch(error => {console.error(error); process.exitCode=1;});
"""
            env = dict(os.environ, OLD_TOKEN=old_token, FRESH_TOKEN=new_token)
            subprocess.run(["node", "-e", harness], input=script, text=True, check=True, timeout=5, env=env)
            self.assertIn(f'name="token" value="{new_token}"', fresh_panel)
            fields = {"token": old_token, "agent": "Agent", "session": "session", "message": "After restart"}
            self.assertEqual(self.request("POST", "/update", fields)["status"], 403)
            self.assertEqual(self.request("POST", "/update", {**fields, "token": new_token})["status"], 303)

    def test_agents_and_sessions_keep_independent_updates_for_the_same_page(self):
        identities = [("Codex", "chat-a"), ("Codex", "chat-b"), ("Other agent", "chat-a")]
        for agent, session in identities:
            self.assertEqual(self.publish(agent, session, f"Visited by {agent}/{session}")["status"], 303)
        self.assertEqual(self.publish("Codex", "chat-a", "Checked links")["status"], 303)
        state = json.loads(self.request("GET", "/status")["body"])
        self.assertEqual(state["version"], 2)
        self.assertEqual(len(state["channels"]), 3)
        for agent, session in identities:
            entry = self.channel(agent, session)["pages"]["https://example.org/page"]
            expected = "Checked links" if (agent, session) == identities[0] else f"Visited by {agent}/{session}"
            self.assertEqual(entry["message"], expected)
        self.assertEqual(len(self.channel("Codex", "chat-a")["pages"]), 1)

    def test_fallbacks_and_page_updates_are_scoped_to_each_session(self):
        self.publish("Agent", "a", "Fallback A", "")
        self.publish("Agent", "b", "Fallback B", "")
        self.publish("Agent", "a", "Page A")
        self.assertEqual(self.channel("Agent", "a")["global"]["message"], "Fallback A")
        self.assertEqual(self.channel("Agent", "b")["global"]["message"], "Fallback B")
        self.assertEqual(self.channel("Agent", "b")["pages"], {})

    def test_channel_names_with_separators_cannot_collide(self):
        self.publish("a/b", "c", "First")
        self.publish("a", "b/c", "Second")
        self.assertEqual(len(server.STATE["channels"]), 2)
        self.assertEqual(self.channel("a/b", "c")["pages"]["https://example.org/page"]["message"], "First")

    def test_missing_invalid_or_oversized_identity_is_rejected(self):
        valid = {"token": server.TOKEN, "agent": "Agent", "session": "session", "message": "Visited page"}
        for name in ("agent", "session"):
            for value in (None, "", " ", "x" * 161, "bad\nlabel"):
                with self.subTest(field=name, value=value):
                    fields = {**valid, name: value}
                    if value is None:
                        fields.pop(name)
                    self.assertEqual(self.request("POST", "/update", fields)["status"], 400)
        self.assertEqual(server.STATE["channels"], {})

    def test_panel_preserves_and_escapes_identity_without_shared_browser_storage(self):
        agent, session = '<b>Agent</b>', 'session"&value'
        response = self.publish(agent, session, "Checked page")
        location = response["headers"]["Location"]
        self.assertEqual(parse_qs(urlsplit(location).query), {"agent": [agent], "session": [session]})
        panel = self.request("GET", location)["body"]
        self.assertIn('value="&lt;b&gt;Agent&lt;/b&gt;"', panel)
        self.assertIn('value="session&quot;&amp;value"', panel)
        self.assertNotIn('<b>Agent</b>', panel)

    def test_concurrent_publishers_keep_all_session_slots(self):
        def publish(number):
            return self.publish("Agent", f"session-{number}", f"Visited page {number}")["status"]
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(publish, range(24))), [303] * 24)
        self.assertEqual(len(server.STATE["channels"]), 24)
        for number in range(24):
            entry = self.channel("Agent", f"session-{number}")["pages"]["https://example.org/page"]
            self.assertEqual(entry["message"], f"Visited page {number}")


class FileChannelTests(unittest.TestCase):
    def test_main_generates_and_refreshes_file_channel_in_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "checkout with spaces"
            root.mkdir()
            source = ROOT / "ai-overlay.user.js"
            shutil.copy(source, root / source.name)
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            code = (ROOT / "overlay_server.py").read_text().replace(
                'HOST, PORT = "127.0.0.1", 17391', f'HOST, PORT = "127.0.0.1", {port}')
            (root / "overlay_server.py").write_text(code)
            target = root / "ai-overlay-file.user.js"
            target.symlink_to(root / source.name)
            env = dict(os.environ, AI_OVERLAY_IDLE_MINUTES="0.001")
            for suffix in ("", "\n// updated\n"):
                (root / source.name).write_text(source.read_text() + suffix)
                subprocess.run([sys.executable, str(root / "overlay_server.py")], env=env,
                               check=True, capture_output=True, timeout=5)
                self.assertFalse(target.is_symlink())
                self.assertEqual((root / source.name).read_text(), source.read_text() + suffix)
                generated = target.read_text()
                self.assertEqual(generated.count(target.as_uri()), 2)
                self.assertTrue(generated.endswith(suffix or "\n"))
            unavailable = subprocess.run(
                [sys.executable, "-c", "import runpy,sys; from unittest.mock import patch\n"
                 "with patch('tempfile.NamedTemporaryFile', side_effect=PermissionError('read only')): "
                 "runpy.run_path(sys.argv[1], run_name='__main__')", str(root / "overlay_server.py")],
                env=env, check=True, capture_output=True, text=True, timeout=5)
            self.assertIn('File userscript unavailable: read only', unavailable.stderr)
            self.assertIn('exiting after', unavailable.stdout)

    def test_read_only_checkout_disables_file_channel_without_affecting_http(self):
        with patch.object(server, "FILE_CHANNEL_READY", False):
            feed = FeedTests()
            panel = feed.request("GET", "/")["body"]
            self.assertIn('id="userscript-disk-action" disabled', panel)
            self.assertIn('File channel unavailable', panel)
            self.assertEqual(feed.request("GET", "/ai-overlay-http.user.js")["status"], 200)


class ActivationTests(unittest.TestCase):
    def test_rejects_invalid_activation_environment(self):
        for env in ({"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "0"},
                    {"LISTEN_PID": "0", "LISTEN_FDS": "1"},
                    {"LISTEN_FDS": "1"}, {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "bad"}):
            with self.subTest(env=env), patch.dict(os.environ, env, clear=True):
                with self.assertRaises(ValueError):
                    server.make_server()

    def test_inherited_listener_serves_real_http(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        # Exec a separate process with an actual inherited listener at fd 3.
        code = """
import importlib.util, os, socket, sys
spec = importlib.util.spec_from_file_location('overlay', sys.argv[1])
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
fd = int(sys.argv[2]); module.PORT = int(sys.argv[3])
if fd != 3: os.dup2(fd, 3)
os.environ['LISTEN_PID'] = str(os.getpid())
os.environ['LISTEN_FDS'] = '1'
module.make_server().serve_forever()
"""
        child = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "overlay_server.py"),
                                  str(listener.fileno()), str(port)], pass_fds=(listener.fileno(),),
                                 stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        self.addCleanup(child.stderr.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.terminate)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(connection.close)
        connection.request("GET", "/ai-overlay.user.js")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.read(), (ROOT / "ai-overlay.user.js").read_bytes())


    def test_idle_exit_waits_for_requests_and_inflight_handler(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.addCleanup(listener.close)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        code = """
import importlib.util, os, sys
spec = importlib.util.spec_from_file_location('overlay', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
fd = int(sys.argv[2]); m.PORT = int(sys.argv[3])
if fd != 3: os.dup2(fd, 3)
os.environ['LISTEN_PID'] = str(os.getpid()); os.environ['LISTEN_FDS'] = '1'
with m.make_server() as httpd: httpd.serve_until_idle(0.8)
"""
        child = subprocess.Popen([sys.executable, "-c", code, str(ROOT / "overlay_server.py"),
                                  str(listener.fileno()), str(port)], pass_fds=(listener.fileno(),),
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.stderr.close)
        self.addCleanup(child.wait)
        self.addCleanup(lambda: child.terminate() if child.poll() is None else None)
        time.sleep(0.5)
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        connection.request("GET", "/status")
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()
        connection.close()
        time.sleep(0.5)
        self.assertIsNone(child.poll(), "request should extend idle deadline")
        # A partial request keeps a handler in flight beyond the idle deadline.
        slow = socket.create_connection(("127.0.0.1", port), timeout=3)
        self.addCleanup(slow.close)
        slow.sendall(f"GET /status HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n".encode())
        time.sleep(1.0)
        self.assertIsNone(child.poll(), "in-flight request must not be interrupted")
        slow.sendall(b"\r\n")
        self.assertIn(b"200 OK", slow.recv(4096))
        self.assertEqual(child.wait(timeout=3), 0)
        self.assertIn("without HTTP requests", child.stdout.read())

    def test_rejects_non_listener_and_wrong_address(self):
        code = """
import importlib.util, os, socket, sys
spec = importlib.util.spec_from_file_location('overlay', sys.argv[1])
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
s = socket.socket(); s.bind(('127.0.0.1', 0))
if sys.argv[2] == 'wrong-address': s.listen()
if s.fileno() != 3: os.dup2(s.fileno(), 3)
os.environ['LISTEN_PID'] = str(os.getpid()); os.environ['LISTEN_FDS'] = '1'
try: m.make_server()
except ValueError: sys.exit(0)
sys.exit(1)
"""
        for case in ("non-listener", "wrong-address"):
            with self.subTest(case=case):
                subprocess.run([sys.executable, "-c", code, str(ROOT / "overlay_server.py"), case],
                               check=True, timeout=5)


if __name__ == "__main__":
    unittest.main()
