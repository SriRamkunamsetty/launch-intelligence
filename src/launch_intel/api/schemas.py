"""Pydantic v2 Request & Response Data Contracts for Launch Intelligence API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "1.0.0"
    database: str = "connected"
    timestamp: str


class TitleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Official title name")
    genre: Optional[str] = Field(None, max_length=64, description="Entertainment genre e.g. action, drama")
    studio: Optional[str] = Field(None, max_length=128, description="Producing studio or distributor")
    release_date: Optional[str] = Field(None, description="Theatrical or platform premiere date (ISO-8601)")


class TitleResponse(BaseModel):
    id: int
    name: str
    genre: Optional[str] = None
    studio: Optional[str] = None
    release_date: Optional[str] = None
    posts_count: int = 0
    velocity_72h: Optional[str] = None


class FactLedgerItem(BaseModel):
    id: str
    label: str
    value: float
    formatted: str
    query: str


class EventItem(BaseModel):
    hours_from_first_post: float
    label: str
    kind: str


class AnomalyItem(BaseModel):
    day: int
    views: float
    direction: str
    severity: str
    events: List[str]


class AnalyticsResponse(BaseModel):
    title_id: int
    title_name: str
    genre: Optional[str]
    narrative: str
    is_claim_verified: bool
    groundedness_score: float
    claims_count: int
    verification_problems: List[str]
    facts_ledger: List[FactLedgerItem]
    series_points: List[List[float]]  # [[hour, views], ...]
    timeline_events: List[EventItem]
    anomalies: List[AnomalyItem]


class ForecastMilestone(BaseModel):
    hours: int
    predicted: float
    lower: float
    upper: float
    is_projected: bool


class TrajectoryForecastResponse(BaseModel):
    title_id: int
    title_name: str
    observed_hours: float
    current_views: float
    model_name: str
    r2_score: float
    milestones: Dict[str, ForecastMilestone]
    curve: List[Dict[str, Any]]
    is_converged: bool
    notes: List[str]


class CommentBatchRequest(BaseModel):
    comments: List[str] = Field(..., min_length=1, max_length=5000)


class AspectScoreSchema(BaseModel):
    aspect: str
    mention_count: int
    positive_ratio: float
    sentiment_score: float
    sample_snippets: List[str]


class SentimentAnalysisResponse(BaseModel):
    title_id: int
    total_comments_analyzed: int
    overall_sentiment_score: float
    sentiment_label: str
    positive_pct: float
    neutral_pct: float
    negative_pct: float
    aspects: Dict[str, AspectScoreSchema]
    top_hype_drivers: List[str]
    top_criticism_drivers: List[str]
    estimated_bot_ratio: float


class ExecutiveBriefingResponse(BaseModel):
    title_id: int
    title_name: str
    narrative: str
    strategic_recommendations: List[str]
    causal_analysis: Dict[str, Any]
    projected_terminal_views: Optional[float]
    groundedness_score: float
    is_claim_verified: bool
    verification_reasons: List[str]
    model_provider: str
    facts_ledger: List[Dict[str, Any]]
