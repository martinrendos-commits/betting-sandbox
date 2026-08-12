from datetime import date

import pytest

from sandbox_bot.analysis import build_pregame_model
from sandbox_bot.models import H2HResult, LiveStats, MarketQuote
from sandbox_bot.odds import (
    bookmaker_margin,
    evaluate_quote,
    expected_total_goals,
    historical_over_rate,
    implied_probability,
    is_value,
    kelly_fraction,
    over_probability,
    poisson_pmf,
    probability_to_odds,
)


def history(totals):
    return [
        H2HResult(date(2023, 1, 1 + i), "A", "B", total, 0) for i, total in enumerate(totals)
    ]


def test_historical_over_rate():
    assert historical_over_rate(history([0, 1, 3, 4])) == 0.5
    assert historical_over_rate([]) == 0.0


def test_expected_total_goals_shrinks_towards_prior():
    lam = expected_total_goals(history([6, 6, 6]), prior=2.7, weight=0.65)
    assert 2.7 < lam < 6.0


def test_poisson_pmf_sums_to_one():
    assert sum(poisson_pmf(k, 2.5) for k in range(40)) == pytest.approx(1.0, abs=1e-9)


def test_over_probability_bounds():
    assert over_probability(2.7, minute=0, goals_so_far=0) == pytest.approx(0.494, abs=0.02)
    assert over_probability(2.7, minute=10, goals_so_far=3) == 1.0
    assert over_probability(2.7, minute=90, goals_so_far=1) == 0.0


def test_over_probability_decreases_with_time_when_no_goals():
    early = over_probability(2.7, minute=10, goals_so_far=0)
    late = over_probability(2.7, minute=70, goals_so_far=0)
    assert early > late


def test_odds_probability_roundtrip():
    assert implied_probability(probability_to_odds(0.4)) == pytest.approx(0.4)


def test_bookmaker_margin_is_positive_for_a_real_book():
    assert bookmaker_margin(1.90, 1.90) == pytest.approx(0.0526, abs=1e-3)


def test_evaluate_quote_detects_value():
    stats = LiveStats("m1", "A", "B", 20, 1, 0, 4, 3, 2, 1)
    quote = MarketQuote("m1", "A – B", "over_2.5", odds=4.0, minute=20)
    signal = evaluate_quote(quote, 2.7, stats)
    assert signal.edge > 0.10
    assert signal.expected_value_per_eur > 0
    assert is_value(signal)


def test_evaluate_quote_rejects_bad_price():
    stats = LiveStats("m1", "A", "B", 80, 0, 0, 2, 1, 1, 0)
    quote = MarketQuote("m1", "A – B", "over_2.5", odds=3.0, minute=80)
    signal = evaluate_quote(quote, 2.7, stats)
    assert not is_value(signal)


def test_kelly_fraction_is_capped_and_non_negative():
    assert kelly_fraction(0.9, 5.0) == 0.05
    assert kelly_fraction(0.1, 1.5) == 0.0


def test_pregame_model_description():
    model = build_pregame_model("m1", "A", "B", history([1, 2, 3, 4, 5]))
    assert model.sample_size == 5
    assert model.fair_over_odds > 1.0
    assert "Férový kurz over" in model.describe()
