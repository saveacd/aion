"""Deterministic validation of already selected expressions on unseen bars."""

from __future__ import annotations

import datetime as dt
import math
from bisect import bisect_right
from collections import defaultdict
from collections.abc import Sequence
from statistics import mean, quantiles

from aion.quant.factor_discovery import _holdout, _rank_ic, evaluate_expression, parse_expression
from aion.quant.factor_ensemble import detect_market_regime

MIN_LABELS = 60
MIN_GROUP = 8
FEE_BPS = 4.0
MIN_SLIPPAGE_BPS = 2.0
PARTICIPATION_LIMIT = 0.01
MIN_NOTIONAL_USD = 100.0
FUNDING_STRESS_PER_8H = 0.0005


def aligned_observations(
    bars: list[dict], values: Sequence, indices: Sequence[int], horizon: int, funding: Sequence[dict] = (),
) -> list[dict]:
    rows = []
    settlement_times = [event["t"] for event in funding]
    for i in indices:
        signal = values[i]
        if signal is None or not math.isfinite(signal):
            continue
        entry, exit_ = bars[i + 1], bars[i + horizon]
        entry_price, exit_price = float(entry["o"]), float(exit_["c"])
        entry_volume = entry_price * float(entry["v"])
        exit_volume = exit_price * float(exit_["v"])
        capacity = min(entry_volume, exit_volume) * PARTICIPATION_LIMIT

        def slippage(bar, notional):
            candle_range_bps = (float(bar["h"]) - float(bar["l"])) / float(bar["o"]) * 10_000
            participation = MIN_NOTIONAL_USD / max(notional, MIN_NOTIONAL_USD)
            return MIN_SLIPPAGE_BPS + candle_range_bps * math.sqrt(participation)

        cost = (FEE_BPS * 2 + slippage(entry, entry_volume) + slippage(exit_, exit_volume)) / 10_000
        first = bisect_right(settlement_times, entry["t"])
        last = bisect_right(settlement_times, exit_["end"])
        covered = (
            first > 0 and last < len(settlement_times)
            and all(
                dt.datetime.fromisoformat(right) - dt.datetime.fromisoformat(left) <= dt.timedelta(hours=8, minutes=2)
                for left, right in zip(settlement_times[first-1:last], settlement_times[first:last+1], strict=True)
            )
        )
        actual_funding = sum(float(event["rate"]) for event in funding[first:last])
        entry_time = dt.datetime.fromisoformat(entry["t"])
        exit_time = dt.datetime.fromisoformat(exit_["end"])
        elapsed = (exit_time - entry_time).total_seconds()
        extra = 0 if covered else FUNDING_STRESS_PER_8H * max(1, math.ceil(elapsed / (8 * 3600)))
        rows.append({
            "time": bars[i]["end"], "label_end": exit_["end"], "signal": signal,
            "target": exit_price / entry_price - 1, "round_trip_cost": cost, "capacity_usd": capacity,
            "funding_return_long": -actual_funding, "funding_stress": extra, "funding_covered": covered,
            "regime": detect_market_regime(bars[max(0, i - 29):i + 1])["label"],
        })
    return rows


def _payoff(row, direction):
    if row["signal"] == 0 or row["capacity_usd"] < MIN_NOTIONAL_USD:
        return 0.0
    sign = math.copysign(1, row["signal"]) * direction
    return (sign * (row["target"] + row["funding_return_long"]) -
            row["round_trip_cost"] - row["funding_stress"])


def _summary(rows, direction):
    pairs = [(row["signal"], row["target"]) for row in rows]
    return {
        "samples": len(rows), "rank_ic": _rank_ic(pairs),
        "net_return": mean(_payoff(row, direction) for row in rows) if rows else None,
    }


def window_metrics(rows: list[dict], direction: int, family_size: int) -> dict:
    pairs = [(row["signal"], row["target"]) for row in rows]
    result = _holdout(pairs, direction)
    active = [row for row in rows if row["signal"] != 0]
    feasible = [row for row in active if row["capacity_usd"] >= MIN_NOTIONAL_USD]
    grouped = defaultdict(list)
    for row in rows:
        grouped[row["regime"]].append(row)
    regimes = {regime: _summary(group, direction) for regime, group in sorted(grouped.items())}
    time_segments = [_summary(rows[i * len(rows) // 3:(i + 1) * len(rows) // 3], direction) for i in range(3)]
    capacities = [row["capacity_usd"] for row in active]
    result.update({
        "first_signal_at": rows[0]["time"], "last_label_at": rows[-1]["label_end"],
        "adjusted_p_value": min(1.0, result["p_value"] * family_size),
        "by_regime": regimes, "by_time": time_segments,
        "net_return_per_interval": mean(_payoff(row, direction) for row in rows),
        "turnover_per_interval": 2 * len(feasible) / len(rows),
        "round_trip_fee_bps": 2 * FEE_BPS,
        "average_round_trip_slippage_bps": (
            mean(row["round_trip_cost"] for row in feasible) * 10_000 - 2 * FEE_BPS if feasible else None
        ),
        "capacity_p10_usd": quantiles(capacities, n=10, method="inclusive")[0] if len(capacities) >= 2 else 0,
        "capacity_participation": PARTICIPATION_LIMIT, "notional_usd": MIN_NOTIONAL_USD,
        "feasible_ratio": len(feasible) / max(1, len(active)),
        "funding_model": "published_settlements_with_stress_for_uncovered_periods",
        "funding_coverage": sum(row["funding_covered"] for row in rows) / len(rows),
        "funding_stress_bps_per_8h": FUNDING_STRESS_PER_8H * 10_000,
        "hit_rate": sum(_payoff(row, direction) > 0 for row in feasible) / max(1, len(feasible)),
    })
    reasons = []
    if result["samples"] < MIN_LABELS:
        reasons.append("insufficient_labels")
    if (result["rank_ic"] or 0) * direction < 0.02 or result["adjusted_p_value"] > 0.05:
        reasons.append("insignificant_rank_ic")
    if any((segment["rank_ic"] or 0) * direction <= 0 for segment in time_segments):
        reasons.append("unstable_time_segments")
    if any((group["rank_ic"] or 0) * direction < -0.05 for group in regimes.values()
           if group["samples"] >= MIN_GROUP):
        reasons.append("regime_reversal")
    if result["net_return_per_interval"] <= 0:
        reasons.append("negative_after_costs")
    if result["feasible_ratio"] < 0.9 or result["capacity_p10_usd"] < MIN_NOTIONAL_USD:
        reasons.append("insufficient_capacity")
    result["reasons"] = reasons
    result["passed"] = not reasons
    return result


def validate_window(dataset: dict, expression: object, direction: int, horizon: int, *,
                    start_after: str, end_at: str | None = None, family_size: int,
                    label_limit: int | None = None) -> tuple[dict, list[dict]]:
    if not dataset["quality"]["eligible"] or len(dataset["bars"]) <= horizon:
        return {"status": "DATA_UNAVAILABLE"}, []
    bars = dataset["bars"]
    first = next((i for i, bar in enumerate(bars) if bar["end"] > start_after), len(bars))
    stop = len(bars) - horizon
    if end_at:
        stop = next((i for i in range(stop) if bars[i + horizon]["end"] > end_at), stop)
    if label_limit is not None:
        stop = min(stop, first + horizon * label_limit)
    values = evaluate_expression(parse_expression(expression), bars)
    rows = aligned_observations(bars, values, range(first, stop, horizon), horizon, dataset.get("funding", ()))
    if len(rows) < max(MIN_LABELS, 20 * family_size):
        return {"status": "INSUFFICIENT_LABELS", "samples": len(rows)}, rows
    return {"status": "SCORED", **window_metrics(rows, direction, family_size)}, rows


def portfolio_contribution(candidate: list[dict], baseline: list[dict], direction: int) -> dict:
    """Equal-notional baseline versus a 50/50 blend on the same label slots."""
    by_time = {row["time"]: row for row in baseline}
    individual = []
    blend = []
    base_signals, new_signals = [], []
    for row in candidate:
        base = by_time.get(row["time"])
        if base is None:
            continue
        base_net = _payoff(base, 1)
        individual.append(base_net)
        blend.append((base_net + _payoff(row, direction)) / 2)
        base_signals.append(math.copysign(1, base["signal"]) if base["signal"] else 0)
        new_signals.append(direction * math.copysign(1, row["signal"]) if row["signal"] else 0)
    if not individual:
        return {"status": "NO_ALIGNED_BASELINE", "samples": 0}
    overlap = mean(a == b for a, b in zip(base_signals, new_signals, strict=True))
    return {"status": "SCORED", "samples": len(individual), "baseline_net": mean(individual),
            "blend_net": mean(blend), "incremental_net": mean(blend) - mean(individual),
            "direction_agreement": overlap, "direction_distance": 1 - overlap}
