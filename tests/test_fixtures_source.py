import json
from datetime import date, datetime, timedelta, timezone

import pytest

from mocksite import simulator
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
