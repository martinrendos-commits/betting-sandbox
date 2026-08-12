import sqlite3

from mocksite.store import (
    connect,
    latest_match_events,
    latest_match_stats,
    store_api_payload,
    store_match_events,
    store_match_payloads,
    store_match_stats,
)


def payload(match_id="1", home_goals=2, away_goals=1, status="complete"):
    return {
        "match_id": match_id,
        "date_unix": 1760000000,
        "status": status,
        "league": {"league_id": 9, "name": "Test Liga", "country": "SK"},
        "home_team": {"team_id": 10, "team_name": "Domaci"},
        "away_team": {"team_id": 11, "team_name": "Hostia"},
        "score": {"home": home_goals, "away": away_goals},
        "xg": {"home": 2.4, "away": 1.1, "prematch": {"home": 1.8, "away": 0.9}},
    }


def test_schema_and_upsert_are_idempotent(tmp_path):
    path = tmp_path / "matches.sqlite3"
    assert store_match_payloads([payload()], source="footballdata", path=path) == 1
    updated = payload(home_goals=3, away_goals=2)
    store_match_payloads([updated], source="footballdata", path=path)
    with connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM matches").fetchone()[0] == 1
        assert connection.execute("SELECT home_goals FROM matches").fetchone()[0] == 3
        assert connection.execute("SELECT COUNT(*) FROM leagues").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 2


def test_scheduled_score_values_are_null(tmp_path):
    path = tmp_path / "matches.sqlite3"
    scheduled = payload(status="incomplete", home_goals=None, away_goals=None)
    store_match_payloads([scheduled], source="footballdata", path=path)
    with connect(path) as connection:
        assert connection.execute("SELECT home_goals, away_goals FROM matches").fetchone() == (None, None)


def test_extended_match_fields_and_raw_payload_are_persisted(tmp_path):
    path = tmp_path / "matches.sqlite3"
    match = payload()
    match.update(
        {
            "status_localized": "Finished",
            "round_id": 12,
            "game_week": 4,
            "season": {"season_id": 88, "year": 2026},
            "score": {
                "home": 2,
                "away": 1,
                "halftime_home": 1,
                "halftime_away": 0,
                "second_half_home": 1,
                "second_half_away": 1,
            },
            "venue": {"name": "Štadión", "location": "Bratislava"},
            "winner_text": "Domáci vyhrali",
        }
    )
    store_match_payloads([match], source="footballdata", path=path)
    store_api_payload(match, endpoint="matches/1", source="footballdata", match_id="1", path=path)
    with connect(path) as connection:
        row = connection.execute(
            "SELECT season_id, round_id, game_week, halftime_home, second_half_away, "
            "actual_home_xg, home_xg, venue_name, winner FROM matches"
        ).fetchone()
        assert row == ("88", "12", 4, 1, 1, 2.4, 1.8, "Štadión", "Domáci vyhrali")
        assert connection.execute("SELECT COUNT(*) FROM api_payloads").fetchone()[0] == 1


def test_stats_and_events_keep_nulls_and_latest_snapshot(tmp_path):
    path = tmp_path / "matches.sqlite3"
    store_match_payloads([payload()], source="footballdata", path=path)
    store_match_stats(
        "1",
        {"possession": {"home": None, "away": 48}, "shots": {"home": 3, "away": 2, "total": 5}},
        path=path,
    )
    store_match_events(
        "1",
        [
            {
                "minute": 13,
                "extra_minute": None,
                "team_side": "away",
                "event_type": "yellow_card",
                "player": {"player_id": 7, "player_name": "Hráč"},
            }
        ],
        path=path,
    )
    stats = latest_match_stats("1", path=path)
    events = latest_match_events("1", path=path)
    assert [(row["metric"], row["home_value"], row["away_value"]) for row in stats] == [
        ("possession", None, "48"),
        ("shots", "3", "2"),
    ]
    assert events[0]["minute"] == 13
    assert events[0]["player_name"] == "Hráč"


def test_existing_database_is_migrated_additively(tmp_path):
    path = tmp_path / "old.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE leagues (league_id TEXT PRIMARY KEY, name TEXT NOT NULL, country TEXT);
            CREATE TABLE teams (team_id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE matches (
                match_id TEXT PRIMARY KEY, league_id TEXT NOT NULL, season_year INTEGER,
                kickoff_utc TEXT, status TEXT, home_team_id TEXT NOT NULL, away_team_id TEXT NOT NULL,
                home_goals INTEGER, away_goals INTEGER, home_xg REAL, away_xg REAL,
                source TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            """
        )
    with connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(matches)")}
        assert "actual_home_xg" in columns
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'api_payloads'").fetchone()
