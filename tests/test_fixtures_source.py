import json
from datetime import date, datetime, timedelta, timezone

import pytest

from mocksite import fixtures_source, simulator
from mocksite.data import reload_fixtures
from mocksite.fixtures_source import load_fixtures, load_json_file, load_synthetic


@pytest.fixture()
def restore_fixtures():
    yield
    reload_fixtures("synthetic")


def write_fixture_file(tmp_path, kickoff: datetime):
    path = tmp_path / "fixtures.json"
    path.write_text(
        json.dumps(
            [
                {
                    "match_id": "real1",
                    "home": "Bayern",
                    "away": "Dortmund",
                    "kickoff_utc": kickoff.isoformat().replace("+00:00", "Z"),
                    "competition": "Bundesliga",
                    "h2h": [
                        {
                            "played_on": "2025-04-01",
                            "home": "Bayern",
                            "away": "Dortmund",
                            "home_goals": 2,
                            "away_goals": 1,
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    return path


FOOTBALLDATA_DAY = {
    "success": True,
    "data": {
        "date": "2026-08-12",
        "matches": [
            {
                "match_id": 1,
                "date_unix": 1786492800,
                "status": "complete",
                "league": {"league_id": 9, "name": "USA Leagues Cup"},
                "home_team": {"team_id": 10, "team_name": "Cincinnati"},
                "away_team": {"team_id": 11, "team_name": "Atlas"},
                "xg": {"prematch": {"home": 1.9, "away": 1.1}},
            },
            {
                "match_id": 2,
                "date_unix": 1786579200,
                "status": "incomplete",
                "league": {"league_id": 12, "name": "England Premier League"},
                "home_team": {"team_id": 20, "team_name": "Arsenal"},
                "away_team": {"team_id": 21, "team_name": "Chelsea"},
                "xg": {"prematch": {"home": 2.0, "away": 1.2}},
            },
        ],
    },
}

FOOTBALLDATA_H2H = {
    "success": True,
    "data": {
        "matches": [
            {
                "match_date": "2025-04-01 16:00:00",
                "status": "complete",
                "home_team": {"team_name": "Arsenal"},
                "away_team": {"team_name": "Chelsea"},
                "score": {"home": 2, "away": 1},
            },
            {
                "match_date": "2026-09-01 16:00:00",
                "status": "incomplete",
                "home_team": {"team_name": "Arsenal"},
                "away_team": {"team_name": "Chelsea"},
                "score": {"home": None, "away": None},
            },
        ]
    },
}


@pytest.fixture()
def footballdata_api(monkeypatch):
    monkeypatch.setenv("FOOTBALLDATA_API_KEY", "fd_test_key")
    calls: list[str] = []

    def fake_get(url, cache_key, max_age_s=3600.0, headers=None):
        calls.append(url)
        assert headers["Authorization"] == "Bearer fd_test_key"
        return FOOTBALLDATA_H2H if "/h2h/" in url else FOOTBALLDATA_DAY

    monkeypatch.setattr(fixtures_source, "_cached_get_json", fake_get)
    return calls


def test_footballdata_provider_skips_finished_and_uses_prematch_xg(footballdata_api):
    fixtures = fixtures_source.load_footballdata(date(2026, 8, 13))
    assert [f.match_id for f in fixtures] == ["m2"]

    fixture = fixtures[0]
    assert (fixture.home, fixture.away) == ("Arsenal", "Chelsea")
    assert fixture.competition == "England Premier League"
    assert fixture.kickoff_utc == datetime(2026, 8, 13, tzinfo=timezone.utc)
    # xG feeds the simulator lambdas: lam_home = (home_attack + away_defence) / 2.
    assert (fixture.home_attack + fixture.away_defence) / 2 == pytest.approx(2.0)
    assert (fixture.away_attack + fixture.home_defence) / 2 == pytest.approx(1.2)
    # Only played matches count as head-to-head history.
    assert [(m.home_goals, m.away_goals) for m in fixture.h2h] == [(2, 1)]


def test_footballdata_league_filter_accepts_id_or_name(footballdata_api):
    by_id = fixtures_source.load_footballdata(date(2026, 8, 13), "12")
    by_name = fixtures_source.load_footballdata(date(2026, 8, 13), "premier league")
    assert [f.match_id for f in by_id] == [f.match_id for f in by_name] == ["m2"]
    assert fixtures_source.load_footballdata(date(2026, 8, 13), "Bundesliga") == []


def test_footballdata_requires_api_key(monkeypatch):
    monkeypatch.delenv("FOOTBALLDATA_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="FOOTBALLDATA_API_KEY"):
        fixtures_source.load_footballdata(date(2026, 8, 13))


def test_provider_status_keeps_finished_match_out_of_the_live_offer(monkeypatch):
    monkeypatch.setenv("MOCK_CLOCK", "demo")
    fixture = fixtures_source.Fixture(
        match_id="done1",
        home="A",
        away="B",
        kickoff_utc=datetime.now(timezone.utc),
        provider_status="complete",
    )
    assert simulator._elapsed_minutes(fixture)[1] == simulator.FINISHED


def test_synthetic_provider_has_kickoffs_and_history():
    fixtures = load_synthetic(date(2026, 8, 12))
    assert len(fixtures) == 4
    assert all(len(f.h2h) == 8 for f in fixtures)
    assert all(f.kickoff_utc.date() == date(2026, 8, 12) for f in fixtures)


def test_json_provider(tmp_path):
    path = write_fixture_file(tmp_path, datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc))
    fixtures = load_json_file(path)
    assert fixtures[0].home == "Bayern"
    assert fixtures[0].h2h[0].total_goals == 3
    assert load_fixtures(str(path))[0].match_id == "real1"


def test_real_clock_marks_match_scheduled_before_kickoff(tmp_path, monkeypatch, restore_fixtures):
    kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
    monkeypatch.setenv("MOCK_CLOCK", "real")
    reload_fixtures(str(write_fixture_file(tmp_path, kickoff)))
    state = simulator.state_of("real1")
    assert state.status == simulator.SCHEDULED
    assert state.minute == 0
    assert not state.tradable


def test_real_clock_marks_match_live_during_play(tmp_path, monkeypatch, restore_fixtures):
    kickoff = datetime.now(timezone.utc) - timedelta(minutes=30)
    monkeypatch.setenv("MOCK_CLOCK", "real")
    reload_fixtures(str(write_fixture_file(tmp_path, kickoff)))
    state = simulator.state_of("real1")
    assert state.status == simulator.LIVE
    assert 20 <= state.minute <= 35
    assert simulator.live_match_ids() == ["real1"]


def test_real_clock_marks_match_finished_after_full_time(tmp_path, monkeypatch, restore_fixtures):
    kickoff = datetime.now(timezone.utc) - timedelta(hours=4)
    monkeypatch.setenv("MOCK_CLOCK", "real")
    reload_fixtures(str(write_fixture_file(tmp_path, kickoff)))
    state = simulator.state_of("real1")
    assert state.status == simulator.FINISHED
    assert state.minute == simulator.MATCH_MINUTES
    assert simulator.live_match_ids() == []
