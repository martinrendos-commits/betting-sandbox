"""Fixture registry for the mock sites.

The actual loading lives in :mod:`mocksite.fixtures_source`; this module keeps
one process-wide list so the Flask app and the simulator always agree on which
matches exist.
"""

from __future__ import annotations

import math
import random
from datetime import date

from .fixtures_source import Fixture, H2HMatch, load_fixtures

SYNTHETIC_TEAMS = [
    ("Slovan Bratislava", 1.85, 1.05),
    ("Spartak Trnava", 1.40, 1.20),
    ("MSK Zilina", 1.65, 1.30),
    ("DAC Dunajska Streda", 1.55, 1.15),
    ("FC Kosice", 1.10, 1.55),
    ("Podbrezova", 0.95, 1.70),
    ("Ruzomberok", 1.25, 1.45),
    ("Trencin", 1.35, 1.60),
]
"""(name, attack = avg goals scored, defence = avg goals conceded)."""


def poisson_sample(rng: random.Random, lam: float) -> int:
    """Knuth's Poisson sampler (keeps the sandbox dependency-free)."""
    limit = math.exp(-lam)
    k, product = 0, rng.random()
    while product > limit:
        k += 1
        product *= rng.random()
    return k


FIXTURES: list[Fixture] = []
FIXTURES_BY_ID: dict[str, Fixture] = {}


def reload_fixtures(
    source: str | None = None, *, league: str | None = None, on_date: date | None = None
) -> list[Fixture]:
    """(Re)load the fixture list and rebuild every dependent structure.

    The containers are mutated in place so modules that did
    ``from .data import FIXTURES`` keep seeing the current data.
    """
    fixtures = load_fixtures(source, league=league, on_date=on_date)
    FIXTURES[:] = fixtures
    FIXTURES_BY_ID.clear()
    FIXTURES_BY_ID.update({fixture.match_id: fixture for fixture in fixtures})

    from . import simulator

    simulator.rebuild_timelines()
    return FIXTURES


reload_fixtures()


__all__ = [
    "FIXTURES",
    "FIXTURES_BY_ID",
    "Fixture",
    "H2HMatch",
    "SYNTHETIC_TEAMS",
    "poisson_sample",
    "reload_fixtures",
]
