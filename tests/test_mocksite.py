import pytest

from mocksite import simulator
from mocksite.app import create_app
from mocksite.data import FIXTURES


@pytest.fixture()
def client():
    return create_app().test_client()


def test_pages_render(client):
    for url in ["/", "/livescore/", "/livescore/match/m1", "/book/", "/book/live"]:
        assert client.get(url).status_code == 200


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
