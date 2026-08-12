import pytest

from mocksite import simulator
from mocksite.app import create_app
from mocksite.data import FIXTURES
from mocksite.store import store_match_events, store_match_payloads, store_match_stats


@pytest.fixture()
def client():
    return create_app().test_client()


def test_pages_render(client):
    for url in ["/", "/livescore/", "/livescore/match/m1", "/book/", "/book/live"]:
        assert client.get(url).status_code == 200


def test_index_has_three_stable_sections(client):
    body = client.get("/").get_data(as_text=True)
    assert body.count('data-testid="live-section"') == 1
    assert body.count('data-testid="upcoming-section"') == 1
    assert body.count('data-testid="finished-section"') == 1
    assert body.index('data-testid="live-section"') < body.index('data-testid="upcoming-section"')
    assert body.index('data-testid="upcoming-section"') < body.index('data-testid="finished-section"')


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
        {"possession": {"home": None, "away": 52}, "shots": {"home": 4, "away": 2, "total": 6}},
    )
    store_match_events(
        "1",
        [{"minute": 7, "team_side": "home", "event_type": "goal", "player": {"player_name": "Strelec"}}],
    )
    body = create_app().test_client().get("/livescore/match/m1").get_data(as_text=True)
    assert "dáta z footballdata.io" in body
    assert 'data-stat="possession"' in body
    assert 'data-stat="shots"' in body
    assert ">–<" in body
    assert 'data-testid="event-row"' in body
    assert "Strelec" in body


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
