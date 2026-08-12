"""Match simulator that drives both mock sites.

The whole 90 minutes of every match is generated up-front from a fixed seed, so
the state at minute *m* is a pure lookup. That makes the sandbox reproducible
and easy to unit-test, while still looking "live" in the browser.
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass

from .data import FIXTURES, Fixture, team_strength

MATCH_MINUTES = 90
#: How many match minutes elapse per real second. Override to speed up demos.
MINUTES_PER_SECOND = float(os.environ.get("MOCK_MINUTES_PER_SECOND", "3"))
#: Bookmaker overround (margin) baked into the published live odds.
BOOKMAKER_MARGIN = 0.06

_START_TIME = time.time()


@dataclass(frozen=True)
class MinuteState:
    minute: int
    home_goals: int
    away_goals: int
    home_shots: int
    away_shots: int
    home_corners: int
    away_corners: int
    over25_odds: float
    under25_odds: float
    settled: bool
    """True once the 2.5 line can no longer change (3 goals scored, or full time)."""

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


def _fair_over25_probability(goals_so_far: int, minute: int, goals_per_90: float) -> float:
    """P(total goals > 2.5) given the current score and the minutes left."""
    still_needed = 3 - goals_so_far
    if still_needed <= 0:
        return 1.0
    minutes_left = max(MATCH_MINUTES - minute, 0)
    lam = goals_per_90 * minutes_left / MATCH_MINUTES
    if lam <= 0:
        return 0.0
    cumulative = sum(math.exp(-lam) * lam**k / math.factorial(k) for k in range(still_needed))
    return max(0.0, min(1.0, 1.0 - cumulative))


def _odds_from_probability(probability: float, margin: float) -> float:
    """Fair odds discounted by the margin, floored at 1.01 like a real book."""
    probability = min(max(probability, 0.01), 0.99)
    return max(1.01, round(1.0 / (probability * (1.0 + margin)), 2))


def _build_timeline(fixture: Fixture) -> list[MinuteState]:
    rng = random.Random(hash(fixture.match_id) & 0xFFFF)
    home_att, home_def = team_strength(fixture.home)
    away_att, away_def = team_strength(fixture.away)
    lam_home = (home_att + away_def) / 2
    lam_away = (away_att + home_def) / 2
    goals_per_90 = lam_home + lam_away

    # Some matches carry a deliberate mispricing so the +EV detector has
    # something to find; others are priced close to fair value.
    mispricing = rng.choice([0.0, 0.0, 0.18, 0.28])

    states: list[MinuteState] = []
    home_goals = away_goals = home_shots = away_shots = 0
    home_corners = away_corners = 0

    for minute in range(MATCH_MINUTES + 1):
        if minute > 0:
            if rng.random() < lam_home / MATCH_MINUTES:
                home_goals += 1
            if rng.random() < lam_away / MATCH_MINUTES:
                away_goals += 1
            if rng.random() < 0.14:
                home_shots += 1
            if rng.random() < 0.11:
                away_shots += 1
            if rng.random() < 0.06:
                home_corners += 1
            if rng.random() < 0.05:
                away_corners += 1

        fair = _fair_over25_probability(home_goals + away_goals, minute, goals_per_90)
        # The bookmaker lags the true probability and adds noise + margin.
        skewed = min(max(fair * (1.0 - mispricing) + rng.uniform(-0.02, 0.02), 0.01), 0.99)
        states.append(
            MinuteState(
                minute=minute,
                home_goals=home_goals,
                away_goals=away_goals,
                home_shots=home_shots,
                away_shots=away_shots,
                home_corners=home_corners,
                away_corners=away_corners,
                over25_odds=_odds_from_probability(skewed, BOOKMAKER_MARGIN),
                under25_odds=_odds_from_probability(1.0 - skewed, BOOKMAKER_MARGIN),
                settled=home_goals + away_goals >= 3 or minute >= MATCH_MINUTES,
            )
        )
    return states


TIMELINES: dict[str, list[MinuteState]] = {f.match_id: _build_timeline(f) for f in FIXTURES}


def current_minute(now: float | None = None) -> int:
    elapsed = (now or time.time()) - _START_TIME
    return int(min(elapsed * MINUTES_PER_SECOND, MATCH_MINUTES))


def state_of(match_id: str, minute: int | None = None) -> MinuteState:
    timeline = TIMELINES[match_id]
    return timeline[current_minute() if minute is None else min(max(minute, 0), MATCH_MINUTES)]


def final_state(match_id: str) -> MinuteState:
    return TIMELINES[match_id][MATCH_MINUTES]


def reset_clock() -> None:
    """Restart the virtual match clock (used by tests and the CLI)."""
    global _START_TIME
    _START_TIME = time.time()
