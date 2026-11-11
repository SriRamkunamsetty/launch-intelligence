"""Enterprise Test Suite: AI/ML Models, FastAPI REST Endpoints, and Observability."""
import unittest
from fastapi.testclient import TestClient

from launch_intel.ai.agent import AgenticLaunchAnalyst, DeterministicHeuristicProvider
from launch_intel.api.app import create_app
from launch_intel.db import connect
from launch_intel.ml.anomalies import EnsembleAnomalyDetector
from launch_intel.ml.sentiment import AspectSentimentAnalyzer
from launch_intel.ml.trajectory import MLTrajectoryForecaster
from launch_intel.sample_data import seed_all


class TestEnterpriseML(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.conn = connect(check_same_thread=False)
        seed_all(cls.conn)

    def test_trajectory_forecaster_bass_diffusion(self):
        forecaster = MLTrajectoryForecaster(forecast_days=30)
        pred = forecaster.forecast_title(self.conn, 1, "Iron Meridian")

        self.assertEqual(pred.title_id, 1)
        self.assertIn(pred.model_name, ("bass_diffusion", "saturation_exponential", "log_linear_heuristic"))
        self.assertTrue(pred.is_converged)
        self.assertGreater(pred.r2_score, 0.70)
        self.assertIn("day_30", pred.milestones)
        self.assertGreater(pred.milestones["day_30"]["predicted"], pred.current_views)
        self.assertTrue(len(pred.curve) > 10)

    def test_ensemble_anomaly_detector(self):
        detector = EnsembleAnomalyDetector(mad_threshold=3.0)
        episodes = detector.detect(self.conn, 1)  # Iron Meridian has the injected TV spot spike

        self.assertGreaterEqual(len(episodes), 1)
        first_ep = episodes[0]
        self.assertEqual(first_ep.direction, "up")
        self.assertGreater(first_ep.views, 2_000_000)
        self.assertIn(first_ep.severity, ("MEDIUM", "HIGH", "CRITICAL"))
        self.assertTrue(len(first_ep.correlated_events) > 0)
        self.assertEqual(first_ep.correlated_events[0]["label"], "Primetime TV spot")

    def test_aspect_sentiment_analyzer(self):
        analyzer = AspectSentimentAnalyzer()
        sample_comments = [
            "The CGI and visual effects look absolutely amazing and clean!",
            "The soundtrack and music gave me literal chills, peak cinema",
            "Worst casting choice ever, terrible acting and cringe performance",
            "Can't wait for premiere day, take my money already!",
            "Total disaster, ruined the story and book canon completely",
            "Check out my bio for free gifts on telegram +1234567890",  # spam bot
        ]
        summary = analyzer.analyze(title_id=1, comments=sample_comments)

        self.assertEqual(summary.total_comments_analyzed, 5)  # 1 bot filtered
        self.assertGreater(summary.estimated_bot_ratio, 0.0)
        self.assertIn("visuals_cgi", summary.aspects)
        self.assertGreater(summary.aspects["visuals_cgi"].mention_count, 0)
        self.assertIn("music_audio", summary.aspects)
        self.assertGreater(summary.aspects["music_audio"].mention_count, 0)
        self.assertIn("cast_acting", summary.aspects)
        self.assertGreater(summary.aspects["cast_acting"].mention_count, 0)
        self.assertTrue(len(summary.top_hype_drivers) > 0)
        self.assertTrue(len(summary.top_criticism_drivers) > 0)

    def test_agentic_launch_analyst(self):
        analyst = AgenticLaunchAnalyst(provider=DeterministicHeuristicProvider())
        briefing = analyst.generate_briefing(self.conn, 1)

        self.assertEqual(briefing.title_id, 1)
        self.assertEqual(briefing.title_name, "Iron Meridian")
        self.assertEqual(briefing.groundedness_score, 1.0)
        self.assertTrue(briefing.is_claim_verified)
        self.assertIn("Primetime TV spot", briefing.causal_analysis["causal_assessment"])
        self.assertGreater(len(briefing.strategic_recommendations), 1)


class TestEnterpriseAPI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app = create_app()
        conn = connect(check_same_thread=False)
        seed_all(conn)
        app.state.conn = conn
        cls.client = TestClient(app)

    def test_health_and_ready(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "ok")

        r = self.client.get("/readyz")
        self.assertEqual(r.status_code, 200)

    def test_prometheus_metrics(self):
        r = self.client.get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertIn("launch_intel_http_requests_total", r.text)

    def test_titles_api(self):
        r = self.client.get("/api/v1/titles")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(len(data), 12)
        self.assertEqual(data[0]["name"], "Iron Meridian")

    def test_analytics_endpoint(self):
        r = self.client.get("/api/v1/analytics/1")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["is_claim_verified"])
        self.assertEqual(data["groundedness_score"], 1.0)
        self.assertGreater(len(data["facts_ledger"]), 5)
        self.assertGreater(len(data["series_points"]), 5)

    def test_ml_predict_endpoint(self):
        r = self.client.get("/api/v1/ml/predict/1?days=30")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["title_id"], 1)
        self.assertTrue(data["is_converged"])
        self.assertIn("day_30", data["milestones"])

    def test_ml_sentiment_endpoint(self):
        payload = {"comments": ["Incredible trailer!", "Loved the VFX and audio.", "A bit slow."]}
        r = self.client.post("/api/v1/ml/sentiment/1", json=payload)
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["total_comments_analyzed"], 3)
        self.assertIn("visuals_cgi", data["aspects"])

    def test_ai_briefing_endpoint(self):
        r = self.client.get("/api/v1/ai/briefing/1")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["title_name"], "Iron Meridian")
        self.assertTrue(data["is_claim_verified"])
        self.assertGreater(len(data["strategic_recommendations"]), 1)


if __name__ == "__main__":
    unittest.main()
