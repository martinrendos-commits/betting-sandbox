"""Poisson ratings estimated from stored competition results."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from mocksite.store import connect

from .odds import poisson_pmf, probability_to_odds

MIN_FINISHED_MATCHES = 3


@dataclass(frozen=True)
class TeamRating:
    team_id: str
    name: str
    matches: int
    home_attack: float
    home_defence: float
    away_attack: float
    away_defence: float


@dataclass(frozen=True)
class LeagueRating:
    league_id: str
    league_name: str
    matches: int
    average_home_goals: float
    average_away_goals: float
    average_home_xg: float
    average_away_xg: float
    teams: list[TeamRating]
    has_small_sample: bool


@dataclass(frozen=True)
class PairOdds:
    lambda_home: float
    lambda_away: float
    home: float
    draw: float
    away: float
    over_25: float
    under_25: float
    used_fallback: bool


def _league_id(connection: sqlite3.Connection, value: str) -> str | None:
    if value.isdigit():
        row = connection.execute(
            "SELECT league_id FROM leagues WHERE league_id = ? LIMIT 1", (value,)
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT league_id FROM leagues WHERE lower(name) LIKE ? LIMIT 1",
            (f"%{value.lower()}%",),
        ).fetchone()
    return str(row[0]) if row else None


def load_league_rating(league: str, path: str | Path | None = None) -> LeagueRating | None:
    with connect(path) as connection:
        league_id = _league_id(connection, league)
        if league_id is None:
            return None
        league_row = connection.execute(
            "SELECT name FROM leagues WHERE league_id = ?", (league_id,)
        ).fetchone()
        rows = connection.execute(
            "SELECT home_team_id, away_team_id, home_goals, away_goals FROM matches "
            "WHERE league_id = ? AND lower(status) IN ('complete', 'finished', 'final') "
            "AND home_goals IS NOT NULL AND away_goals IS NOT NULL",
            (league_id,),
        ).fetchall()
        averages = connection.execute(
            "SELECT AVG(home_goals), AVG(away_goals), AVG(home_xg), AVG(away_xg) FROM matches WHERE league_id = ? "
            "AND lower(status) IN ('complete', 'finished', 'final') "
            "AND home_goals IS NOT NULL AND away_goals IS NOT NULL",
            (league_id,),
        ).fetchone()
        home_average = float(averages[0] or 1.45)
        away_average = float(averages[1] or 1.15)
        home_xg = float(averages[2] or home_average)
        away_xg = float(averages[3] or away_average)
        team_ids = sorted({str(row[0]) for row in rows} | {str(row[1]) for row in rows})
        teams: list[TeamRating] = []
        for team_id in team_ids:
            home_rows = [row for row in rows if str(row[0]) == team_id]
            away_rows = [row for row in rows if str(row[1]) == team_id]
            count = len(home_rows) + len(away_rows)
            home_scored = sum(int(row[2]) for row in home_rows)
            home_allowed = sum(int(row[3]) for row in home_rows)
            away_scored = sum(int(row[3]) for row in away_rows)
            away_allowed = sum(int(row[2]) for row in away_rows)
            teams.append(
                TeamRating(
                    team_id=team_id,
                    name=str(connection.execute("SELECT name FROM teams WHERE team_id = ?", (team_id,)).fetchone()[0]),
                    matches=count,
                    home_attack=(home_scored / len(home_rows) / home_average) if home_rows else 1.0,
                    home_defence=(home_allowed / len(home_rows) / away_average) if home_rows else 1.0,
                    away_attack=(away_scored / len(away_rows) / away_average) if away_rows else 1.0,
                    away_defence=(away_allowed / len(away_rows) / home_average) if away_rows else 1.0,
                )
            )
        has_small_sample = len(rows) < MIN_FINISHED_MATCHES or any(
            team.matches < MIN_FINISHED_MATCHES for team in teams
        )
        return LeagueRating(
            league_id=league_id,
            league_name=str(league_row[0]),
            matches=len(rows),
            average_home_goals=home_average,
            average_away_goals=away_average,
            average_home_xg=home_xg,
            average_away_xg=away_xg,
            teams=teams,
            has_small_sample=has_small_sample,
        )


def pair_odds(
    rating: LeagueRating,
    home_team: str,
    away_team: str,
) -> PairOdds | None:
    home = next((team for team in rating.teams if team.team_id == home_team or team.name.lower() == home_team.lower()), None)
    away = next((team for team in rating.teams if team.team_id == away_team or team.name.lower() == away_team.lower()), None)
    if home is None or away is None:
        return None
    used_fallback = (
        rating.matches < MIN_FINISHED_MATCHES
        or home.matches < MIN_FINISHED_MATCHES
        or away.matches < MIN_FINISHED_MATCHES
    )
    if used_fallback:
        lam_home = rating.average_home_xg
        lam_away = rating.average_away_xg
    else:
        lam_home = rating.average_home_goals * home.home_attack * away.away_defence
        lam_away = rating.average_away_goals * away.away_attack * home.home_defence
    matrix = [
        (poisson_pmf(h, lam_home) * poisson_pmf(a, lam_away), h, a)
        for h in range(10)
        for a in range(10)
    ]
    home_probability = sum(probability for probability, h, a in matrix if h > a)
    draw_probability = sum(probability for probability, h, a in matrix if h == a)
    away_probability = sum(probability for probability, h, a in matrix if h < a)
    under_probability = sum(probability for probability, h, a in matrix if h + a <= 2)
    return PairOdds(
        lambda_home=lam_home,
        lambda_away=lam_away,
        home=probability_to_odds(home_probability),
        draw=probability_to_odds(draw_probability),
        away=probability_to_odds(away_probability),
        over_25=probability_to_odds(1.0 - under_probability),
        under_25=probability_to_odds(under_probability),
        used_fallback=used_fallback,
    )
