"""Plain data structures shared by the scraper, the model and the monitor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class H2HResult:
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


@dataclass(frozen=True)
class LiveStats:
    match_id: str
    home: str
    away: str
    minute: int
    home_goals: int
    away_goals: int
    home_shots: int
    away_shots: int
    home_corners: int
    away_corners: int

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals

    @property
    def total_shots(self) -> int:
        return self.home_shots + self.away_shots


@dataclass(frozen=True)
class MarketQuote:
    match_id: str
    event_name: str
    market: str
    odds: float
    minute: int


@dataclass(frozen=True)
class ValueSignal:
    quote: MarketQuote
    fair_odds: float
    fair_probability: float
    edge: float
    """(offered_odds / fair_odds) - 1, i.e. how far above fair value we are."""

    @property
    def expected_value_per_eur(self) -> float:
        return self.fair_probability * self.quote.odds - 1.0
