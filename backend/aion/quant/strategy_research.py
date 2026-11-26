"""Deterministic, non-executing strategy candidate backtests from active factors."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from aion.quant.backtest import BacktestConfig, Candle, PositionState, Signal, run_backtest
from aion.quant.factor_signals import normalize_factor_value
from aion.quant.factor_values import factor_series
from aion.quant.strategy_score import evaluate_strategy


@dataclass(frozen=True, slots=True)
class _FactorConsensusStrategy:
    signals: dict[datetime, float]
    kind: str = "trend_confirmation"
    threshold: float = 0.18
    min_candles: int = 1

    def on_candle(self, index: int, window: Sequence[Candle], position: PositionState | None) -> Signal:
        value = self.signals.get(window[-1].open_time)
        if value is None:
            return Signal.FLAT
        if self.kind == "trend_confirmation":
            # Require two consecutive completed bars to agree; this deliberately
            # reduces turnover rather than reacting to a single noisy print.
            previous = self.signals.get(window[-2].open_time) if len(window) >= 2 else None
            if previous is None or value * previous <= 0 or min(abs(value), abs(previous)) < self.threshold:
                return Signal.FLAT
        elif self.kind == "range_reversion":
            if abs(value) < self.threshold:
                return Signal.FLAT
            value = -value
        elif self.kind == "high_volatility_defensive":
            if abs(value) < self.threshold:
                return Signal.FLAT
        else:
            raise ValueError(f"unsupported strategy kind: {self.kind}")
        return Signal.LONG if value > 0 else Signal.SHORT


def as_backtest_candles(rows: Sequence[Any]) -> list[Candle]:
    return [Candle(
        open_time=row.open_time, open=Decimal(row.open), high=Decimal(row.high),
        low=Decimal(row.low), close=Decimal(row.close), volume=Decimal(row.volume),
        interval_seconds=max(1, round((row.close_time - row.open_time).total_seconds())),
    ) for row in rows]


def _build_consensus(records: Sequence[Any], candles: Sequence[Candle]) -> dict[datetime, float]:
    if len(records) < 2 or not candles:
        return {}
    weighted: dict[datetime, float] = {}
    weights: dict[datetime, float] = {}
    for record in records:
        try:
            series = factor_series(record, candles)
            # Equal weights avoid using IC/ICIR estimated on the full dataset
            # as hidden information inside the holdout period.
            weight = 1.0
            if record.direction == "NEGATIVE":
                sign = -1.0
            elif record.direction == "POSITIVE":
                sign = 1.0
            else:
                continue
            if weight <= 0:
                continue
            for candle, raw in zip(candles, series, strict=True):
                if raw is None:
                    continue
                signal = normalize_factor_value(record.formula, float(raw)) * sign
                weighted[candle.open_time] = weighted.get(candle.open_time, 0.0) + signal * weight
                weights[candle.open_time] = weights.get(candle.open_time, 0.0) + weight
        except (ValueError, TypeError, OverflowError):
            continue
    return {time: weighted[time] / weight for time, weight in weights.items() if weight > 0}


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


def evaluate_factor_strategy(
    records: Sequence[Any], rows: Sequence[Any], *, fee_bps: Decimal = Decimal("4"),
    slippage_bps: Decimal = Decimal("2"), max_volume_participation: Decimal = Decimal("0.10"),
    funding_rate_by_index: dict[int, Decimal] | None = None, funding_history_complete: bool = False,
    strategy_kind: str = "trend_confirmation",
) -> dict[str, Any]:
    """Run one distinct candidate through chronological IS/OOS and walk-forward gates."""
    if strategy_kind not in {"trend_confirmation", "range_reversion", "high_volatility_defensive"}:
        return {"status": "BLOCKED", "reason": "UNSUPPORTED_STRATEGY_KIND", "execution_enabled": False}
    if len(records) < 2:
        return {"status": "BLOCKED", "reason": "NEED_AT_LEAST_TWO_ACTIVE_FACTORS", "execution_enabled": False}
    if len(rows) < 120:
        return {"status": "BLOCKED", "reason": "INSUFFICIENT_HISTORY_FOR_HOLDOUT", "candles": len(rows), "execution_enabled": False}
    candles = as_backtest_candles(rows)
    split = int(len(candles) * 0.70)
    if split < 60 or len(candles) - split < 30:
        return {"status": "BLOCKED", "reason": "INSUFFICIENT_SPLIT_SAMPLES", "execution_enabled": False}
    holdout_start = candles[split].open_time
    # A factor definition must predate every candle used for OOS scoring.
    # Newly mined factors cannot validate themselves on the same historical
    # window that generated them; wait for genuinely later observations.
    eligible_records = [
        record for record in records
        if getattr(record, "created_at", None) is not None
        and record.created_at <= holdout_start
    ]
    if len(eligible_records) < 2:
        return {
            "status": "BLOCKED", "reason": "FACTORS_NOT_PREEXISTING_AT_HOLDOUT_START",
            "holdout_start": holdout_start.isoformat(),
            "eligible_factor_count": len(eligible_records),
            "candidate_factor_count": len(records), "execution_enabled": False,
        }
    signal_map = _build_consensus(eligible_records, candles)
    if not signal_map:
        return {"status": "BLOCKED", "reason": "NO_CAUSAL_FACTOR_SIGNAL_SERIES", "execution_enabled": False}
    risk_profile = {
        "trend_confirmation": (0.18, Decimal("0.50"), Decimal("3"), Decimal("6")),
        "range_reversion": (0.22, Decimal("0.35"), Decimal("1.5"), Decimal("2.5")),
        "high_volatility_defensive": (0.65, Decimal("0.20"), Decimal("2"), Decimal("4")),
    }
    threshold, notional_fraction, stop_pct, target_pct = risk_profile[strategy_kind]
    base_config = dict(
        initial_equity=Decimal("10000"), fee_bps=fee_bps, slippage_bps=slippage_bps,
        allow_short=True, max_volume_participation=max_volume_participation,
        max_notional_fraction=notional_fraction, execution_delay_bars=1,
        stop_loss_pct=stop_pct, take_profit_pct=target_pct,
    )
    strategy = _FactorConsensusStrategy(signal_map, kind=strategy_kind, threshold=threshold,
                                        min_candles=2 if strategy_kind == "trend_confirmation" else 1)
    in_funding = {index: rate for index, rate in (funding_rate_by_index or {}).items() if index < split}
    ins = run_backtest(candles[:split], strategy, BacktestConfig(**base_config, funding_rate_by_index=in_funding))
    if ins.error:
        return {"status": "BLOCKED", "reason": ins.error, "execution_enabled": False}

    # Three sequential, non-overlapping 10% holdout folds. The factor definitions
    # must already exist at the first fold boundary; all later folds are strictly
    # later observations. No model/LLM calls occur inside a fold.
    fold_results = []
    test_start = split
    fold_width = max(1, (len(candles) - split) // 3)
    for fold_number in range(3):
        fold_start = test_start + fold_number * fold_width
        fold_end = len(candles) if fold_number == 2 else min(len(candles), fold_start + fold_width)
        if fold_start >= fold_end:
            continue
        fold_funding = {
            index - fold_start: rate
            for index, rate in (funding_rate_by_index or {}).items()
            if fold_start <= index < fold_end
        }
        fold = run_backtest(
            candles[fold_start:fold_end], strategy,
            BacktestConfig(**base_config, funding_rate_by_index=fold_funding),
        )
        if fold.error:
            return {"status": "BLOCKED", "reason": fold.error, "execution_enabled": False}
        fold_results.append((fold_number + 1, fold_start, fold_end, fold))
    if len(fold_results) != 3:
        return {"status": "BLOCKED", "reason": "INCOMPLETE_WALK_FORWARD_FOLDS", "execution_enabled": False}

    all_trades = [trade for _, _, _, result in fold_results for trade in result.trades]
    fold_returns = [result.metrics.get("total_return") for _, _, _, result in fold_results]
    compounded_return = Decimal("1")
    for fold_return in fold_returns:
        compounded_return *= Decimal("1") + Decimal(str(fold_return or 0))
    dd_values = [result.metrics.get("max_drawdown_pct") for _, _, _, result in fold_results]
    dd_values = [Decimal(str(value)) for value in dd_values if value is not None]
    sharpe_values = [float(result.metrics["sharpe"]) for _, _, _, result in fold_results if result.metrics.get("sharpe") is not None]
    sortino_values = [float(result.metrics["sortino"]) for _, _, _, result in fold_results if result.metrics.get("sortino") is not None]
    gross_profit = sum((trade.pnl for trade in all_trades if trade.pnl > 0), Decimal("0"))
    gross_loss = -sum((trade.pnl for trade in all_trades if trade.pnl < 0), Decimal("0"))
    max_consecutive_losses = 0
    current_losses = 0
    for trade in all_trades:
        if trade.pnl < 0:
            current_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_losses)
        else:
            current_losses = 0
    oos_periods = sum(fold_end - fold_start for _, fold_start, fold_end, _ in fold_results)
    periods_per_year = int(fold_results[0][3].metrics.get("periods_per_year") or 365)
    total_oos_return = compounded_return - Decimal("1")
    annualized_return = (float(Decimal("1") + total_oos_return) ** (periods_per_year / max(oos_periods, 1))) - 1.0 if total_oos_return > Decimal("-1") else -1.0
    oos_metrics = {
        "total_return": total_oos_return,
        "annualized_return": annualized_return,
        "max_drawdown_pct": max(dd_values) if dd_values else None,
        "sharpe": sum(sharpe_values) / len(sharpe_values) if sharpe_values else None,
        "sortino": sum(sortino_values) / len(sortino_values) if sortino_values else None,
        "win_rate": (sum(1 for trade in all_trades if trade.pnl > 0) / len(all_trades)) if all_trades else None,
        "profit_factor": (float(gross_profit / gross_loss) if gross_loss > 0 else None),
        "trade_count": len(all_trades),
        "expectancy": (sum((trade.pnl for trade in all_trades), Decimal("0")) / len(all_trades)) if all_trades else None,
        "trade_frequency_per_100_bars": (len(all_trades) * 100.0 / max(oos_periods, 1)),
        "max_consecutive_losses": max_consecutive_losses,
        "calmar": (annualized_return / float(max(dd_values) / Decimal("100"))) if dd_values and max(dd_values) > 0 else None,
    }
    score = evaluate_strategy(ins.metrics, oos_metrics)
    if not funding_history_complete:
        score["passed"] = False
        score["gate"] = "RESEARCH_ONLY"
        score["failures"] = [*score["failures"], "FUNDING_HISTORY_INCOMPLETE"]
    walk_forward = [
        {"fold": number, "start": candles[fold_start].open_time.isoformat(),
         "end": candles[fold_end - 1].close_time.isoformat(),
         "metrics": _json_safe(result.metrics), "trade_count": len(result.trades)}
        for number, fold_start, fold_end, result in fold_results
    ]
    return {
        "status": "COMPLETED", "evaluation": score,
        "in_sample": _json_safe({**ins.metrics, "equity_points": len(ins.equity_curve)}),
        "out_of_sample": _json_safe(oos_metrics),
        "walk_forward": walk_forward,
        "assumptions": {
            "strategy_kind": strategy_kind,
            "signal_threshold": threshold,
            "fee_bps": str(fee_bps), "slippage_bps": str(slippage_bps),
            "max_volume_participation": str(max_volume_participation),
            "max_notional_fraction": str(notional_fraction),
            "stop_loss_pct": str(stop_pct),
            "take_profit_pct": str(target_pct),
            "execution_delay_bars": 1,
            "funding_settlement_count": len(funding_rate_by_index or {}),
            "funding_history_complete": funding_history_complete,
            "signal_source": "pre-existing active factor definitions, equal-weight causal formula series",
            "holdout_split": "chronological 70/30 with three sequential OOS folds",
            "holdout_start": holdout_start.isoformat(),
            "eligible_factor_count": len(eligible_records),
        },
        "execution_enabled": False,
        "human_approval_required": True,
    }
