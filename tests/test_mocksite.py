import pytest
from datetime import datetime, timezone

from mocksite import simulator
from mocksite.app import create_app
from mocksite.data import (
    FIXTURES,
    PROVIDER_LIVE_FIXTURES,
    PROVIDER_LIVE_PAYLOADS,
    PROVIDER_UPCOMING_FIXTURES,
    refresh_live_data,
)
from mocksite.fixtures_source import Fixture
from mocksite.store import store_match_events, store_match_payloads, store_match_stats


@pytest.fixture()
def client():
    return create_app().test_client()


def test_pages_render(client):
    for url in ["/", "/livescore/", "/livescore/match/m1", "/book/", "/book/live"]:
        assert client.get(url).status_code == 200


def test_index_has_stable_live_and_upcoming_sections(client):
    body = client.get("/").get_data(as_text=True)
    assert body.count('data-testid="live-section"') == 1
    assert body.count('data-testid="upcoming-section"') == 1
    assert 'data-testid="finished-section"' not in body
    assert body.index('data-testid="live-section"') < body.index('data-testid="upcoming-section"')


def test_simulated_detail_labels_provenance(client):
    body = client.get("/livescore/match/m1").get_data(as_text=True)
    assert "simulované" in body
    assert 'data-testid="event-timeline"' in body


def test_provider_detail_renders_all_stats_and_events(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "provider.sqlite3"))
    store_match_payloads(
        [
            {
                "match_id": 1,
                "date_unix": 1760000000,
                "status": "incomplete",
                "league": {"league_id": 9, "name": "Liga"},
                "home_team": {"team_id": 10, "team_name": "Slovan Bratislava"},
                "away_team": {"team_id": 11, "team_name": "Spartak Trnava"},
            }
        ],
        source="footballdata",
    )
    store_match_stats(
        "1",
        {
            "possession": {"home": None, "away": 52},
            "shots": {"home": 4, "away": 2, "total": 6},
            "shots_on_target": {"home": 2, "away": 1, "total": 3},
            "shots_off_target": {"home": 2, "away": 1, "total": 3},
            "fouls": {"home": 4, "away": 5, "total": 9},
            "offsides": {"home": 1, "away": 0, "total": 1},
            "yellow_cards": {"home": 0, "away": 2, "total": 2},
            "dangerous_attacks": {"home": 20, "away": 18, "total": 38},
            "throwins": {"home": None, "away": None, "total": None},
            "goal_kicks": {"home": 3, "away": 4, "total": 7},
            "penalties_won": {"home": 0, "away": 1, "total": 1},
            "xg": {"home": 1.2, "away": 0.7, "total": 1.9},
            "half_time_goals": {"home": 1, "away": 0, "total": 1},
        },
    )
    store_match_events(
        "1",
        [{"minute": 7, "team_side": "home", "event_type": "goal", "player": {"player_name": "Strelec"}}],
    )
    body = create_app().test_client().get("/livescore/match/m1").get_data(as_text=True)
    assert "dáta z footballdata.io" in body
    assert 'data-stat="possession"' in body
    assert 'data-stat="shots"' in body
    assert "Držanie lopty" in body
    assert "Strely na bránu" in body
    assert "Fauly" in body
    assert "Ofsajdy" in body
    assert "Žlté karty" in body
    assert "Nebezpečné útoky" in body
    assert "Vhadzovania" in body
    assert "Odkopy od brány" in body
    assert "Vybojované penalty" in body
    assert "xG" in body
    assert "Góly v polčase" in body
    assert ">–<" in body
    assert 'data-testid="event-row"' in body
    assert "Strelec" in body


def test_provider_live_set_drives_index_and_detail(monkeypatch, tmp_path):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "provider-live.sqlite3"))
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "false")
    payload = {
        "match_id": 999,
        "date_unix": 1600000000,
        "status": "incomplete",
        "status_localized": "Live",
        "league": {"league_id": 9, "name": "Liga"},
        "home_team": {"team_id": 10, "team_name": "API Domáci"},
        "away_team": {"team_id": 11, "team_name": "API Hostia"},
        "score": {"home": 2, "away": 1},
        "minute": 71,
    }
    monkeypatch.setattr(
        "mocksite.fixtures_source.load_footballdata_live",
        lambda: [payload],
    )
    monkeypatch.setenv("MOCK_FIXTURES", "footballdata")
    monkeypatch.setattr("mocksite.data.reload_fixtures", lambda: [])
    from mocksite.store import store_match_payloads

    store_match_payloads([payload], source="footballdata")
    refresh_live_data()
    try:
        body = create_app().test_client().get("/").get_data(as_text=True)
        assert "API Domáci" in body
        assert 'data-testid="live-section"' in body
        assert 'data-status="live"' in body
        assert 'data-testid="finished-section"' not in body
        detail = create_app().test_client().get("/livescore/match/m999")
        assert detail.status_code == 200
        assert ">2:1<" in detail.get_data(as_text=True)
    finally:
        PROVIDER_LIVE_FIXTURES.clear()
        PROVIDER_LIVE_PAYLOADS.clear()


def test_empty_provider_live_section_is_explicit(monkeypatch):
    monkeypatch.setenv("MOCK_FIXTURES", "footballdata")
    monkeypatch.setenv("MOCK_REFRESH_S", "0")
    monkeypatch.setenv("MOCK_CLOCK", "real")
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "false")
    PROVIDER_LIVE_FIXTURES.clear()
    PROVIDER_LIVE_PAYLOADS.clear()
    body = create_app().test_client().get("/").get_data(as_text=True)
    assert "Momentálne sa nehrá žiadny zápas z API" in body
    assert "MOCK_CLOCK=demo" in body


def test_provider_upcoming_feed_is_merged_and_dates_are_displayed(monkeypatch):
    monkeypatch.setenv("MOCK_FIXTURES", "footballdata")
    monkeypatch.setenv("MOCK_REFRESH_S", "0")
    monkeypatch.setenv("MOCK_CLOCK", "real")
    monkeypatch.setenv("WERKZEUG_RUN_MAIN", "false")
    future = Fixture(
        match_id="m-upcoming",
        home="Vzdialení Domáci",
        away="Vzdialení Hostia",
        kickoff_utc=datetime(2099, 12, 31, 20, 30, tzinfo=timezone.utc),
        competition="Budúca liga",
    )
    earlier = Fixture(
        match_id="m-earlier",
        home="Skorší Domáci",
        away="Skorší Hostia",
        kickoff_utc=datetime(2099, 12, 30, 20, 30, tzinfo=timezone.utc),
        competition="Budúca liga",
    )
    PROVIDER_LIVE_FIXTURES.clear()
    PROVIDER_LIVE_PAYLOADS.clear()
    PROVIDER_UPCOMING_FIXTURES.clear()
    PROVIDER_UPCOMING_FIXTURES[future.match_id] = future
    PROVIDER_UPCOMING_FIXTURES[earlier.match_id] = earlier
    try:
        body = create_app().test_client().get("/").get_data(as_text=True)
        assert "Vzdialení Domáci" in body
        assert "31.12.2099 20:30" in body
        assert body.index("Skorší Domáci") < body.index("Vzdialení Domáci")
        assert 'data-testid="upcoming-section"' in body
    finally:
        PROVIDER_UPCOMING_FIXTURES.clear()


def test_livescore_exposes_h2h_rows(client):
    body = client.get("/livescore/match/m1").get_data(as_text=True)
    assert body.count('data-testid="h2h-row"') == len(FIXTURES[0].h2h)


def test_bookmaker_exposes_both_markets(client):
    body = client.get("/book/live").get_data(as_text=True)
    over = body.count('data-market="over_2.5"')
    under = body.count('data-market="under_2.5"')
    # Settled markets hide their buttons, but the two sides always come in pairs.
    assert over == under
    assert over + body.count('data-testid="market-settled"') == len(FIXTURES)


def test_betslip_endpoint_accepts_json(client):
    response = client.post("/book/api/betslip", json={"selection": {"market": "over_2.5"}, "stake": 1})
    assert response.get_json()["status"] == "accepted"


def test_timeline_is_monotonic_and_deterministic():
    first = [simulator.state_of("m1", m) for m in range(91)]
    second = [simulator.state_of("m1", m) for m in range(91)]
    assert first == second
    goals = [s.total_goals for s in first]
    assert goals == sorted(goals)


def test_odds_reflect_margin():
    state = simulator.state_of("m1", 0)
    overround = 1 / state.over25_odds + 1 / state.under25_odds
    assert overround > 1.0


def test_odds_are_never_below_one():
    for match_id in [f.match_id for f in FIXTURES]:
        for minute in range(91):
            state = simulator.state_of(match_id, minute)
            assert state.over25_odds >= 1.01
            assert state.under25_odds >= 1.01


def test_market_settles_once_third_goal_is_scored():
    states = [simulator.state_of("m1", m) for m in range(91)]
    for state in states:
        if state.total_goals >= 3:
            assert state.settled
