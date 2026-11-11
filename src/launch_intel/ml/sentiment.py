"""Aspect-Based Sentiment & Audience NLP Analyzer.
Extracts aspect sentiment (VFX, cast, music, storyline, hype) from viewer comments,
detects polarity distribution, and estimates organic vs bot reaction ratios.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List


@dataclass
class AspectScore:
    aspect: str
    mention_count: int
    positive_ratio: float
    sentiment_score: float  # -1.0 to 1.0
    sample_snippets: List[str] = field(default_factory=list)


@dataclass
class SentimentSummary:
    title_id: int
    total_comments_analyzed: int
    overall_sentiment_score: float  # -1.0 to +1.0
    sentiment_label: str            # "VERY POSITIVE" | "POSITIVE" | "NEUTRAL" | "MIXED" | "NEGATIVE"
    positive_pct: float
    neutral_pct: float
    negative_pct: float
    aspects: Dict[str, AspectScore]
    top_hype_drivers: List[str]
    top_criticism_drivers: List[str]
    estimated_bot_ratio: float


# Aspect lexicons for entertainment marketing
ASPECT_PATTERNS = {
    "visuals_cgi": re.compile(r"\b(cgi|vfx|visuals?|cinematography|shots?|graphics?|effects?|looks? (?:amazing|clean|cheap|bad))\b", re.I),
    "cast_acting": re.compile(r"\b(cast|actor|actress|acting|performance|role|lead|character|chemistry)\b", re.I),
    "story_lore": re.compile(r"\b(story|plot|writing|lore|canon|book|adaptation|script|ending|twist)\b", re.I),
    "music_audio": re.compile(r"\b(music|soundtrack|score|sound|song|theme|audio|beats?)\b", re.I),
    "release_hype": re.compile(r"\b(hype|hyped|cant wait|can't wait|take my money|day one|tickets?|premiere|cinema|theater)\b", re.I),
}

POSITIVE_WORDS = {
    "great", "amazing", "incredible", "masterpiece", "epic", "hyped", "love", "fire",
    "peak", "goated", "insane", "chills", "cinematic", "awesome", "perfection", "phenomenal"
}

NEGATIVE_WORDS = {
    "trash", "bad", "terrible", "awful", "cheap", "mid", "flop", "boring", "cringe",
    "disaster", "ruined", "hate", "unnecessary", "disappointing", "worse", "lazy"
}

BOT_PATTERNS = re.compile(r"(telegram|whatsapp|crypto|check my bio|free gift|sub to my channel|whatsapp me|\b\d{10}\b)", re.I)


class AspectSentimentAnalyzer:
    """Analyzes viewer text comments across core entertainment launch aspects."""

    def analyze(self, title_id: int, comments: List[str]) -> SentimentSummary:
        if not comments:
            return self._empty_summary(title_id)

        clean_comments = []
        bot_count = 0
        for c in comments:
            text = c.strip()
            if not text:
                continue
            if BOT_PATTERNS.search(text):
                bot_count += 1
                continue
            clean_comments.append(text)

        n = len(clean_comments)
        if n == 0:
            return self._empty_summary(title_id)

        pos_count = 0
        neg_count = 0
        neu_count = 0

        aspect_matches: Dict[str, Dict[str, Any]] = {
            k: {"count": 0, "pos": 0, "neg": 0, "samples": []}
            for k in ASPECT_PATTERNS
        }

        hype_drivers = []
        crit_drivers = []

        for text in clean_comments:
            words = set(re.findall(r"\b\w+\b", text.lower()))
            has_pos = bool(words & POSITIVE_WORDS)
            has_neg = bool(words & NEGATIVE_WORDS)

            if has_pos and not has_neg:
                pos_count += 1
                if len(hype_drivers) < 8 and len(text) < 120:
                    hype_drivers.append(text)
            elif has_neg and not has_pos:
                neg_count += 1
                if len(crit_drivers) < 8 and len(text) < 120:
                    crit_drivers.append(text)
            else:
                neu_count += 1

            # Tag aspects
            for asp, pattern in ASPECT_PATTERNS.items():
                if pattern.search(text):
                    aspect_matches[asp]["count"] += 1
                    if has_pos:
                        aspect_matches[asp]["pos"] += 1
                    if has_neg:
                        aspect_matches[asp]["neg"] += 1
                    if len(aspect_matches[asp]["samples"]) < 3:
                        aspect_matches[asp]["samples"].append(text[:100])

        pos_pct = round((pos_count / n) * 100.0, 1)
        neg_pct = round((neg_count / n) * 100.0, 1)
        neu_pct = round((neu_count / n) * 100.0, 1)

        # Net sentiment index between -1.0 and +1.0
        sentiment_score = round((pos_count - neg_count) / max(n, 1), 3)

        if sentiment_score >= 0.45:
            sentiment_label = "VERY POSITIVE"
        elif sentiment_score >= 0.15:
            sentiment_label = "POSITIVE"
        elif sentiment_score <= -0.30:
            sentiment_label = "NEGATIVE"
        elif sentiment_score <= -0.10:
            sentiment_label = "MIXED NEGATIVE"
        elif pos_pct > 25.0 and neg_pct > 25.0:
            sentiment_label = "POLARIZING / MIXED"
        else:
            sentiment_label = "BALANCED / NEUTRAL"

        aspect_results: Dict[str, AspectScore] = {}
        for asp, data in aspect_matches.items():
            cnt = data["count"]
            if cnt > 0:
                pos_ratio = round((data["pos"] / cnt) * 100.0, 1)
                net_asp = round((data["pos"] - data["neg"]) / cnt, 2)
            else:
                pos_ratio = 50.0
                net_asp = 0.0

            aspect_results[asp] = AspectScore(
                aspect=asp,
                mention_count=cnt,
                positive_ratio=pos_ratio,
                sentiment_score=net_asp,
                sample_snippets=data["samples"]
            )

        bot_ratio = round((bot_count / max(len(comments), 1)) * 100.0, 1)

        return SentimentSummary(
            title_id=title_id,
            total_comments_analyzed=n,
            overall_sentiment_score=sentiment_score,
            sentiment_label=sentiment_label,
            positive_pct=pos_pct,
            neutral_pct=neu_pct,
            negative_pct=neg_pct,
            aspects=aspect_results,
            top_hype_drivers=hype_drivers[:5],
            top_criticism_drivers=crit_drivers[:5],
            estimated_bot_ratio=bot_ratio
        )

    def _empty_summary(self, title_id: int) -> SentimentSummary:
        return SentimentSummary(
            title_id=title_id,
            total_comments_analyzed=0,
            overall_sentiment_score=0.0,
            sentiment_label="NO DATA",
            positive_pct=0.0,
            neutral_pct=0.0,
            negative_pct=0.0,
            aspects={
                asp: AspectScore(aspect=asp, mention_count=0, positive_ratio=0.0, sentiment_score=0.0)
                for asp in ASPECT_PATTERNS
            },
            top_hype_drivers=[],
            top_criticism_drivers=[],
            estimated_bot_ratio=0.0
        )
