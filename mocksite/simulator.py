"""Match simulator that drives both mock sites.

The whole 90 minutes of every match is generated up-front from a fixed seed, so
the state at minute *m* is a pure lookup. That makes the sandbox reproducible
and easy to unit-test, while still looking "live" in the browser.

Two clock modes decide *when* a match is live:

``real`` (default)
    The match follows its real kickoff time in real time. With a real schedule
    loaded this means only the matches actually being played right now are live,
    and the rest are ``scheduled`` or ``finished``.

``demo``
    Every match kicks off when the server starts and runs at
    ``MOCK_MINUTES_PER_SECOND`` match-minutes per second, so you always have
    something live to watch.

Only the timing and the schedule can be real. The minute-by-minute statistics
and every price shown are simulated locally.
"""

from __future__ import annotations

import math
import os
import random
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone

from .fixtures_source import FD_DEAD_STATUSES, Fixture

#: Provider statuses that override the clock: the match is not going to run.
DEAD_PROVIDER_STATUSES = FD_DEAD_STATUSES

MATCH_MINUTES = 90
#: Wall-clock length of a match in ``real`` mode, including the half-time break.
REAL_MATCH_DURATION_MINUTES = 105
#: Bookmaker overround (margin) baked into the published live odds.
BOOKMAKER_MARGIN = 0.06

SCHEDULED, LIVE, FINISHED = "scheduled", "live", "finished"

_SERVER_START = time.time()


def clock_mode() -> str:
    """``real`` (follow actual kickoff times) or ``demo`` (start everything now)."""
    return os.environ.get("MOCK_CLOCK", "real")


def minutes_per_second() -> float:
    """Speed of the ``demo`` clock in match minutes per real second."""
    return float(os.environ.get("MOCK_MINUTES_PER_SECOND", "3"))


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
    status: str = LIVE

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def is_live(self) -> bool:
        return self.status == LIVE

    @property
    def tradable(self) -> bool:
        """Can the bot still take a price on the 2.5 line?"""
        return self.status == LIVE and not self.settled


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
    lam_home = (fixture.home_attack + fixture.away_defence) / 2
    lam_away = (fixture.away_attack + fixture.home_defence) / 2
    goals_per_90 = max(lam_home + lam_away, 0.2)

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


TIMELINES: dict[str, list[MinuteState]] = {}


def rebuild_timelines() -> None:
    """Regenerate every timeline from the fixture list currently loaded."""
    from .data import FIXTURES

    TIMELINES.clear()
    TIMELINES.update({fixture.match_id: _build_timeline(fixture) for fixture in FIXTURES})


def _elapsed_minutes(fixture: Fixture, now: float | None = None) -> tuple[int, str]:
    """Current match minute and status for one fixture."""
    now = now or time.time()
    if fixture.provider_status in DEAD_PROVIDER_STATUSES:
        # The data provider already reported the match as over/called off.
        return MATCH_MINUTES, FINISHED
    if clock_mode() == "demo":
        minute = (now - _SERVER_START) * minutes_per_second()
        if minute >= MATCH_MINUTES:
            return MATCH_MINUTES, FINISHED
        return int(minute), LIVE

    kickoff = fixture.kickoff_utc
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=timezone.utc)
    delta_minutes = (
        datetime.fromtimestamp(now, tz=timezone.utc) - kickoff
    ).total_seconds() / 60.0
    if delta_minutes < 0:
        return 0, SCHEDULED
    if delta_minutes >= REAL_MATCH_DURATION_MINUTES:
        return MATCH_MINUTES, FINISHED
    # Stretch 90 played minutes over the real duration (approximates the break).
    minute = delta_minutes * MATCH_MINUTES / REAL_MATCH_DURATION_MINUTES
    return int(min(minute, MATCH_MINUTES)), LIVE


def state_of(match_id: str, minute: int | None = None) -> MinuteState:
    """State of a match now, or at an explicit minute (for tests/backtests)."""
    from .data import FIXTURES_BY_ID

    timeline = TIMELINES[match_id]
    if minute is not None:
        return timeline[min(max(minute, 0), MATCH_MINUTES)]

    current, status = _elapsed_minutes(FIXTURES_BY_ID[match_id])
    base = timeline[current]
    if status == SCHEDULED:
        # Nothing has happened yet: show a clean 0-0 with no prices settled.
        opening = timeline[0]
        return MinuteState(
            minute=0,
            home_goals=0,
            away_goals=0,
            home_shots=0,
            away_shots=0,
            home_corners=0,
            away_corners=0,
            over25_odds=opening.over25_odds,
            under25_odds=opening.under25_odds,
            settled=False,
            status=SCHEDULED,
        )
    return replace(base, status=status)


def final_state(match_id: str) -> MinuteState:
    return TIMELINES[match_id][MATCH_MINUTES]


def live_match_ids() -> list[str]:
    return [match_id for match_id in TIMELINES if state_of(match_id).status == LIVE]


def reset_clock() -> None:
    """Restart the virtual match clock used by ``demo`` mode."""
    global _SERVER_START
    _SERVER_START = time.time()
