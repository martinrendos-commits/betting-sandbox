"""Fair-odds model and value detection.

The model is a simple Poisson goal model:

1. estimate the expected number of goals in the match (``lambda``) from the H2H
   sample, shrunk towards a league prior so a handful of matches cannot dominate;
2. for a live match, scale ``lambda`` down to the minutes that are still to be
   played and condition on the goals already scored;
3. turn the resulting probability into fair (margin-free) decimal odds;
4. compare against the offered odds to get the edge.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from .config import SETTINGS
from .models import H2HResult, LiveStats, MarketQuote, ValueSignal

MATCH_MINUTES = 90


def historical_over_rate(history: Sequence[H2HResult], line: float = SETTINGS.goal_line) -> float:
    """Share of past meetings that went over the line (the "naive" estimate)."""
    if not history:
        return 0.0
    return sum(1 for match in history if match.total_goals > line) / len(history)


def expected_total_goals(
    history: Sequence[H2HResult],
    *,
    prior: float = SETTINGS.league_avg_total_goals,
    weight: float = SETTINGS.h2h_weight,
) -> float:
    """Shrink the H2H goal average towards the league prior."""
    if not history:
        return prior
    sample_mean = sum(match.total_goals for match in history) / len(history)
    # More matches -> more trust in the sample, capped by ``weight``.
    trust = weight * len(history) / (len(history) + 4)
    return trust * sample_mean + (1 - trust) * prior


def poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam**k / math.factorial(k)


def over_probability(
    lam_full_match: float,
    *,
    line: float = SETTINGS.goal_line,
    minute: int = 0,
    goals_so_far: int = 0,
) -> float:
    """P(final total goals > line), conditioned on the current live state."""
    still_needed = int(math.floor(line)) + 1 - goals_so_far
    if still_needed <= 0:
        return 1.0
    minutes_left = max(MATCH_MINUTES - minute, 0)
    lam_rest = lam_full_match * minutes_left / MATCH_MINUTES
    if lam_rest <= 0:
        return 0.0
    return max(0.0, min(1.0, 1.0 - sum(poisson_pmf(k, lam_rest) for k in range(still_needed))))


def probability_to_odds(probability: float) -> float:
    if probability <= 0:
        return math.inf
    return 1.0 / probability


def implied_probability(odds: float) -> float:
    return 1.0 / odds if odds > 0 else 0.0


def bookmaker_margin(over_odds: float, under_odds: float) -> float:
    """Overround of a two-way market: 0.06 means the book pays out ~94 %."""
    return implied_probability(over_odds) + implied_probability(under_odds) - 1.0


def live_intensity(lam_pregame: float, stats: LiveStats | None) -> float:
    """Nudge the pre-game lambda with live evidence (shot volume)."""
    if stats is None or stats.minute <= 0:
        return lam_pregame
    shots_per_minute = stats.total_shots / stats.minute
    # ~0.25 shots/min is an average game; scale within +/-25 %.
    factor = 1.0 + max(-0.25, min(0.25, (shots_per_minute - 0.25) * 0.8))
    return lam_pregame * factor


def evaluate_quote(
    quote: MarketQuote,
    lam_full_match: float,
    stats: LiveStats | None = None,
    *,
    line: float = SETTINGS.goal_line,
) -> ValueSignal:
    """Compare an offered price with our fair price."""
    lam = live_intensity(lam_full_match, stats)
    goals = stats.total_goals if stats else 0
    minute = stats.minute if stats else quote.minute
    prob_over = over_probability(lam, line=line, minute=minute, goals_so_far=goals)
    probability = prob_over if quote.market.startswith("over") else 1.0 - prob_over
    fair = probability_to_odds(probability)
    edge = (quote.odds / fair - 1.0) if math.isfinite(fair) and fair > 0 else -1.0
    return ValueSignal(quote=quote, fair_odds=fair, fair_probability=probability, edge=edge)


def is_value(signal: ValueSignal, threshold: float = SETTINGS.value_threshold) -> bool:
    return signal.edge >= threshold and signal.expected_value_per_eur > 0


def kelly_fraction(probability: float, odds: float, cap: float = 0.05) -> float:
    """Fraction of the bankroll a full Kelly criterion would stake, capped."""
    b = odds - 1.0
    if b <= 0:
        return 0.0
    fraction = (probability * b - (1 - probability)) / b
    return max(0.0, min(fraction, cap))
