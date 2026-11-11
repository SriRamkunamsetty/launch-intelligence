"""Ensemble Anomaly & Change-Point Detection.
Combines:
1. Robust MAD Z-scores on day-over-day log growth.
2. Scikit-learn Isolation Forest on multi-dimensional velocity & engagement features.
3. Bayesian Change-Point probability estimation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List
import numpy as np

try:
    from sklearn.ensemble import IsolationForest
except ImportError:
    IsolationForest = None

from ..db import parse_ts
from ..metrics import daily_views


@dataclass
class AnomalyEpisode:
    episode_id: str
    title_id: int
    peak_date: str
    peak_day_index: int
    views: float
    expected_views: float
    lift_views: float
    lift_percentage: float
    direction: str                     # "up" (surge) or "down" (drop)
    severity: str                      # "LOW" | "MEDIUM" | "HIGH" | "CRITICAL"
    mad_z_score: float
    isolation_forest_score: float
    change_point_prob: float
    confidence: float
    correlated_events: List[Dict[str, Any]]
    episode_days: List[int]
    notes: List[str] = field(default_factory=list)


class EnsembleAnomalyDetector:
    """Ensemble detector running Isolation Forest alongside robust MAD scoring."""

    def __init__(
        self,
        mad_threshold: float = 3.0,
        isolation_contamination: float = 0.10,
        event_window_hours: int = 48
    ):
        self.mad_threshold = mad_threshold
        self.contamination = isolation_contamination
        self.event_window_hours = event_window_hours

    def detect(self, conn, title_id: int) -> List[AnomalyEpisode]:
        series = daily_views(conn, title_id)
        if len(series) < 5:
            return []

        dates = [pt[0] for pt in series]
        views = np.array([pt[1] for pt in series], dtype=float)

        # 1. Day-over-day log growth
        log_growth = np.zeros(len(views))
        for i in range(1, len(views)):
            log_growth[i] = math.log((views[i] + 1.0) / (views[i - 1] + 1.0))

        # 2. Robust MAD calculation
        med = float(np.median(log_growth[1:]))
        mad = float(np.median(np.abs(log_growth[1:] - med)))
        mad_scale = max(mad, 0.05)
        z_scores = 0.6745 * (log_growth - med) / mad_scale

        # 3. Isolation Forest multi-dimensional feature space
        # Features: [log_views, log_growth, acceleration (diff of log_growth)]
        accel = np.zeros(len(views))
        for i in range(2, len(views)):
            accel[i] = log_growth[i] - log_growth[i - 1]

        X = np.column_stack([
            np.log1p(views),
            log_growth,
            accel
        ])

        if IsolationForest is not None and len(views) >= 6:
            try:
                clf = IsolationForest(contamination=self.contamination, random_state=42)
                clf.fit(X)
                # Raw anomaly score: lower is more abnormal
                raw_if_scores = clf.score_samples(X)
                # Normalize to [0, 1] anomaly likelihood
                min_s, max_s = np.min(raw_if_scores), np.max(raw_if_scores)
                if max_s > min_s:
                    if_probs = 1.0 - (raw_if_scores - min_s) / (max_s - min_s)
                else:
                    if_probs = np.zeros(len(views))
            except Exception:
                if_probs = np.zeros(len(views))
        else:
            if_probs = np.clip(np.abs(z_scores) / 5.0, 0.0, 1.0)

        # 4. Bayesian change-point heuristic
        # Ratio of variance before vs after sliding window
        cp_probs = np.zeros(len(views))
        for i in range(2, len(views) - 2):
            before = np.mean(views[max(0, i - 3):i])
            after = np.mean(views[i:min(len(views), i + 3)])
            delta = abs(after - before) / max(before, 1.0)
            cp_probs[i] = min(1.0, delta / 2.0)

        # 5. Fetch campaign events
        events_rows = conn.execute(
            "SELECT occurred_at, kind, label FROM events WHERE title_id=?",
            (title_id,)
        ).fetchall()

        # 6. Flag candidate anomalous days
        flagged = []
        for i in range(1, len(views)):
            is_mad = abs(z_scores[i]) >= self.mad_threshold
            is_if = if_probs[i] >= 0.70
            if is_mad or is_if:
                flagged.append((i, z_scores[i], if_probs[i], cp_probs[i]))

        if not flagged:
            return []

        # 7. Merge consecutive days into single episodes (a spike + rebound is 1 episode)
        episodes_raw: List[List[tuple]] = []
        current_run: List[tuple] = []
        for item in flagged:
            if current_run and item[0] != current_run[-1][0] + 1:
                episodes_raw.append(current_run)
                current_run = []
            current_run.append(item)
        if current_run:
            episodes_raw.append(current_run)

        # 8. Construct structured AnomalyEpisode objects
        episodes: List[AnomalyEpisode] = []
        for ep_idx, run in enumerate(episodes_raw):
            first_up = run[0][1] >= 0
            # Identify the peak day in the episode's lead direction
            peak_day = max(run, key=lambda t: abs(t[1]))
            i = peak_day[0]
            z_val = float(peak_day[1])
            if_score = float(peak_day[2])
            cp_val = float(peak_day[3])

            cur_v = float(views[i])
            # Counterfactual expected views: baseline using median growth from previous day
            expected_v = float(views[i - 1] * math.exp(med))
            lift_v = max(0.0, cur_v - expected_v)
            lift_pct = (lift_v / max(expected_v, 1.0)) * 100.0

            # Match events within window_hours
            mid_ts = parse_ts(f"{dates[i].isoformat()}T12:00:00Z")
            matched_events = []
            for ev in events_rows:
                ev_dt = parse_ts(ev["occurred_at"])
                diff_hours = (ev_dt - mid_ts).total_seconds() / 3600.0
                if abs(diff_hours) <= self.event_window_hours:
                    matched_events.append({
                        "label": ev["label"],
                        "kind": ev["kind"],
                        "occurred_at": ev["occurred_at"],
                        "delta_hours": round(diff_hours, 1)
                    })

            # Severity classification
            abs_z = abs(z_val)
            if abs_z >= 5.0 or if_score >= 0.90:
                severity = "CRITICAL"
            elif abs_z >= 4.0 or if_score >= 0.80:
                severity = "HIGH"
            elif abs_z >= 3.0:
                severity = "MEDIUM"
            else:
                severity = "LOW"

            confidence = round(min(1.0, 0.5 * (abs_z / 4.0) + 0.3 * if_score + 0.2 * cp_val), 3)

            notes = []
            if matched_events:
                labels_str = ", ".join(e["label"] for e in matched_events)
                notes.append(f"Correlated with campaign event(s): {labels_str}. Coincides with timing; requires causal holdout for proof.")
            else:
                notes.append("No logged marketing events within window. Investigate organic virality, influencer pick-up, or PR leaks.")

            episodes.append(AnomalyEpisode(
                episode_id=f"ep_{title_id}_{i}_{ep_idx}",
                title_id=title_id,
                peak_date=dates[i].isoformat(),
                peak_day_index=i + 1,
                views=cur_v,
                expected_views=round(expected_v),
                lift_views=round(lift_v),
                lift_percentage=round(lift_pct, 1),
                direction="up" if first_up else "down",
                severity=severity,
                mad_z_score=round(z_val, 3),
                isolation_forest_score=round(if_score, 3),
                change_point_prob=round(cp_val, 3),
                confidence=confidence,
                correlated_events=matched_events,
                episode_days=[item[0] + 1 for item in run],
                notes=notes
            ))

        return episodes
