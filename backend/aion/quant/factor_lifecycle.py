"""Conservative walk-forward lifecycle checks for persisted factors.

A factor is removed from trading eligibility (ACTIVE -> WATCH) when its recent
holdout IC is too weak or reverses the direction established in research.
WATCH factors can return to ACTIVE only after passing the same deterministic
out-of-sample gate. Insufficient data never changes status.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from aion.quant.factors import FactorFormula, evaluate_factor

MIN_LIFECYCLE_SAMPLES = 40
MIN_HOLDOUT_ABS_IC = 0.01
MAX_CONSECUTIVE_VALIDATION_FAILURES = 3


def should_retire_after_failures(recent_passed: Sequence[bool], current_passed: bool,
                                 threshold: int = MAX_CONSECUTIVE_VALIDATION_FAILURES) -> bool:
    """Retire only after repeated consecutive failures; history is never deleted."""
    if current_passed:
        return False
    streak = 1
    for passed in recent_passed:
        if passed:
            break
        streak += 1
    return streak >= threshold


@dataclass(frozen=True, slots=True)
class LifecycleDecision:
    factor_name: str
    previous_status: str
    new_status: str
    holdout_ic: float | None
    samples: int
    reason: str


def assess_factor_lifecycle(record: Any, candles: Sequence[Any]) -> LifecycleDecision:
    previous = str(getattr(record, "status", "ACTIVE")).upper()
    unchanged = LifecycleDecision(
        factor_name=str(record.factor_name), previous_status=previous,
        new_status=previous, holdout_ic=None, samples=0,
        reason="insufficient_or_invalid_data",
    )
    if previous not in {"TESTING", "ACTIVE", "WATCH"}:
        return LifecycleDecision(record.factor_name, previous, previous, None, 0, "terminal_status")
    try:
        stats = evaluate_factor(
            candles, FactorFormula(record.formula), int(record.lookback_bars),
            int(record.horizon_bars), direction=str(record.direction),
            min_samples=MIN_LIFECYCLE_SAMPLES, holdout_fraction=0.4,
        )
    except (ValueError, TypeError, AttributeError):
        return unchanged
    holdout = stats.ic_holdout
    if stats.samples < MIN_LIFECYCLE_SAMPLES or holdout is None:
        return LifecycleDecision(record.factor_name, previous, previous, holdout, stats.samples, "insufficient_holdout")
    expected_positive = str(record.direction).upper() == "POSITIVE"
    direction_matches = (holdout > 0) == expected_positive
    robust = direction_matches and abs(holdout) >= MIN_HOLDOUT_ABS_IC
    new_status = ("WATCH" if previous == "TESTING" else "ACTIVE") if robust else "WATCH"
    reason = ("validation_passed_to_watch" if previous == "TESTING" else "holdout_revalidated") if robust else ("holdout_direction_reversed" if not direction_matches else "holdout_ic_decayed")
    return LifecycleDecision(record.factor_name, previous, new_status, holdout, stats.samples, reason)


__all__ = ["LifecycleDecision", "assess_factor_lifecycle", "should_retire_after_failures"]
