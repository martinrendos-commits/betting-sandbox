"""Small, resilient SQLite persistence layer for provider data."""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("mocksite.store")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = ROOT / "data" / "sandbox.sqlite3"
WRITE_RETRIES = 3


def db_path() -> Path:
    return Path(os.environ.get("MOCK_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    selected = Path(path) if path is not None else db_path()
    selected.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(selected, timeout=2.0)
    connection.execute("PRAGMA foreign_keys = ON")
    create_schema(connection)
    return connection


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_columns(connection: sqlite3.Connection, table: str, definitions: dict[str, str]) -> None:
    existing = _columns(connection, table)
    for name, definition in definitions.items():
        if name in existing:
            continue
        try:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise


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
        CREATE TABLE IF NOT EXISTS api_payloads (
            payload_id INTEGER PRIMARY KEY AUTOINCREMENT,
            endpoint TEXT NOT NULL,
            match_id TEXT,
            fetched_at TEXT NOT NULL,
            source TEXT NOT NULL,
            payload_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS match_stats_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            metric TEXT NOT NULL,
            home_value TEXT,
            away_value TEXT,
            total_value TEXT,
            raw_json TEXT,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS match_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            minute INTEGER,
            extra_minute INTEGER,
            team_side TEXT,
            event_type TEXT,
            detail TEXT,
            player_id TEXT,
            player_name TEXT,
            assist_player_id TEXT,
            assist_player_name TEXT,
            player_in_id TEXT,
            player_in_name TEXT,
            player_out_id TEXT,
            player_out_name TEXT,
            raw_json TEXT NOT NULL,
            FOREIGN KEY (match_id) REFERENCES matches(match_id)
        );
        CREATE TABLE IF NOT EXISTS sharp_events (
            event_id TEXT PRIMARY KEY,
            uuid TEXT,
            sport TEXT,
            league TEXT,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            start_time TEXT,
            status TEXT,
            is_live INTEGER,
            book_count INTEGER,
            market_count INTEGER,
            fetched_at TEXT NOT NULL,
            raw_payload_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS sharp_odds_snapshots (
            snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            sportsbook TEXT NOT NULL,
            market_type TEXT NOT NULL,
            selection TEXT,
            odds_decimal REAL,
            odds_american REAL,
            odds_probability REAL,
            is_live INTEGER,
            timestamp TEXT,
            fetched_at TEXT NOT NULL,
            raw_payload_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS sharp_event_links (
            event_id TEXT PRIMARY KEY,
            match_id TEXT NOT NULL,
            confidence REAL NOT NULL,
            matched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS matches_league_idx ON matches(league_id);
        CREATE INDEX IF NOT EXISTS api_payloads_match_idx ON api_payloads(match_id);
        CREATE INDEX IF NOT EXISTS stats_match_idx ON match_stats_snapshots(match_id, fetched_at);
        CREATE INDEX IF NOT EXISTS events_match_idx ON match_events(match_id, fetched_at);
        CREATE INDEX IF NOT EXISTS sharp_odds_event_idx ON sharp_odds_snapshots(event_id, fetched_at);
        CREATE INDEX IF NOT EXISTS sharp_links_match_idx ON sharp_event_links(match_id);
        """
    )
    _add_columns(
        connection,
        "matches",
        {
            "season_id": "TEXT",
            "round_id": "TEXT",
            "game_week": "INTEGER",
            "status_localized": "TEXT",
            "halftime_home": "INTEGER",
            "halftime_away": "INTEGER",
            "second_half_home": "INTEGER",
            "second_half_away": "INTEGER",
            "actual_home_xg": "REAL",
            "actual_away_xg": "REAL",
            "venue_name": "TEXT",
            "venue_location": "TEXT",
            "winner": "TEXT",
            "last_updated": "TEXT",
        },
    )
    _add_columns(
        connection,
        "sharp_odds_snapshots",
        {
            "line": "REAL",
            "market_concept": "TEXT",
            "selection_key": "TEXT",
            "is_player_prop": "INTEGER NOT NULL DEFAULT 0",
        },
    )
    connection.commit()


def _text(value: object | None) -> str | None:
    return None if value is None else str(value)


def _number(value: object | None) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        return float(value) if isinstance(value, float) else int(value)
    except (TypeError, ValueError):
        return None


def _value(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _run_write(writer, path: str | Path | None) -> object:
    last_error: sqlite3.OperationalError | None = None
    for attempt in range(WRITE_RETRIES):
        try:
            with connect(path) as connection:
                result = writer(connection)
                connection.commit()
                return result
        except sqlite3.OperationalError as exc:
            last_error = exc
            if attempt + 1 < WRITE_RETRIES:
                time.sleep(0.05 * (attempt + 1))
    if last_error is not None:
        raise last_error
    raise RuntimeError("SQLite write failed")


def _match_values(match: dict, fetched_at: str) -> tuple:
    league = match.get("league") or {}
    season = match.get("season") or {}
    home = match.get("home_team") or {}
    away = match.get("away_team") or {}
    score = match.get("score") or {}
    xg_payload = match.get("xg") or {}
    prematch = xg_payload.get("prematch") or {}
    actual_home_xg = xg_payload.get("home") if "home" in xg_payload else None
    actual_away_xg = xg_payload.get("away") if "away" in xg_payload else None
    match_id = _text(match.get("match_id"))
    league_id = _text(league.get("league_id") or league.get("name") or "unknown")
    home_id = _text(home.get("team_id") or home.get("team_name"))
    away_id = _text(away.get("team_id") or away.get("team_name"))
    kickoff = match.get("date_unix")
    kickoff_utc = (
        datetime.fromtimestamp(int(kickoff), tz=timezone.utc).isoformat()
        if kickoff is not None
        else None
    )
    season_year = _number(season.get("year"))
    if season_year is None and kickoff is not None:
        season_year = datetime.fromtimestamp(int(kickoff), tz=timezone.utc).year
    last_updated = match.get("last_updated")
    if isinstance(last_updated, dict):
        last_updated = last_updated.get("synced_at") or last_updated.get("source_last_updated")
    return (
        match_id,
        league_id,
        season_year,
        kickoff_utc,
        match.get("status"),
        home_id,
        away_id,
        _number(score.get("home")),
        _number(score.get("away")),
        _number(prematch.get("home") if "home" in prematch else xg_payload.get("prematch_home")),
        _number(prematch.get("away") if "away" in prematch else xg_payload.get("prematch_away")),
        fetched_at,
        match.get("status_localized"),
        _text(season.get("season_id")),
        _text(match.get("round_id")),
        _number(match.get("game_week")),
        _number(score.get("halftime_home")),
        _number(score.get("halftime_away")),
        _number(score.get("second_half_home")),
        _number(score.get("second_half_away")),
        _number(actual_home_xg),
        _number(actual_away_xg),
        (match.get("venue") or {}).get("name") or (match.get("venue") or {}).get("stadium"),
        (match.get("venue") or {}).get("location"),
        match.get("winner_text") or score.get("winner"),
        last_updated,
    )


def store_match_payloads(
    payloads: list[dict],
    *,
    source: str,
    path: str | Path | None = None,
) -> int:
    """Upsert provider-native match dictionaries and return valid row count."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> int:
        stored = 0
        for match in payloads:
            values = _match_values(match, fetched_at)
            match_id, league_id, _, _, _, home_id, away_id, *_ = values
            if not match_id or not home_id or not away_id:
                continue
            league = match.get("league") or {}
            home = match.get("home_team") or {}
            away = match.get("away_team") or {}
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
                "home_team_id, away_team_id, home_goals, away_goals, home_xg, away_xg, source, fetched_at, "
                "status_localized, season_id, round_id, game_week, halftime_home, halftime_away, "
                "second_half_home, second_half_away, actual_home_xg, actual_away_xg, venue_name, "
                "venue_location, winner, last_updated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(match_id) DO UPDATE SET league_id=excluded.league_id, season_year=excluded.season_year, "
                "kickoff_utc=excluded.kickoff_utc, status=excluded.status, home_team_id=excluded.home_team_id, "
                "away_team_id=excluded.away_team_id, home_goals=excluded.home_goals, away_goals=excluded.away_goals, "
                "home_xg=excluded.home_xg, away_xg=excluded.away_xg, source=excluded.source, fetched_at=excluded.fetched_at, "
                "status_localized=excluded.status_localized, season_id=excluded.season_id, round_id=excluded.round_id, "
                "game_week=excluded.game_week, halftime_home=excluded.halftime_home, halftime_away=excluded.halftime_away, "
                "second_half_home=excluded.second_half_home, second_half_away=excluded.second_half_away, "
                "actual_home_xg=excluded.actual_home_xg, actual_away_xg=excluded.actual_away_xg, venue_name=excluded.venue_name, "
                "venue_location=excluded.venue_location, winner=excluded.winner, last_updated=excluded.last_updated",
                values[:11] + (source, values[11]) + values[12:],
            )
            stored += 1
        return stored

    return int(_run_write(write, path))


def store_fixture_payloads(payloads: list[dict], *, source: str, path: str | Path | None = None) -> int:
    """Store OpenLiga-shaped dictionaries after normalizing them."""
    normalized: list[dict] = []
    for match in payloads:
        score = match.get("matchResults") or []
        final = next((item for item in score if item.get("resultTypeID") == 2), score[-1] if score else {})
        finished = bool(match.get("matchIsFinished")) and bool(final)
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


def store_api_payload(
    payload: dict,
    *,
    endpoint: str,
    source: str,
    match_id: str | None = None,
    path: str | Path | None = None,
) -> int:
    """Persist a successful provider response as JSON."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> int:
        cursor = connection.execute(
            "INSERT INTO api_payloads(endpoint, match_id, fetched_at, source, payload_json) VALUES (?, ?, ?, ?, ?)",
            (endpoint, match_id, fetched_at, source, json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        )
        return int(cursor.lastrowid)

    return int(_run_write(write, path))


def store_sharp_events(events: list[dict], *, raw_payload_id: int | None = None, path: str | Path | None = None) -> int:
    """Persist normalized SharpAPI event rows."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> int:
        stored = 0
        for event in events:
            event_id = _text(event.get("id"))
            home = _text(event.get("home_team"))
            away = _text(event.get("away_team"))
            if not event_id or not home or not away:
                continue
            connection.execute(
                "INSERT INTO sharp_events(event_id, uuid, sport, league, home_team, away_team, start_time, "
                "status, is_live, book_count, market_count, fetched_at, raw_payload_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(event_id) DO UPDATE SET uuid=excluded.uuid, sport=excluded.sport, league=excluded.league, "
                "home_team=excluded.home_team, away_team=excluded.away_team, start_time=excluded.start_time, status=excluded.status, "
                "is_live=excluded.is_live, book_count=excluded.book_count, market_count=excluded.market_count, fetched_at=excluded.fetched_at, "
                "raw_payload_id=excluded.raw_payload_id",
                (event_id, _text(event.get("uuid")), _text(event.get("sport")), _text(event.get("league")), home, away,
                 _text(event.get("start_time")), _text(event.get("status")), int(bool(event.get("is_live"))),
                 _number(event.get("book_count")), _number(event.get("market_count")), fetched_at, raw_payload_id),
            )
            stored += 1
        return stored

    return int(_run_write(write, path))


def store_sharp_odds(
    odds: list[dict], *, raw_payload_id: int | None = None, path: str | Path | None = None
) -> int:
    """Persist normalized SharpAPI sportsbook odds rows."""
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> int:
        for row in odds:
            connection.execute(
                "INSERT INTO sharp_odds_snapshots(event_id, sportsbook, market_type, selection, odds_decimal, "
                "odds_american, odds_probability, is_live, timestamp, fetched_at, raw_payload_id, line, "
                "market_concept, selection_key, is_player_prop) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (_text(row.get("event_id")) or "", _text(row.get("sportsbook")) or "",
                 _text(row.get("market_type")) or "", _text(row.get("selection")),
                 row.get("odds_decimal"), row.get("odds_american"), row.get("odds_probability"),
                 int(bool(row.get("is_live"))), _text(row.get("timestamp")), fetched_at, raw_payload_id,
                 row.get("line"), _text(row.get("market_concept")), _text(row.get("selection_key")),
                 int(bool(row.get("is_player_prop")))),
            )
        return len(odds)

    return int(_run_write(write, path))


def store_sharp_event_link(event_id: str, match_id: str, confidence: float, *, path: str | Path | None = None) -> None:
    matched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> None:
        connection.execute(
            "INSERT INTO sharp_event_links(event_id, match_id, confidence, matched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(event_id) DO UPDATE SET match_id=excluded.match_id, confidence=excluded.confidence, matched_at=excluded.matched_at",
            (event_id, match_id, confidence, matched_at),
        )

    _run_write(write, path)


def latest_sharp_odds(event_id: str | None = None) -> list[dict]:
    with connect() as connection:
        if event_id is None:
            rows = connection.execute(
                "SELECT event_id, sportsbook, market_type, selection, odds_decimal, odds_american, odds_probability, "
                "is_live, timestamp FROM sharp_odds_snapshots WHERE fetched_at = (SELECT max(fetched_at) FROM sharp_odds_snapshots)"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT event_id, sportsbook, market_type, selection, odds_decimal, odds_american, odds_probability, "
                "is_live, timestamp, line, market_concept, selection_key, is_player_prop "
                "FROM sharp_odds_snapshots WHERE event_id=? AND fetched_at = "
                "(SELECT max(current.fetched_at) FROM sharp_odds_snapshots AS current WHERE current.event_id=?) "
                "ORDER BY sportsbook, market_type, selection",
                (event_id, event_id),
            ).fetchall()
    keys = ("event_id", "sportsbook", "market_type", "selection", "odds_decimal", "odds_american",
            "odds_probability", "is_live", "timestamp", "line", "market_concept", "selection_key",
            "is_player_prop")
    return [dict(zip(keys, row)) for row in rows]


def sharp_event_id_for_match(match_id: str) -> str | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT event_id FROM sharp_event_links WHERE match_id=? ORDER BY matched_at DESC LIMIT 1",
            (match_id,),
        ).fetchone()
    return str(row[0]) if row else None


def store_match_stats(
    match_id: str,
    stats: dict,
    *,
    source: str | None = None,
    endpoint: str | None = None,
    path: str | Path | None = None,
) -> None:
    del source, endpoint
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> None:
        for metric, values in stats.items():
            if not isinstance(values, dict):
                values = {"home": values}
            connection.execute(
                "INSERT INTO match_stats_snapshots(match_id, fetched_at, metric, home_value, away_value, total_value, raw_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id,
                    fetched_at,
                    metric,
                    _value(values.get("home")),
                    _value(values.get("away")),
                    _value(values.get("total")),
                    json.dumps(values, ensure_ascii=False, sort_keys=True),
                ),
            )

    _run_write(write, path)


def store_match_events(
    match_id: str,
    events: list[dict],
    *,
    source: str | None = None,
    endpoint: str | None = None,
    path: str | Path | None = None,
) -> None:
    del source, endpoint
    fetched_at = datetime.now(timezone.utc).isoformat()

    def write(connection: sqlite3.Connection) -> None:
        for event in events:
            player = event.get("player") or {}
            assist = event.get("assist") or {}
            player_in = event.get("player_in") or {}
            player_out = event.get("player_out") or {}
            connection.execute(
                "INSERT INTO match_events(match_id, fetched_at, minute, extra_minute, team_side, event_type, detail, "
                "player_id, player_name, assist_player_id, assist_player_name, player_in_id, player_in_name, "
                "player_out_id, player_out_name, raw_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    match_id,
                    fetched_at,
                    _number(event.get("minute")),
                    _number(event.get("extra_minute")),
                    event.get("team_side"),
                    event.get("event_type"),
                    event.get("detail"),
                    _text(player.get("player_id")),
                    player.get("player_name"),
                    _text(assist.get("player_id")),
                    assist.get("player_name"),
                    _text(player_in.get("player_id")),
                    player_in.get("player_name"),
                    _text(player_out.get("player_id")),
                    player_out.get("player_name"),
                    json.dumps(event, ensure_ascii=False, sort_keys=True),
                ),
            )

    _run_write(write, path)


def latest_match_stats(match_id: str, path: str | Path | None = None) -> list[sqlite3.Row]:
    with connect(path) as connection:
        connection.row_factory = sqlite3.Row
        latest = connection.execute(
            "SELECT MAX(fetched_at) FROM match_stats_snapshots WHERE match_id = ?", (match_id,)
        ).fetchone()[0]
        if latest is None:
            return []
        return connection.execute(
            "SELECT * FROM match_stats_snapshots WHERE match_id = ? AND fetched_at = ? ORDER BY snapshot_id",
            (match_id, latest),
        ).fetchall()


def latest_match_events(match_id: str, path: str | Path | None = None) -> list[sqlite3.Row]:
    with connect(path) as connection:
        connection.row_factory = sqlite3.Row
        latest = connection.execute(
            "SELECT MAX(fetched_at) FROM match_events WHERE match_id = ?", (match_id,)
        ).fetchone()[0]
        if latest is None:
            return []
        return connection.execute(
            "SELECT * FROM match_events WHERE match_id = ? AND fetched_at = ? ORDER BY minute, event_id",
            (match_id, latest),
        ).fetchall()


def safe_store(payloads: list[dict], *, source: str, path: str | Path | None = None) -> None:
    try:
        store_match_payloads(payloads, source=source, path=path)
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        log.warning("Uloženie zápasov do lokálnej DB zlyhalo: %s", exc)


def store_fixtures(payloads: list[dict], *, source: str, path: str | Path | None = None) -> int:
    return store_match_payloads(payloads, source=source, path=path)


def fetch_finished_matches(
    league: str | None = None, path: str | Path | None = None
) -> list[sqlite3.Row]:
    with connect(path) as connection:
        connection.row_factory = sqlite3.Row
        if league:
            if league.isdigit():
                selected = connection.execute(
                    "SELECT league_id FROM leagues WHERE league_id = ? LIMIT 1", (league,)
                ).fetchone()
            else:
                selected = connection.execute(
                    "SELECT league_id FROM leagues WHERE lower(name) LIKE ? LIMIT 1",
                    (f"%{league.lower()}%",),
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
