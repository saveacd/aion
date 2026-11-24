from aion.quant.strategy_score import StrategyGate, compare_strategies, evaluate_strategy


def metrics(n: int, **overrides):
    result = {"trade_count": n, "total_return": 0.20, "max_drawdown_pct": 10,
              "sharpe": 1.2, "sortino": 1.4, "win_rate": 0.55,
              "profit_factor": 1.4, "calmar": 1.5, "max_consecutive_losses": 2}
    result.update(overrides)
    return result


def test_small_sample_cannot_pass_even_with_perfect_metrics():
    result = evaluate_strategy(metrics(4), metrics(3))
    assert not result["passed"]
    assert result["gate"] == "RESEARCH_ONLY"
    assert result["sample_reliability"] < 1
    assert "INSUFFICIENT_OUT_OF_SAMPLE_TRADES" in result["failures"]
    assert result["execution_enabled"] is False


def test_sufficient_robust_oos_evidence_can_become_paper_eligible():
    result = evaluate_strategy(metrics(60), metrics(30))
    assert result["passed"]
    assert result["gate"] == "PAPER_ELIGIBLE"
    assert result["human_approval_required"] is True


def test_drawdown_and_oos_return_gates_reject_candidate():
    result = evaluate_strategy(metrics(60), metrics(30, max_drawdown_pct=40, total_return=-0.1))
    assert not result["passed"]
    assert "DRAWDOWN_GATE_FAILED" in result["failures"]
    assert "OUT_OF_SAMPLE_RETURN_GATE_FAILED" in result["failures"]


def test_comparison_orders_passed_and_high_score_first():
    rows = compare_strategies({"weak": {"score": 0.8, "passed": False},
                               "strong": {"score": 0.3, "passed": True}})
    assert [row["name"] for row in rows] == ["strong", "weak"]


def test_consecutive_loss_streak_blocks_paper_eligibility():
    result = evaluate_strategy(metrics(60), metrics(30, max_consecutive_losses=8))
    assert not result["passed"]
    assert "CONSECUTIVE_LOSS_GATE_FAILED" in result["failures"]


def test_evolution_proposes_parallel_candidates_not_three_labels_for_one_rule():
    from aion.quant.strategy_evolution import build_strategy_evolution

    result = build_strategy_evolution(
        {"composite_signal": 0.7, "factor_count": 3, "bias": "BULLISH"},
        {"label": "TRENDING"},
    )
    candidates = result["candidate_strategies"]
    assert {item["strategy_kind"] for item in candidates} == {
        "trend_confirmation", "range_reversion", "high_volatility_defensive"
    }
    assert sum(bool(item["regime_match"]) for item in candidates) == 1
    assert result["auto_orders"] is False
