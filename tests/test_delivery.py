import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from launch_intel.catalog import import_events_csv
from launch_intel.cli import main
from launch_intel.db import connect, get_or_create_title
from launch_intel.ingest import YouTubeClient, ingest_youtube
from launch_intel.sample_data import seed_all
from launch_intel.web import App, make_handler


class Server(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        conn = connect(check_same_thread=False); seed_all(conn)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(conn)))
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()

    def get(self, p, data=None):
        try:
            with urllib.request.urlopen(urllib.request.Request(self.base + p, data=data)) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read()

    def test_page_headers_and_api(self):
        s, h, b = self.get("/")
        self.assertEqual(s, 200); self.assertIn("default-src 'none'", h["Content-Security-Policy"]); self.assertNotIn(b"innerHTML", b)
        self.assertEqual(len(json.loads(self.get("/api/titles")[2])), 12)
        t = json.loads(self.get("/api/title/1")[2])
        self.assertTrue(t["verification"]["ok"]); self.assertTrue(t["series"] and t["events"] and t["anomalies"])

    def test_errors_do_not_leak_and_writes_are_refused(self):
        for p in ("/api/title/9999", "/api/title/abc", "/etc/passwd", "/api/title/1/../../x"):
            self.assertEqual(self.get(p)[0], 404)
        self.assertEqual(self.get("/api/titles", data=b"{}")[0], 405)


class Importers(unittest.TestCase):
    def test_events_validation_and_dedupe(self):
        import csv
        c = connect(); get_or_create_title(c, "Alpha")
        rows = [dict(title="Alpha", occurred_at="2026-01-05T12:00:00Z", kind="tv_spot", label="TV spot")] * 2 + [
                dict(title="Alhpa", occurred_at="2026-01-05T12:00:00Z", kind="tv_spot", label="typo title"),
                dict(title="Alpha", occurred_at="garbage", kind="tv_spot", label="bad date"),
                dict(title="Alpha", occurred_at="2026-01-06", kind="", label="no kind")]
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        w = csv.DictWriter(f, fieldnames=["title", "occurred_at", "kind", "label"]); w.writeheader(); w.writerows(rows); f.close()
        res = import_events_csv(c, f.name); os.unlink(f.name)
        self.assertEqual((res["accepted"], len(res["rejected"])), (2, 3))
        self.assertIn("unknown title", res["rejected"][0]["reason"])
        self.assertEqual(c.execute("SELECT COUNT(*) n FROM events").fetchone()["n"], 1)

    def test_event_labels_with_digits_do_not_break_claim_verification(self):
        c = connect(); seed_all(c)
        c.execute("INSERT INTO events (title_id, occurred_at, kind, label) VALUES (1, '2026-09-07T08:00:00Z', 'teaser', 'Teaser 2 drop')")
        t = App(c).title(1)
        self.assertIn("Teaser 2 drop", t["narrative"]); self.assertTrue(t["verification"]["ok"], t["verification"]["problems"])


class RealHttpTransport(unittest.TestCase):
    def test_youtube_over_real_sockets_with_retry(self):
        state, log = {"n": 0}, []
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                log.append(self.path); state["n"] += 1
                q = parse_qs(urlsplit(self.path).query)
                if state["n"] == 1:
                    code, out = 503, "busy"
                else:
                    code, out = 200, json.dumps({"items": [{"id": i, "snippet": {"publishedAt": "2026-01-01T00:00:00Z"}, "statistics": {"viewCount": "42", "likeCount": "4", "commentCount": "1"}} for i in q["id"][0].split(",")]})
                self.send_response(code); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out.encode())
            def log_message(self, *a): pass
        srv = HTTPServer(("127.0.0.1", 0), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            cl = YouTubeClient("MYKEY", sleep=lambda s: None); cl.BASE = f"http://127.0.0.1:{srv.server_address[1]}/youtube/v3/"
            c = connect()
            self.assertEqual(ingest_youtube(c, cl, "T", ["a", "b"]), 2)
            self.assertEqual(len(log), 2); self.assertIn("key=MYKEY", log[0]); self.assertEqual(cl.used, 1)
            self.assertEqual(c.execute("SELECT views FROM snapshots LIMIT 1").fetchone()["views"], 42)
        finally:
            srv.shutdown(); srv.server_close()


class Cli(unittest.TestCase):
    def test_demo_command_and_deck(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            main(["demo"])
        self.assertIn("claims checked", buf.getvalue()); self.assertIn("ok=True", buf.getvalue())
        try:
            import pptx
        except ImportError:
            self.skipTest("python-pptx not installed")
        out = os.path.join(tempfile.mkdtemp(), "d.pptx")
        main(["deck", "--demo", "--title", "Iron Meridian", "--out", out])
        self.assertEqual(len(pptx.Presentation(out).slides), 5)


class Browser(unittest.TestCase):
    def test_ui_renders_without_errors(self):
        try:
            from playwright.sync_api import sync_playwright
            pw = sync_playwright().start(); browser = pw.chromium.launch()
        except Exception:
            self.skipTest("Playwright/Chromium not available")
        conn = connect(check_same_thread=False); seed_all(conn)
        srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(App(conn))); threading.Thread(target=srv.serve_forever, daemon=True).start()
        errors = []
        try:
            page = browser.new_page(); page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{srv.server_address[1]}/"); page.wait_for_selector("#slate button")
            for i in (0, 1, 5):
                page.locator("#slate button").nth(i).click(); page.wait_for_timeout(400)
                body = page.inner_text("main")
                for bad in ("[object", "undefined", "NaN"):
                    self.assertNotIn(bad, body)
            self.assertTrue(page.locator("main svg").count() >= 1)
        finally:
            browser.close(); pw.stop(); srv.shutdown(); srv.server_close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
