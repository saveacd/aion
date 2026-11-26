"""Bounded, causal factor-expression search and independent holdout scoring."""

from __future__ import annotations

import itertools
import json
import math
from dataclasses import dataclass
from statistics import StatisticsError, correlation, mean, stdev

from aion.quant.factors import FactorFormula, _rank, _spearman, compute_series

VERSION = 2
LOOKBACKS = (8, 16, 32)
LEAVES = ("MOMENTUM", "VOLUME_Z", "MEAN_REVERSION_Z", "RANGE_POSITION", "VOL_ADJUSTED_MOMENTUM")
ARITY = {"tanh": 1, "add": 2, "mul": 2, "gate": 2}
HOLDOUT_COUNT = 3
ROUND_TRIP_COST = 0.0012


@dataclass(frozen=True, slots=True)
class Expression:
    operation: str
    arguments: tuple[Expression, ...] = ()
    lookback: int = 0
    coefficient: float | None = None

    def encode(self) -> list:
        if self.lookback:
            return [self.operation, self.lookback]
        if self.coefficient is not None:
            return ["scale", self.coefficient, self.arguments[0].encode()]
        return [self.operation, *(child.encode() for child in self.arguments)]


def parse_expression(raw: object, *, depth: int = 0) -> Expression:
    if depth > 5 or not isinstance(raw, list) or not raw or not isinstance(raw[0], str):
        raise ValueError("invalid factor expression")
    op, *args = raw
    if op in LEAVES and len(args) == 1 and type(args[0]) is int and args[0] in LOOKBACKS:
        return Expression(op, lookback=args[0])
    if op == "scale":
        if len(args) != 2 or type(args[0]) not in (int, float) or abs(args[0]) > 4 or not math.isfinite(args[0]):
            raise ValueError("scale requires a finite coefficient within [-4, 4]")
        return Expression(op, (parse_expression(args[1], depth=depth + 1),), coefficient=round(float(args[0]), 6))
    if ARITY.get(op) != len(args):
        raise ValueError("unknown operator or invalid operands")
    children = tuple(parse_expression(child, depth=depth + 1) for child in args)
    if op in ("add", "mul"):
        children = tuple(sorted(children, key=lambda child: json.dumps(child.encode())))
    return Expression(op, children)


def evaluate_expression(expression: Expression, bars: list[dict], cache: dict | None = None) -> list:
    cache = {} if cache is None else cache
    if expression in cache:
        return cache[expression]
    if expression.lookback:
        series = compute_series(FactorFormula(expression.operation), bars, expression.lookback)
    else:
        operands = [evaluate_expression(child, bars, cache) for child in expression.arguments]
        series = []
        for values in zip(*operands, strict=True):
            if any(value is None or not math.isfinite(value) for value in values):
                series.append(None)
                continue
            x = values[0]
            match expression.operation:
                case "scale":
                    value = expression.coefficient * x
                case "tanh":
                    value = math.tanh(x)
                case "add":
                    value = x + values[1]
                case "mul":
                    value = x * values[1]
                case "gate":
                    value = x if values[1] > 0 else 0.0
                case _:
                    raise ValueError("unknown operator")
            series.append(value if math.isfinite(value) else None)
    cache[expression] = series
    return series


def _pairs(series, opens, closes, indices, horizon):
    return [(series[i], closes[i + horizon] / opens[i + 1] - 1.0) for i in indices
            if series[i] is not None and math.isfinite(series[i])]


def _rank_ic(pairs):
    if len(pairs) < 5:
        return None
    return _spearman(*zip(*pairs, strict=True))


def _pearson(xs, ys):
    try:
        return correlation(xs, ys)
    except StatisticsError:
        return None


def _holdout(pairs, direction):
    xs, ys = zip(*pairs, strict=True)
    rank_ic = _rank_ic(pairs)
    # Circular shifts retain the series' marginals and temporal ordering.
    # This is an approximate screening statistic, not evidence of causation.
    rx, ry = _rank(xs), _rank(ys)
    null = [_pearson(rx, ry[shift:] + ry[:shift]) for shift in range(1, len(ry))]
    p = (1 + sum(score is not None and abs(score) >= abs(rank_ic or 0) for score in null)) / len(ry)
    thirds = [pairs[i * len(pairs) // 3:(i + 1) * len(pairs) // 3] for i in range(3)]
    segments = [_rank_ic(part) for part in thirds]
    estimates = [metric for metric in segments if metric is not None]
    dispersion = stdev(estimates) if len(estimates) == 3 else 0
    ranked = sorted(((direction * x, y) for x, y in pairs), key=lambda point: point[0])
    quartile = len(ranked) // 4
    quantiles = [mean(y for _, y in ranked[i * len(ranked) // 4:(i + 1) * len(ranked) // 4])
                 for i in range(4)]
    # Each selected non-overlapping holding period is charged a full round trip.
    net = mean(direction * (1 if x > 0 else -1) * y - ROUND_TRIP_COST for x, y in pairs if x) if any(xs) else None
    return {"samples": len(pairs), "ic": _pearson(xs, ys), "rank_ic": rank_ic, "p_value": p,
            "segment_rank_ic": segments, "icir": mean(estimates) / dispersion if dispersion > 1e-9 else None,
            "quartile_returns": quantiles, "net_return_per_interval": net,
            "gross_quartile_spread": quantiles[-1] - quantiles[0], "long_short_bucket_size": quartile}


def discover(dataset: dict, horizon: int) -> dict:
    from aion.quant.factor_validation import aligned_observations, window_metrics

    bars = dataset["bars"]
    split = len(bars) * 7 // 10
    report = {"algorithm_version": VERSION, "dataset_id": dataset["dataset_id"],
              "horizon_bars": horizon, "holdout_start_index": split,
              "round_trip_cost": ROUND_TRIP_COST, "candidates": []}
    if not dataset["quality"]["eligible"] or not 1 <= horizon <= 120:
        return {**report, "status": "BLOCKED_DATA_QUALITY"}
    train = range(0, split - horizon, horizon)
    test = range(split, len(bars) - horizon, horizon)
    if len(train) < 25 or len(test) < 15:
        return {**report, "status": "INSUFFICIENT_NON_OVERLAPPING_LABELS"}

    closes = [float(bar["c"]) for bar in bars]
    opens = [float(bar["o"]) for bar in bars]
    seeds = [parse_expression([name, window]) for name, window in itertools.product(LEAVES, LOOKBACKS)]
    trials = set(seeds)
    for a, b in itertools.combinations(seeds, 2):
        if a.operation != b.operation and a.lookback == b.lookback:
            for op in ("add", "mul", "gate"):
                trials.add(parse_expression([op, ["tanh", a.encode()], ["tanh", b.encode()]]))

    cache: dict = {}
    train_scores = []

    def score(expressions):
        for expression in sorted(expressions, key=lambda item: json.dumps(item.encode())):
            series = evaluate_expression(expression, bars, cache)
            pairs = _pairs(series, opens, closes, train, horizon)
            ic = _rank_ic(pairs) if len(pairs) >= 20 else None
            row = {"expression": expression.encode(), "train_rank_ic": ic, "train_samples": len(pairs)}
            report["candidates"].append(row)
            if ic is None:
                row["status"] = "INSUFFICIENT_SIGNAL"
                continue
            train_scores.append((expression, row, ic))

    score(trials)
    # One bounded evolutionary generation. Parent ranking, crossover and
    # mutation finish before the holdout is accessed.
    parents = sorted(train_scores, key=lambda item: -abs(item[2]))[:6]
    offspring = set()
    for expression, _, _ in parents:
        offspring.add(parse_expression(["tanh", expression.encode()]))
        for leaf in seeds[:5]:
            offspring.add(parse_expression(["mul", expression.encode(), leaf.encode()]))
    score(offspring - trials)
    report["search_generations"] = 2

    # Fit coefficients of two nonlinear expressions on training labels.
    # Freeze coefficients in the AST before evaluating any holdout observation.
    fitted = set()
    for (left, _, _), (right, _, _) in itertools.combinations(parents[:4], 2):
        points = [(math.tanh(cache[left][i]), math.tanh(cache[right][i]), closes[i + horizon] / opens[i + 1] - 1)
                  for i in train if cache[left][i] is not None and cache[right][i] is not None]
        if len(points) < 20:
            continue
        xx = sum(x*x for x, _, _ in points) + 1e-6
        yy = sum(y*y for _, y, _ in points) + 1e-6
        xy = sum(x*y for x, y, _ in points)
        xz = sum(x*z for x, _, z in points)
        yz = sum(y*z for _, y, z in points)
        determinant = xx*yy - xy*xy
        weights = ((yy*xz - xy*yz)/determinant, (xx*yz - xy*xz)/determinant)
        scale = max(map(abs, weights))
        if scale > 1e-10:
            fitted.add(parse_expression(["add", *(
                ["scale", weight / scale, ["tanh", node.encode()]]
                for weight, node in zip(weights, (left, right), strict=True)
            )]))
    score(fitted - trials - offspring)
    report["symbolic_regression_candidates"] = len(fitted)

    selected = []
    for expression, row, ic in sorted(train_scores, key=lambda item: -abs(item[2])):
        if len(selected) >= HOLDOUT_COUNT:
            row["status"] = "NOT_SELECTED"
            continue
        series = cache[expression]
        overlaps = [
            [[series[i], earlier[i]] for i in train if series[i] is not None and earlier[i] is not None]
            for earlier, _ in selected
        ]
        similarities = []
        for pairs, (_, peer) in zip(overlaps, selected, strict=True):
            rank_corr = _rank_ic(pairs)
            distance = (1 - abs(rank_corr)) if rank_corr is not None else None
            agreement = mean((x > 0) == (y > 0) for x, y in pairs) if pairs else None
            similarities.append({"peer_expression": peer["expression"], "rank_correlation": rank_corr,
                                 "rank_distance": distance, "sign_agreement": agreement})
        row["similarity"] = similarities
        if any(item["rank_distance"] is not None and item["rank_distance"] <= 0.10 for item in similarities):
            row["status"] = "REDUNDANT"
            continue
        row["direction"] = 1 if ic > 0 else -1
        selected.append((series, row))

    for series, row in selected:
        points = _pairs(series, opens, closes, test, horizon)
        if len(points) < 15:
            row["status"] = "INSUFFICIENT_HOLDOUT"
            continue
        observations = aligned_observations(bars, series, test, horizon, dataset.get("funding", ()))
        metrics = window_metrics(observations, row["direction"], len(selected))
        row["holdout"] = metrics
        row["status"] = "PRIMARY_PASS" if metrics["passed"] else "REJECTED"
    return {**report, "status": "COMPLETE"}
