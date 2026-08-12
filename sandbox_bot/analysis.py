"""Pre-game analysis: turn scraped H2H history into a fair price."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from .config import SETTINGS
from .models import H2HResult
from .odds import (
    expected_total_goals,
    historical_over_rate,
    over_probability,
    probability_to_odds,
)


@dataclass(frozen=True)
class PregameModel:
    match_id: str
    home: str
    away: str
    sample_size: int
    historical_over_rate: float
    expected_goals: float
    fair_over_probability: float
    fair_over_odds: float
    fair_under_odds: float

    def describe(self) -> str:
        return (
            f"{self.home} – {self.away}\n"
            f"  H2H vzoriek:        {self.sample_size}\n"
            f"  Over {SETTINGS.goal_line} historicky: {self.historical_over_rate:.0%}\n"
            f"  Očak. góly (lambda): {self.expected_goals:.2f}\n"
            f"  Férová P(over):      {self.fair_over_probability:.1%}\n"
            f"  Férový kurz over:    {self.fair_over_odds:.2f}\n"
            f"  Férový kurz under:   {self.fair_under_odds:.2f}"
        )


def build_pregame_model(
    match_id: str,
    home: str,
    away: str,
    history: Sequence[H2HResult],
    *,
    line: float = SETTINGS.goal_line,
) -> PregameModel:
    lam = expected_total_goals(history)
    prob_over = over_probability(lam, line=line)
    return PregameModel(
        match_id=match_id,
        home=home,
        away=away,
        sample_size=len(history),
        historical_over_rate=historical_over_rate(history, line),
        expected_goals=lam,
        fair_over_probability=prob_over,
        fair_over_odds=probability_to_odds(prob_over),
        fair_under_odds=probability_to_odds(1 - prob_over),
    )
