import sqlite3

from mocksite.store import connect, store_match_payloads


def payload(match_id="1", home_goals=2, away_goals=1, status="complete"):
    return {
        "match_id": match_id,
        "date_unix": 1760000000,
        "status": status,
        "league": {"league_id": 9, "name": "Test Liga", "country": "SK"},
        "home_team": {"team_id": 10, "team_name": "Domaci"},
        "away_team": {"team_id": 11, "team_name": "Hostia"},
        "score": {"home": home_goals, "away": away_goals},
        "xg": {"prematch": {"home": 1.8, "away": 0.9}},
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
