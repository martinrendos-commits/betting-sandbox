"""Small SQLite persistence layer for downloaded match data."""

from __future__ import annotations

import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mocksite.store")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "sandbox.sqlite3"


def db_path() -> Path:
    return Path(os.environ.get("MOCK_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    selected = Path(path) if path is not None else db_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(selected)
    connection.execute("PRAGMA foreign_keys = ON")
    create_schema(connection)
    return connection


def create_schema(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS leagues (
            league_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT
        );
        CREATE TABLE IF NOT EXISTS teams (
            team_id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            league_id TEXT NOT NULL,
            season_year INTEGER,
            kickoff_utc TEXT,
            status TEXT,
            home_team_id TEXT NOT NULL,
            away_team_id TEXT NOT NULL,
            home_goals INTEGER,
            away_goals INTEGER,
            home_xg REAL,
            away_xg REAL,
            source TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            FOREIGN KEY (league_id) REFERENCES leagues(league_id),
            FOREIGN KEY (home_team_id) REFERENCES teams(team_id),
            FOREIGN KEY (away_team_id) REFERENCES teams(team_id)
        );
        CREATE INDEX IF NOT EXISTS matches_league_idx ON matches(league_id);
        """
    )


def _text(value: object | None) -> str | None:
    return None if value is None else str(value)


def _number(value: object | None) -> int | float | None:
    if value is None:
        return None
    return float(value) if isinstance(value, float) else int(value)


def store_match_payloads(
    payloads: list[dict],
    *,
    source: str,
    path: str | Path | None = None,
) -> int:
    """Upsert provider-native match dictionaries and return the row count."""
    fetched_at = datetime.now(timezone.utc).isoformat()
    with connect(path) as connection:
        for match in payloads:
            league = match.get("league") or {}
            home = match.get("home_team") or {}
            away = match.get("away_team") or {}
            score = match.get("score") or {}
            xg_payload = match.get("xg") or {}
            xg = xg_payload.get("prematch") or xg_payload
            match_id = _text(match.get("match_id"))
            league_id = _text(league.get("league_id") or league.get("name") or "unknown")
            home_id = _text(home.get("team_id") or home.get("team_name"))
            away_id = _text(away.get("team_id") or away.get("team_name"))
            if not match_id or not home_id or not away_id:
                continue
            kickoff = match.get("date_unix")
            kickoff_utc = (
                datetime.fromtimestamp(int(kickoff), tz=timezone.utc).isoformat()
                if kickoff is not None
                else None
            )
            connection.execute(
                "INSERT INTO leagues(league_id, name, country) VALUES (?, ?, ?) "
                "ON CONFLICT(league_id) DO UPDATE SET name=excluded.name, country=excluded.country",
                (league_id, str(league.get("name") or league_id), league.get("country")),
            )
            connection.executemany(
                "INSERT INTO teams(team_id, name) VALUES (?, ?) "
                "ON CONFLICT(team_id) DO UPDATE SET name=excluded.name",
                [(home_id, str(home.get("team_name") or home_id)), (away_id, str(away.get("team_name") or away_id))],
            )
            connection.execute(
                "INSERT INTO matches(match_id, league_id, season_year, kickoff_utc, status, "
                "home_team_id, away_team_id, home_goals, away_goals, home_xg, away_xg, source, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(match_id) DO UPDATE SET league_id=excluded.league_id, "
                "season_year=excluded.season_year, kickoff_utc=excluded.kickoff_utc, status=excluded.status, "
                "home_team_id=excluded.home_team_id, away_team_id=excluded.away_team_id, "
                "home_goals=excluded.home_goals, away_goals=excluded.away_goals, home_xg=excluded.home_xg, "
                "away_xg=excluded.away_xg, source=excluded.source, fetched_at=excluded.fetched_at",
                (
                    match_id,
                    league_id,
                    datetime.fromtimestamp(int(kickoff), tz=timezone.utc).year if kickoff is not None else None,
                    kickoff_utc,
                    match.get("status"),
                    home_id,
                    away_id,
                    _number(score.get("home")),
                    _number(score.get("away")),
                    _number(xg.get("home")),
                    _number(xg.get("away")),
                    source,
                    fetched_at,
                ),
            )
        connection.commit()
    return len(payloads)


def store_fixture_payloads(payloads: list[dict], *, source: str, path: str | Path | None = None) -> int:
    """Store OpenLiga-shaped dictionaries after normalizing them."""
    normalized: list[dict] = []
    for match in payloads:
        score = match.get("matchResults") or []
        final = next((item for item in score if item.get("resultTypeID") == 2), score[-1] if score else {})
        finished = bool(match.get("matchIsFinished")) and final
        kickoff = datetime.fromisoformat(match["matchDateTimeUTC"].replace("Z", "+00:00"))
        normalized.append(
            {
                "match_id": match.get("matchID"),
                "date_unix": int(kickoff.timestamp()),
                "status": "complete" if finished else "scheduled",
                "league": {"league_id": match.get("leagueShortcut", "openliga"), "name": match.get("leagueName", "")},
                "home_team": {"team_id": match.get("team1", {}).get("teamName"), "team_name": match.get("team1", {}).get("teamName")},
                "away_team": {"team_id": match.get("team2", {}).get("teamName"), "team_name": match.get("team2", {}).get("teamName")},
                "score": {"home": final.get("pointsTeam1") if finished else None, "away": final.get("pointsTeam2") if finished else None},
            }
        )
    return store_match_payloads(normalized, source=source, path=path)


def safe_store(payloads: list[dict], *, source: str, path: str | Path | None = None) -> None:
    try:
        store_match_payloads(payloads, source=source, path=path)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        log.warning("Uloženie zápasov do lokálnej DB zlyhalo: %s", exc)


def store_fixtures(payloads: list[dict], *, source: str, path: str | Path | None = None) -> int:
    """Public provider-neutral entry point for already normalized payloads."""
    return store_match_payloads(payloads, source=source, path=path)


def fetch_finished_matches(
    league: str | None = None, path: str | Path | None = None
) -> list[sqlite3.Row]:
    with connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if league:
            selected = connection.execute(
                "SELECT league_id FROM leagues WHERE league_id = ? OR lower(name) LIKE ? LIMIT 1",
                (league, f"%{league.lower()}%"),
            ).fetchone()
            if selected is None:
                return []
            league_id = str(selected[0])
            return connection.execute(
                "SELECT * FROM matches WHERE league_id = ? AND lower(status) IN "
                "('complete', 'finished', 'final') AND home_goals IS NOT NULL AND away_goals IS NOT NULL",
                (league_id,),
            ).fetchall()
        return connection.execute(
            "SELECT * FROM matches WHERE lower(status) IN ('complete', 'finished', 'final') "
            "AND home_goals IS NOT NULL AND away_goals IS NOT NULL"
        ).fetchall()
