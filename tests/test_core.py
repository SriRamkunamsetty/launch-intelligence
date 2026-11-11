import csv
import json
import os
import tempfile
import unittest

from launch_intel.db import connect, insert_id
from launch_intel.ingest import QuotaExceeded, YouTubeClient, import_csv, store_row
from launch_intel.metrics import anomalies, benchmark_percentile, build_facts, engagement_rate, launch_velocity, share_of_voice
from launch_intel.narrator import numbers_in, render, template_narrative, verify
from launch_intel.pipeline import collect_once, load_config, schedule
from launch_intel.sample_data import seed


def tiny():
    c = connect()
    tid = insert_id(c, "INSERT INTO titles (name, genre) VALUES ('T','g')")
    store_row(c, tid, "youtube", "a", "trailer", "2026-01-01T00:00:00Z", "2026-01-01T12:00:00Z", 1000, 100, 10, 10)
    store_row(c, tid, "youtube", "a", "trailer", "2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z", 4000, 400, 40, 40)
    return c, tid


class Metrics(unittest.TestCase):
    def test_interpolation_and_unobserved_window(self):
        c, t = tiny()
        self.assertAlmostEqual(launch_velocity(c, t, 24)[0], 2000)   # 1000 + 3000 * 12/36
        self.assertIsNone(launch_velocity(c, t, 72)[0])              # not observed -> None, not a guess

    def test_engagement_rate(self):
        c, t = tiny()
        self.assertAlmostEqual(engagement_rate(c, t), 480 / 4000)

    def test_sov_sums_to_one_and_percentile(self):
        c = connect(); seed(c)
        ids = [r["id"] for r in c.execute("SELECT id FROM titles")]
        self.assertAlmostEqual(sum(share_of_voice(c, ids).values()), 1.0)
        pcts = [benchmark_percentile(c, i) for i in ids[:4]]          # 4 action titles
        self.assertEqual(sorted(pcts), [0.0, 100 / 3, 200 / 3, 100.0])

    def test_spike_detected_and_explained_by_event(self):
        c = connect(); seed(c)
        spiky = anomalies(c, 1)
        self.assertEqual(len(spiky), 1)                                # spike + rebound = one episode
        self.assertEqual(spiky[0]["direction"], "up")
        self.assertGreater(spiky[0]["views"], 2e6)                     # reports the peak, not the onset day
        self.assertIn("Primetime TV spot", spiky[0]["events"])
        self.assertEqual(anomalies(c, 5), [])                         # a calm title is not flagged


class Ingest(unittest.TestCase):
    def _csv(self, rows):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="")
        w = csv.DictWriter(f, fieldnames=["post_id", "title", "platform", "published_at", "captured_at", "views", "likes"])
        w.writeheader(); w.writerows(rows); f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def test_csv_accepts_good_and_rejects_bad_rows(self):
        ok = dict(post_id="1", title="X", platform="TikTok", published_at="2026-01-01T00:00:00Z", captured_at="2026-01-02T00:00:00Z", views="10", likes="1")
        bad = dict(ok, post_id="2", captured_at="2025-12-31T00:00:00Z")
        res = import_csv(connect(), self._csv([ok, bad]))
        self.assertEqual(res["accepted"], 1)
        self.assertIn("before published_at", res["rejected"][0]["reason"])

    def test_csv_missing_columns(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False); f.write("a,b\n1,2\n"); f.close()
        self.addCleanup(os.unlink, f.name)
        with self.assertRaises(ValueError):
            import_csv(connect(), f.name)

    def test_youtube_retry_quota_and_hard_error(self):
        body = json.dumps({"items": [{"id": "v", "snippet": {"publishedAt": "2026-01-01T00:00:00Z"},
                                      "statistics": {"viewCount": "5", "likeCount": "1", "commentCount": "0"}}]})
        seq, waits = [(503, ""), (429, ""), (200, body)], []
        cl = YouTubeClient("k", fetch=lambda u: seq.pop(0), sleep=waits.append)
        self.assertEqual(cl.video_stats(["v"])[0]["views"], 5)
        self.assertEqual(waits, [1, 2])                               # exponential backoff
        with self.assertRaises(QuotaExceeded):
            YouTubeClient("k", fetch=lambda u: (200, body), daily_quota=0).video_stats(["v"])
        with self.assertRaises(RuntimeError):
            YouTubeClient("k", fetch=lambda u: (403, "no"), sleep=lambda s: None).video_stats(["v"])


class Narrator(unittest.TestCase):
    def test_number_extraction_skips_windows_and_years(self):
        got = [x for _, x, _ in numbers_in("In 2026 the 72 hours and 7-day totals hit 1.2M and 5.5% on day 3")]
        self.assertEqual(got, [1.2e6, 0.055, 3.0])

    def test_verify_catches_fabricated_number(self):
        c = connect(); seed(c)
        facts = build_facts(c, 1, range(2, 13))
        self.assertEqual(verify(render("[[f:velocity_72h]] views", facts), facts).unsupported, [])
        self.assertEqual(verify("Views grew 340% in 72 hours", facts).unsupported, ["340%"])
        self.assertEqual(verify("[[f:nope]]", facts).unknown_markers, ["nope"])

    def test_template_narrative_is_fully_grounded(self):
        c = connect(); seed(c)
        facts = build_facts(c, 1, range(2, 13))
        text = render(template_narrative("Iron Meridian", facts, c, 1), facts)
        self.assertEqual(verify(text, facts).groundedness, 1.0)
        self.assertIn("Primetime TV spot", text)


class Pipeline(unittest.TestCase):
    def _fetch(self):
        body = json.dumps({"items": [{"id": "v1", "snippet": {"publishedAt": "2026-01-01T00:00:00Z"}, "statistics": {"viewCount": "9", "likeCount": "1", "commentCount": "0"}}]})
        calls = []
        def fetch(url):
            calls.append(url); return 200, body
        return fetch, calls

    def test_collect_config_and_isolation_of_failures(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump({"titles": [{"name": "A", "genre": "g", "videos": {"trailer": ["v1"]}}]}, f); f.close()
        cfg = load_config(f.name); os.unlink(f.name)
        fetch, calls = self._fetch()
        c = connect()
        s = collect_once(c, YouTubeClient("k", fetch=fetch), cfg)
        self.assertEqual((s["titles"], s["videos"], s["errors"]), (1, 1, []))
        bad = {"titles": [{"name": "B", "videos": {"trailer": ["x"]}}, cfg["titles"][0]]}
        client = YouTubeClient("k", fetch=lambda u: (403, "forbidden") if "id=x" in u else self._fetch()[0](u), sleep=lambda s: None)
        s = collect_once(c, client, bad)
        self.assertEqual(s["titles"], 1); self.assertEqual(len(s["errors"]), 1)   # B failed, A still collected
        with self.assertRaises(ValueError):
            tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False); json.dump({"titles": [{"name": "x"}]}, tmp); tmp.close(); load_config(tmp.name)

    def test_schedule_runs_and_stops_on_repeated_failure(self):
        fetch, _ = self._fetch(); c = connect(); sleeps = []
        cfg = {"titles": [{"name": "A", "videos": {"trailer": ["v1"]}}]}
        self.assertEqual(schedule(c, YouTubeClient("k", fetch=fetch), cfg, 60, max_runs=3, sleep=sleeps.append, log=lambda *a: None), 3)
        self.assertEqual(sleeps, [60, 60])
        failing = YouTubeClient("k", fetch=lambda u: (403, "x"), sleep=lambda s: None)
        n = schedule(c, failing, cfg, 1, max_runs=50, sleep=lambda s: None, log=lambda *a: None, max_consecutive_failures=3)
        self.assertEqual(n, 3)



if __name__ == "__main__":
    unittest.main()
