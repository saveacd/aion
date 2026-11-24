from aion.services.paper_observer import paper_signal_for, _profile, is_new_observation_candle
import datetime as dt
from decimal import Decimal as D


def test_trend_paper_signal_requires_two_consecutive_confirmations():
    assert paper_signal_for("trend_confirmation", [0.5], 0.18) == "FLAT"
    assert paper_signal_for("trend_confirmation", [0.5, -0.6], 0.18) == "FLAT"
    assert paper_signal_for("trend_confirmation", [0.5, 0.6], 0.18) == "LONG"
    assert paper_signal_for("trend_confirmation", [-0.5, -0.6], 0.18) == "SHORT"


def test_range_reversion_is_contrarian_and_thresholded():
    assert paper_signal_for("range_reversion", [0.1], 0.22) == "FLAT"
    assert paper_signal_for("range_reversion", [0.4], 0.22) == "SHORT"
    assert paper_signal_for("range_reversion", [-0.4], 0.22) == "LONG"


def test_defensive_paper_profile_reduces_notional_and_unknown_kind_is_flat():
    trend = _profile("trend_confirmation")
    defensive = _profile("high_volatility_defensive")
    assert defensive[0] < trend[0]
    assert paper_signal_for("unknown", [0.99], 0.1) == "FLAT"
    assert defensive == (D("0.20"), D("2"), D("4"))


def test_paper_session_never_backfills_candles_before_creation():
    started = dt.datetime(2026, 11, 24, 7, 30, tzinfo=dt.UTC)
    before = dt.datetime(2026, 11, 24, 7, 0, tzinfo=dt.UTC)
    at_start = started
    after = dt.datetime(2026, 11, 24, 8, 0, tzinfo=dt.UTC)
    assert not is_new_observation_candle(before, None, started)
    assert is_new_observation_candle(at_start, None, started)
    assert is_new_observation_candle(after, None, started)


def test_paper_session_resume_uses_strictly_newer_candles():
    started = dt.datetime(2026, 11, 24, 7, 0, tzinfo=dt.UTC)
    last = dt.datetime(2026, 11, 24, 8, 0, tzinfo=dt.UTC)
    assert not is_new_observation_candle(last, last, started)
    assert is_new_observation_candle(last + dt.timedelta(hours=1), last, started)
