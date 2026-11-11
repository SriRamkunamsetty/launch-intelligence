"""FastAPI Route Handlers for Enterprise Launch Intelligence Platform."""
from __future__ import annotations

import os
import tempfile
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse, PlainTextResponse

from ..ai.agent import AgenticLaunchAnalyst
from ..cache import get_cache
from ..db import get_or_create_title, now_iso, parse_ts
from ..deck import build_deck
from ..metrics import build_facts, cumulative_series, title_posts
from ..ml.anomalies import EnsembleAnomalyDetector
from ..ml.sentiment import AspectSentimentAnalyzer
from ..ml.trajectory import MLTrajectoryForecaster
from ..monitoring import REGISTRY, Timer
from ..narrator import fmt, ignore_list, render, template_narrative, verify
from .schemas import (
    AnalyticsResponse,
    CommentBatchRequest,
    ExecutiveBriefingResponse,
    HealthResponse,
    SentimentAnalysisResponse,
    TitleCreate,
    TitleResponse,
    TrajectoryForecastResponse,
)

router = APIRouter()


def get_db(request: Request):
    return request.app.state.conn


# ==========================================
# Health & Observability
# ==========================================

@router.get("/healthz", response_model=HealthResponse, tags=["Observability"])
def healthz(db=Depends(get_db)):
    """Kubernetes Liveness Probe."""
    return HealthResponse(
        status="ok",
        version="1.0.0",
        database="connected",
        timestamp=now_iso()
    )


@router.get("/readyz", response_model=HealthResponse, tags=["Observability"])
def readyz(db=Depends(get_db)):
    """Kubernetes Readiness Probe."""
    try:
        db.execute("SELECT 1").fetchone()
        return HealthResponse(
            status="ok",
            version="1.0.0",
            database="connected",
            timestamp=now_iso()
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database readiness check failed: {exc}"
        )


@router.get("/metrics", response_class=PlainTextResponse, tags=["Observability"])
def prometheus_metrics():
    """Prometheus metrics scraping endpoint."""
    return Response(
        content=REGISTRY.generate_prometheus_text(),
        media_type="text/plain; version=0.0.4; charset=utf-8"
    )


# ==========================================
# Titles CRUD
# ==========================================

@router.get("/api/v1/titles", response_model=List[TitleResponse], tags=["Titles"])
def list_titles(
    genre: Optional[str] = Query(None, description="Filter by genre"),
    q: Optional[str] = Query(None, description="Search by title name"),
    db=Depends(get_db)
):
    """List tracked titles with current post counts and 72h velocity."""
    sql = "SELECT id, name, genre, studio, release_date FROM titles WHERE 1=1"
    params = []
    if genre:
        sql += " AND LOWER(genre) = LOWER(?)"
        params.append(genre)
    if q:
        sql += " AND LOWER(name) LIKE LOWER(?)"
        params.append(f"%{q}%")
    sql += " ORDER BY id ASC"

    rows = db.execute(sql, tuple(params)).fetchall()
    results = []
    for r in rows:
        facts = {x.id: x for x in build_facts(db, r["id"], [])}
        v72 = fmt(facts["velocity_72h"]) if "velocity_72h" in facts else None
        results.append(TitleResponse(
            id=r["id"],
            name=r["name"],
            genre=r["genre"],
            studio=r["studio"],
            release_date=r["release_date"],
            posts_count=len(title_posts(db, r["id"])),
            velocity_72h=v72
        ))
    return results


@router.post("/api/v1/titles", response_model=TitleResponse, status_code=status.HTTP_201_CREATED, tags=["Titles"])
def create_title(payload: TitleCreate, db=Depends(get_db)):
    """Register a new title to track."""
    tid = get_or_create_title(
        db,
        name=payload.name,
        genre=payload.genre,
        studio=payload.studio,
        release_date=payload.release_date
    )
    db.commit()
    r = db.execute("SELECT id, name, genre, studio, release_date FROM titles WHERE id=?", (tid,)).fetchone()
    return TitleResponse(
        id=r["id"],
        name=r["name"],
        genre=r["genre"],
        studio=r["studio"],
        release_date=r["release_date"],
        posts_count=0,
        velocity_72h=None
    )


# ==========================================
# Analytics & Grounded Reporting
# ==========================================

@router.get("/api/v1/analytics/{title_id}", response_model=AnalyticsResponse, tags=["Analytics"])
def get_title_analytics(title_id: int, db=Depends(get_db)):
    """Retrieve full launch metrics, timeline series, and claim-verified narrative."""
    row = db.execute("SELECT id, name, genre FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Title not found")

    cache = get_cache()
    cache_key = f"analytics:{title_id}"
    cached = cache.get(cache_key)
    if cached:
        return AnalyticsResponse(**cached)

    with Timer("launch_intel_analytics_calc_seconds"):
        peer_ids = [r["id"] for r in db.execute("SELECT id FROM titles WHERE id<>?", (title_id,))]
        facts = build_facts(db, title_id, peer_ids)
        narrative_text = render(template_narrative(row["name"], facts, db, title_id), facts)
        rep = verify(narrative_text, facts, ignore=ignore_list(db, title_id, row["name"]))

        series = cumulative_series(db, title_id, step_h=12)
        start_row = db.execute(
            "SELECT MIN(published_at) as first_pub FROM posts WHERE title_id=?", (title_id,)
        ).fetchone()

        events_list = []
        if start_row and start_row["first_pub"]:
            start_dt = parse_ts(start_row["first_pub"])
            ev_rows = db.execute(
                "SELECT occurred_at, kind, label FROM events WHERE title_id=? ORDER BY occurred_at",
                (title_id,)
            ).fetchall()
            for e in ev_rows:
                h_diff = (parse_ts(e["occurred_at"]) - start_dt).total_seconds() / 3600.0
                events_list.append({
                    "hours_from_first_post": round(h_diff, 1),
                    "label": e["label"],
                    "kind": e["kind"]
                })

        detector = EnsembleAnomalyDetector()
        episodes = detector.detect(db, title_id)
        anomalies_list = [
            {
                "day": ep.peak_day_index,
                "views": ep.views,
                "direction": ep.direction,
                "severity": ep.severity,
                "events": [ev["label"] for ev in ep.correlated_events]
            }
            for ep in episodes
        ]

        resp = AnalyticsResponse(
            title_id=title_id,
            title_name=row["name"],
            genre=row["genre"],
            narrative=narrative_text,
            is_claim_verified=rep.ok,
            groundedness_score=round(rep.groundedness, 3),
            claims_count=rep.n_numbers,
            verification_problems=rep.reasons(),
            facts_ledger=[
                {
                    "id": f.id,
                    "label": f.label,
                    "value": f.value,
                    "formatted": fmt(f),
                    "query": f.query
                }
                for f in facts
            ],
            series_points=[[float(pt[0]), float(pt[1])] for pt in series],
            timeline_events=events_list,
            anomalies=anomalies_list
        )

        cache.set(cache_key, resp.model_dump(), ttl_seconds=120)
        return resp


# ==========================================
# Machine Learning & AI Endpoints
# ==========================================

@router.get("/api/v1/ml/predict/{title_id}", response_model=TrajectoryForecastResponse, tags=["Machine Learning"])
def forecast_trajectory(title_id: int, days: int = Query(30, ge=7, le=90), db=Depends(get_db)):
    """Run Bass Diffusion & Gradient Boosted trajectory forecast with confidence intervals."""
    row = db.execute("SELECT id, name FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Title not found")

    with Timer("launch_intel_ml_inference_seconds", {"model": "bass_diffusion"}):
        forecaster = MLTrajectoryForecaster(forecast_days=days)
        pred = forecaster.forecast_title(db, title_id, row["name"])

    return TrajectoryForecastResponse(
        title_id=pred.title_id,
        title_name=pred.title_name,
        observed_hours=pred.observed_hours,
        current_views=pred.current_views,
        model_name=pred.model_name,
        r2_score=pred.r2_score,
        milestones={k: {**v} for k, v in pred.milestones.items()},
        curve=pred.curve,
        is_converged=pred.is_converged,
        notes=pred.notes
    )


@router.post("/api/v1/ml/sentiment/{title_id}", response_model=SentimentAnalysisResponse, tags=["Machine Learning"])
def analyze_sentiment(title_id: int, payload: CommentBatchRequest):
    """Analyze audience comment reaction across core aspects (VFX, cast, music, story, hype)."""
    with Timer("launch_intel_nlp_sentiment_seconds"):
        analyzer = AspectSentimentAnalyzer()
        summary = analyzer.analyze(title_id, payload.comments)

    return SentimentAnalysisResponse(
        title_id=summary.title_id,
        total_comments_analyzed=summary.total_comments_analyzed,
        overall_sentiment_score=summary.overall_sentiment_score,
        sentiment_label=summary.sentiment_label,
        positive_pct=summary.positive_pct,
        neutral_pct=summary.neutral_pct,
        negative_pct=summary.negative_pct,
        aspects={
            asp: {
                "aspect": score.aspect,
                "mention_count": score.mention_count,
                "positive_ratio": score.positive_ratio,
                "sentiment_score": score.sentiment_score,
                "sample_snippets": score.sample_snippets
            }
            for asp, score in summary.aspects.items()
        },
        top_hype_drivers=summary.top_hype_drivers,
        top_criticism_drivers=summary.top_criticism_drivers,
        estimated_bot_ratio=summary.estimated_bot_ratio
    )


@router.get("/api/v1/ai/briefing/{title_id}", response_model=ExecutiveBriefingResponse, tags=["Agentic AI"])
def get_executive_briefing(title_id: int, db=Depends(get_db)):
    """Generate agentic AI executive briefing with causal reasoning and grounded recommendations."""
    row = db.execute("SELECT id, name FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Title not found")

    with Timer("launch_intel_ai_briefing_seconds"):
        analyst = AgenticLaunchAnalyst()
        briefing = analyst.generate_briefing(db, title_id)

    return ExecutiveBriefingResponse(
        title_id=briefing.title_id,
        title_name=briefing.title_name,
        narrative=briefing.narrative,
        strategic_recommendations=briefing.strategic_recommendations,
        causal_analysis=briefing.causal_analysis,
        projected_terminal_views=briefing.projected_terminal_views,
        groundedness_score=briefing.groundedness_score,
        is_claim_verified=briefing.is_claim_verified,
        verification_reasons=briefing.verification_reasons,
        model_provider=briefing.model_provider,
        facts_ledger=briefing.facts_ledger
    )


# ==========================================
# Executive Presentation Deck Export
# ==========================================

@router.get("/api/v1/deck/{title_id}", tags=["Reporting"])
def download_executive_deck(title_id: int, db=Depends(get_db)):
    """Download verified 5-slide executive PowerPoint presentation."""
    row = db.execute("SELECT id, name FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Title not found")

    tmp_dir = tempfile.mkdtemp()
    out_path = os.path.join(tmp_dir, f"launch_report_{title_id}.pptx")
    try:
        build_deck(db, title_id, out_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Deck generation failed verification: {exc}")

    sanitized_name = "".join(c for c in row["name"] if c.isalnum() or c in (" ", "_", "-")).strip().replace(" ", "_")
    return FileResponse(
        path=out_path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=f"{sanitized_name}_Launch_Report.pptx"
    )
