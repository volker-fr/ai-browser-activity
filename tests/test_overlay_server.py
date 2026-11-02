import importlib.util
import io
import json
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
        self.assertIn('href="/ai-overlay.user.js"', panel)
        self.assertNotIn("OpenAI", panel)

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


if __name__ == "__main__":
    unittest.main()
