"""Sentiment analysis for live customer responses.

Production uses VADER, a well-tested lexicon and rule-based sentiment
analyzer tuned for short, social-style text. If vaderSentiment is not
available (rare in a managed environment) the module falls back to a small
rule-based lexicon so the server keeps working.
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Try to load VADER. If it's missing, fall back gracefully.
try:  # pragma: no cover
    from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

    _vader = SentimentIntensityAnalyzer()
    _VADER_AVAILABLE = True
except Exception as exc:  # pragma: no cover
    logger.warning("vaderSentiment not available, using fallback lexicon: %s", exc)
    _vader = None
    _VADER_AVAILABLE = False


# ------------------------------------------------------------------
# Fallback lexicon (kept small on purpose)
# ------------------------------------------------------------------
_POSITIVE = {
    "love", "like", "enjoy", "happy", "great", "good", "excellent",
    "amazing", "awesome", "satisfied", "perfect", "fine", "okay", "yes",
    "sure", "absolutely", "definitely", "willing", "interested", "stay",
    "keep", "continue", "appreciate", "grateful", "value", "helpful",
}

_NEGATIVE = {
    "hate", "dislike", "angry", "frustrated", "terrible", "awful",
    "horrible", "bad", "worst", "disappointed", "sad", "annoyed",
    "pissed", "upset", "unhappy", "poor", "useless", "broken", "slow",
    "expensive", "overpriced", "cancel", "quit", "leave", "refund",
    "worthless", "garbage", "scam", "problem", "issue", "bug", "fail",
    "wrong", "never", "no", "nothing", "nobody", "nowhere",
    "canceling", "cancelling", "unsubscribe", "stop",
}

_INTENSIFIERS = {
    "very", "really", "extremely", "incredibly", "so", "totally",
    "absolutely", "completely", "utterly", "highly", "super", "quite",
}

_NEGATORS = {
    "not", "no", "never", "neither", "nor", "hardly", "barely", "rarely",
    "scarcely", "seldom", "don", "doesn", "didn", "isn", "aren", "wasn",
    "weren", "haven", "hasn", "hadn", "won", "wouldn", "couldn",
    "shouldn", "can", "cannot", "cant", "wont",
}


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z']+", text.lower())


def _fallback_sentiment(text: str) -> dict[str, float | str]:
    tokens = _tokenize(text)
    score = 0.0
    negation_active = 1.0
    intensifier = 1.0

    for token in tokens:
        if token in _INTENSIFIERS:
            intensifier = 1.5
            continue
        if token in _NEGATORS:
            negation_active = -1.0
            continue

        if token in _POSITIVE:
            score += 0.4 * negation_active * intensifier
        elif token in _NEGATIVE:
            score -= 0.5 * negation_active * intensifier

        negation_active = 1.0
        intensifier = 1.0

    score = max(-1.0, min(1.0, score))
    if score > 0.15:
        label = "positive"
    elif score < -0.15:
        label = "negative"
    else:
        label = "neutral"

    return {"score": round(score, 3), "label": label}


def analyze_sentiment(text: str | None) -> dict[str, Any]:
    """Score a customer utterance and return a normalized label.

    Returns:
        {"score": float, "label": "positive" | "neutral" | "negative" | "unknown"}
    """
    if not text:
        return {"score": 0.0, "label": "unknown"}

    if _VADER_AVAILABLE and _vader is not None:
        scores = _vader.polarity_scores(text)
        compound = scores["compound"]

        # VADER's compound is in [-1, 1]. Map to our labels.
        if compound >= 0.05:
            label = "positive"
        elif compound <= -0.05:
            label = "negative"
        else:
            label = "neutral"

        return {"score": round(compound, 3), "label": label}

    return _fallback_sentiment(text)
