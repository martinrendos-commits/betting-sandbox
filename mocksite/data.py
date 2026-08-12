"""Fixture registry for the mock sites.

The actual loading lives in :mod:`mocksite.fixtures_source`; this module keeps
one process-wide list so the Flask app and the simulator always agree on which
matches exist.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from datetime import date

from .fixtures_source import Fixture, H2HMatch, load_fixtures

log = logging.getLogger("mocksite.data")

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
_LAST_LOAD = 0.0
#: Providers whose schedule/status can change while the server runs.
REMOTE_SOURCES = frozenset({"openliga", "footballdata"})


def reload_fixtures(
    source: str | None = None, *, league: str | None = None, on_date: date | None = None
) -> list[Fixture]:
    """(Re)load the fixture list and rebuild every dependent structure.

    The containers are mutated in place so modules that did
    ``from .data import FIXTURES`` keep seeing the current data.
    """
    global _LAST_LOAD

    fixtures = load_fixtures(source, league=league, on_date=on_date)
    _LAST_LOAD = time.time()
    FIXTURES[:] = fixtures
    FIXTURES_BY_ID.clear()
    FIXTURES_BY_ID.update({fixture.match_id: fixture for fixture in fixtures})

    from . import simulator

    simulator.rebuild_timelines()
    return FIXTURES


def refresh_if_stale() -> None:
    """Re-poll a remote provider so kickoff times and statuses stay current.

    ``MOCK_REFRESH_S=0`` turns it off; the provider responses are cached on disk,
    so this does not mean one upstream request per page view.
    """
    interval = float(os.environ.get("MOCK_REFRESH_S", "300"))
    if interval <= 0 or os.environ.get("MOCK_FIXTURES", "synthetic") not in REMOTE_SOURCES:
        return
    if time.time() - _LAST_LOAD < interval:
        return
    try:
        reload_fixtures()
    except (OSError, RuntimeError, ValueError) as exc:
        log.warning("Obnovenie rozpisu zlyhalo, ostávam pri poslednom: %s", exc)


reload_fixtures()


__all__ = [
    "FIXTURES",
    "FIXTURES_BY_ID",
    "Fixture",
    "H2HMatch",
    "SYNTHETIC_TEAMS",
    "poisson_sample",
    "refresh_if_stale",
    "reload_fixtures",
]
