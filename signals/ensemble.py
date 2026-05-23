"""Ensemble signal combiner.

Combines LLM, cross-platform, and news signals into a single
calibrated probability estimate. Handles missing signals by
renormalizing weights.
"""

import logging
from typing import Optional, TYPE_CHECKING

from config import settings
from core.models import Signal, Market

if TYPE_CHECKING:
    from strategy.adaptive_calibration import AdaptiveCalibrator

logger = logging.getLogger(__name__)

# Signal freshness thresholds (minutes)
MAX_SIGNAL_AGE = {
    "llm_forecaster": 30,
    "cross_platform": 60,
    "news": 15,
}


def compute_ensemble(
    llm_signal: Optional[Signal],
    cross_platform_signal: Optional[Signal] = None,
    news_signal: Optional[Signal] = None,
    weights: Optional[dict] = None,
    calibrator: Optional["AdaptiveCalibrator"] = None,
) -> Optional[tuple[float, float, dict]]:
    """
    Combine available signals into a single probability estimate.

    Returns (ensemble_prob, confidence, breakdown) or None if no usable signals.

    breakdown is a dict of source -> probability for audit trail.
    """
    base_weights = weights or settings.SIGNAL_WEIGHTS

    # Collect available, fresh signals
    active: dict[str, Signal] = {}

    if llm_signal is not None:
        age = llm_signal.age_minutes
        if age < MAX_SIGNAL_AGE["llm_forecaster"]:
            active["llm"] = llm_signal  # key matches settings.SIGNAL_WEIGHTS
        else:
            logger.debug(f"LLM signal stale ({age:.1f}m > {MAX_SIGNAL_AGE['llm_forecaster']}m)")

    if cross_platform_signal is not None:
        age = cross_platform_signal.age_minutes
        if age < MAX_SIGNAL_AGE["cross_platform"]:
            active["cross_platform"] = cross_platform_signal
        else:
            logger.debug(f"Cross-platform signal stale ({age:.1f}m)")

    if news_signal is not None:
        age = news_signal.age_minutes
        if age < MAX_SIGNAL_AGE["news"]:
            active["news"] = news_signal
        else:
            logger.debug(f"News signal stale ({age:.1f}m)")

    # LLM signal is required — it's our primary edge
    if "llm" not in active:
        logger.debug("No fresh LLM signal — skipping ensemble")
        return None

    # Renormalize weights to sum to 1.0 over available sources
    available_weight = sum(
        base_weights.get(source, 0) for source in active
    )
    if available_weight == 0:
        return None

    normalized = {
        source: base_weights.get(source, 0) / available_weight
        for source in active
    }

    # Weighted average
    ensemble_prob = sum(
        normalized[source] * signal.probability
        for source, signal in active.items()
    )
    ensemble_prob = max(0.01, min(0.99, ensemble_prob))

    # Apply adaptive calibration if available
    if calibrator is not None:
        original_prob = ensemble_prob
        ensemble_prob = calibrator.apply_calibration(ensemble_prob)
        if abs(ensemble_prob - original_prob) > 0.01:
            logger.info(f"Calibrated probability: {original_prob:.2%} → {ensemble_prob:.2%}")

    # Confidence: minimum across active signals, boosted by agreement
    min_conf = min(s.confidence for s in active.values())
    if len(active) > 1:
        probs = [s.probability for s in active.values()]
        spread = max(probs) - min(probs)
        agreement_bonus = max(0, 0.2 - spread)  # up to +0.2 when signals agree
        confidence = min(1.0, min_conf + agreement_bonus)
    else:
        confidence = min_conf

    breakdown = {
        source: {
            "probability": round(signal.probability, 3),
            "confidence": round(signal.confidence, 3),
            "weight": round(normalized[source], 3),
            "age_minutes": round(signal.age_minutes, 1),
        }
        for source, signal in active.items()
    }

    logger.debug(
        f"Ensemble: {ensemble_prob:.3f} (conf={confidence:.2f}) "
        f"from {list(active.keys())}"
    )

    return round(ensemble_prob, 3), round(confidence, 3), breakdown
