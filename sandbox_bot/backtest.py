"""Offline backtest of the +EV rule over the simulated matches.

No browser is involved: the simulator's timelines are replayed minute by minute
so you can see whether the staking rule would have made money before you ever
point the scraper at anything.
"""

from __future__ import annotations

from dataclasses import dataclass

from mocksite import simulator
from mocksite.data import FIXTURES

from .config import SETTINGS
from .models import H2HResult, LiveStats, MarketQuote
from .odds import evaluate_quote, expected_total_goals, is_value, kelly_fraction


@dataclass
class BacktestResult:
    bets: int
    staked: float
    profit: float
    bankroll: float
    hit_rate: float

    @property
    def roi(self) -> float:
        return self.profit / self.staked if self.staked else 0.0

    def describe(self) -> str:
        return (
            f"Stávok: {self.bets}\n"
            f"Vsadené: {self.staked:.2f} €\n"
            f"Zisk: {self.profit:+.2f} €\n"
            f"ROI: {self.roi:+.1%}\n"
            f"Úspešnosť: {self.hit_rate:.1%}\n"
            f"Bankroll: {self.bankroll:.2f} €"
        )


def _history_from_fixture(fixture) -> list[H2HResult]:
    return [
        H2HResult(m.played_on, m.home, m.away, m.home_goals, m.away_goals) for m in fixture.h2h
    ]


def run_backtest(
    *,
    bankroll: float = 100.0,
    threshold: float = SETTINGS.value_threshold,
    staking: str = "flat",
    flat_stake: float = SETTINGS.stake_eur,
    max_bets_per_match: int = 1,
) -> BacktestResult:
    bets = staked = profit = wins = 0.0
    bets = 0

    for fixture in FIXTURES:
        lam = expected_total_goals(_history_from_fixture(fixture))
        final = simulator.final_state(fixture.match_id)
        placed = 0

        for minute in range(0, simulator.MATCH_MINUTES):
            if placed >= max_bets_per_match:
                break
            state = simulator.state_of(fixture.match_id, minute)
            if state.settled:
                break
            stats = LiveStats(
                match_id=fixture.match_id,
                home=fixture.home,
                away=fixture.away,
                minute=state.minute,
                home_goals=state.home_goals,
                away_goals=state.away_goals,
                home_shots=state.home_shots,
                away_shots=state.away_shots,
                home_corners=state.home_corners,
                away_corners=state.away_corners,
            )
            for market, odds in (
                ("over_2.5", state.over25_odds),
                ("under_2.5", state.under25_odds),
            ):
                quote = MarketQuote(
                    match_id=fixture.match_id,
                    event_name=f"{fixture.home} – {fixture.away}",
                    market=market,
                    odds=odds,
                    minute=minute,
                )
                signal = evaluate_quote(quote, lam, stats)
                if not is_value(signal, threshold):
                    continue

                if staking == "kelly":
                    stake = round(
                        bankroll * kelly_fraction(signal.fair_probability, quote.odds), 2
                    )
                else:
                    stake = flat_stake
                if stake <= 0 or stake > bankroll:
                    continue

                went_over = final.total_goals > SETTINGS.goal_line
                won = went_over if market.startswith("over") else not went_over
                payout = stake * quote.odds if won else 0.0
                bankroll += payout - stake
                staked += stake
                profit += payout - stake
                wins += 1 if won else 0
                bets += 1
                placed += 1
                break

    return BacktestResult(
        bets=bets,
        staked=staked,
        profit=profit,
        bankroll=bankroll,
        hit_rate=(wins / bets) if bets else 0.0,
    )
