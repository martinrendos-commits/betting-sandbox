import pytest

from sandbox_bot.backtest import run_backtest


def test_flat_backtest_runs():
    result = run_backtest(bankroll=100.0, staking="flat")
    assert result.bets >= 0
    assert result.bankroll == pytest.approx(100.0 + result.profit, abs=0.01)
    assert "ROI" in result.describe()


def test_kelly_backtest_never_goes_negative_bankroll():
    result = run_backtest(bankroll=100.0, staking="kelly")
    assert result.bankroll >= 0


def test_higher_threshold_places_fewer_bets():
    low = run_backtest(threshold=0.05)
    high = run_backtest(threshold=0.60)
    assert high.bets <= low.bets
