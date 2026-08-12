"""Deterministic fake data for the mock sites.

Everything here is generated locally from a fixed seed. No external service is
contacted, so the sandbox behaves identically on every run.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, timedelta

SEED = 20240817

TEAMS = [
    ("Slovan Bratislava", 1.85, 1.05),
    ("Spartak Trnava", 1.40, 1.20),
    ("MSK Zilina", 1.65, 1.30),
    ("DAC Dunajska Streda", 1.55, 1.15),
    ("FC Kosice", 1.10, 1.55),
    ("Podbrezova", 0.95, 1.70),
    ("Ruzomberok", 1.25, 1.45),
    ("Trencin", 1.35, 1.60),
]
"""(name, attack strength = avg goals scored, defence = avg goals conceded)."""


@dataclass(frozen=True)
class H2HMatch:
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


@dataclass
class Fixture:
    match_id: str
    home: str
    away: str
    kickoff: str
    h2h: list[H2HMatch] = field(default_factory=list)


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (keeps the sandbox dependency-free)."""
    import math

    limit = math.exp(-lam)
    k, product = 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


def build_fixtures() -> list[Fixture]:
    rng = random.Random(SEED)
    fixtures: list[Fixture] = []
    pairs = [(0, 4), (1, 5), (2, 6), (3, 7)]
    today = date.today()

    for index, (home_idx, away_idx) in enumerate(pairs):
        home, home_att, home_def = TEAMS[home_idx]
        away, away_att, away_def = TEAMS[away_idx]
        fixture = Fixture(
            match_id=f"m{index + 1}",
            home=home,
            away=away,
            kickoff=f"{17 + index}:00",
        )
        for back in range(1, 9):
            lam_home = (home_att + away_def) / 2
            lam_away = (away_att + home_def) / 2
            fixture.h2h.append(
                H2HMatch(
                    played_on=today - timedelta(days=back * 97),
                    home=home if back % 2 else away,
                    away=away if back % 2 else home,
                    home_goals=_poisson(rng, lam_home),
                    away_goals=_poisson(rng, lam_away),
                )
            )
        fixtures.append(fixture)
    return fixtures


FIXTURES: list[Fixture] = build_fixtures()
FIXTURES_BY_ID: dict[str, Fixture] = {f.match_id: f for f in FIXTURES}


def team_strength(name: str) -> tuple[float, float]:
    for team, attack, defence in TEAMS:
        if team == name:
            return attack, defence
    raise KeyError(name)
