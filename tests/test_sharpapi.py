from datetime import datetime, timezone
from email.message import Message
from io import BytesIO
import re
from urllib.error import HTTPError

from mocksite import sharpapi_source
from mocksite.app import create_app
from mocksite.fixtures_source import Fixture
from mocksite.store import connect, latest_sharp_odds, store_sharp_events, store_sharp_odds


def test_sharpapi_request_uses_api_key_and_persists(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPAPI_API_KEY", "test-key")
    monkeypatch.setenv("SHARPAPI_DISABLED", "true")
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    monkeypatch.setenv("MOCK_CACHE_DIR", str(tmp_path / "cache"))
    sharpapi_source._REQUEST_TIMES.clear()
    sharpapi_source._SERVER_LIMIT = None

    class Response:
        headers = Message()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"data": [{"id": "e1", "sport": "soccer"}]}'

    seen = {}

    def fake_urlopen(request, timeout):
        seen.setdefault("key", request.headers["X-api-key"])
        seen.setdefault("url", request.full_url)
        return Response()

    monkeypatch.setattr(sharpapi_source.urllib.request, "urlopen", fake_urlopen)
    payload = sharpapi_source._request("sports")
    assert payload["data"][0]["id"] == "e1"
    assert seen == {"key": "test-key", "url": "https://api.sharpapi.io/api/v1/sports"}
    with connect() as connection:
        assert connection.execute("SELECT count(*) FROM api_payloads WHERE source='sharpapi'").fetchone()[0] == 1


def test_sharpapi_pagination_follows_cursor_with_page_cap(monkeypatch):
    requests = []
    payloads = iter(
        [
            ({"data": [1], "pagination": {"has_more": True, "next_cursor": "abc"}}, 1),
            ({"data": [2], "pagination": {"has_more": False}}, 2),
        ]
    )
    monkeypatch.setattr(
        sharpapi_source,
        "_request_with_id",
        lambda endpoint, params=None: requests.append((endpoint, params)) or next(payloads),
    )
    monkeypatch.setattr(sharpapi_source, "sharpapi_request_available", lambda: True)

    pages = sharpapi_source._request_pages("events", params={"sport": "soccer", "limit": 200})

    assert [payload["data"] for payload, _ in pages] == [[1], [2]]
    assert requests == [
        ("events", {"sport": "soccer", "limit": 200}),
        ("events", {"sport": "soccer", "limit": 200, "cursor": "abc"}),
    ]


def test_sharpapi_missing_key_and_tier_errors(monkeypatch):
    monkeypatch.delenv("SHARPAPI_API_KEY", raising=False)
    monkeypatch.setattr(sharpapi_source, "load_env_file", lambda: {})
    try:
        sharpapi_source.sports()
    except sharpapi_source.SharpAPIError as error:
        assert error.code == "missing_api_key"
        assert "chýba" in str(error)
    else:
        raise AssertionError("missing key should fail cleanly")

    error = sharpapi_source._error_from_payload(
        {"error": {"code": "tier_restricted", "required_tier": "Pro"}}, 403
    )
    assert error.required_tier == "Pro"
    assert "Pro" in str(error)


def test_sharpapi_retries_rate_limit_and_honors_header(monkeypatch, tmp_path):
    monkeypatch.setenv("SHARPAPI_API_KEY", "test-key")
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    monkeypatch.setenv("MOCK_CACHE_DIR", str(tmp_path / "cache"))
    sharpapi_source._REQUEST_TIMES.clear()
    sharpapi_source._SERVER_LIMIT = None
    calls = {"count": 0}

    class Response:
        headers = {"X-RateLimit-Limit": "7"}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"data": []}'

    def fake_urlopen(request, timeout):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPError(request.full_url, 429, "limited", {}, BytesIO(b'{"error":{"code":"rate_limited"}}'))
        return Response()

    monkeypatch.setattr(sharpapi_source.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(sharpapi_source.time, "sleep", lambda seconds: None)
    assert sharpapi_source._request("odds") == {"data": []}
    assert calls["count"] == 2
    assert sharpapi_source._account_limit() == 7


def test_sharpapi_normalization_and_matching(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    event = {
        "id": "sharp-1",
        "sport": "soccer",
        "home_team": "MŠK Žilina FC",
        "away_team": "AFC Trnava",
        "start_time": "2026-08-12T17:00:00Z",
    }
    fixture = Fixture(
        "fd-1",
        "MSK Zilina",
        "Trnava",
        datetime(2026, 8, 12, 17, 30, tzinfo=timezone.utc),
    )
    assert sharpapi_source.match_events_to_fixtures([event], [fixture]) == [
        {"event_id": "sharp-1", "match_id": "fd-1", "confidence": 1.0}
    ]
    wrong = Fixture(
        "fd-2",
        "MSK Zilina",
        "Kosice",
        fixture.kickoff_utc,
    )
    assert sharpapi_source.match_events_to_fixtures([event], [wrong]) == []
    assert store_sharp_events([event]) == 1
    assert store_sharp_odds(
        [{"event_id": "sharp-1", "sportsbook": "draftkings", "market_type": "moneyline", "selection": "MSK Zilina", "odds_decimal": 2.1}]
    ) == 1
    assert latest_sharp_odds("sharp-1")[0]["odds_decimal"] == 2.1


def test_soccer_markets_normalize_moneyline_draw_and_total_line():
    payload = {
        "data": [
            {
                "event_id": "e1",
                "sport": "soccer",
                "market_type": "moneyline",
                "home_team": "Home FC",
                "away_team": "Away FC",
                "selection": "Draw",
                "odds_decimal": 3.2,
            },
            {
                "event_id": "e1",
                "sport": "soccer",
                "market_type": "total_goals",
                "selection": "Over",
                "line": 2.5,
                "odds_decimal": 2.0,
            },
        ]
    }
    rows = sharpapi_source.normalize_odds(payload)
    assert rows[0]["market_concept"] == "1x2"
    assert rows[0]["selection_key"] == "draw"
    assert rows[1]["market_concept"] == "over_under"
    assert rows[1]["selection_key"] == "over_2_5"


def test_sharpapi_excludes_outrights_and_props_from_events():
    payload = {
        "data": [
            {"id": "match", "sport": "soccer", "home_team": "A", "away_team": "B"},
            {"id": "future", "sport": "soccer", "home_team": "League", "away_team": "", "market_type": "outright"},
            {"id": "prop", "sport": "soccer", "home_team": "A", "away_team": "B", "is_player_prop": True},
        ]
    }
    assert [row["id"] for row in sharpapi_source.normalize_events(payload)] == ["match"]
    odds = sharpapi_source.normalize_odds(
        {"data": [
            {"event_id": "future", "market_type": "outright", "selection": "League", "odds_decimal": 5},
            {"event_id": "match", "market_type": "player_goals", "is_player_prop": True, "selection": "Player", "odds_decimal": 4},
        ]}
    )
    assert len(odds) == 2
    assert all(row["is_player_prop"] or row["market_type"] == "outright" for row in odds)


def test_match_detail_uses_sharpapi_rows_when_linked(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    monkeypatch.setenv("SHARPAPI_DISABLED", "true")
    store_sharp_events(
        [{
            "id": "sharp-ui",
            "sport": "soccer",
            "home_team": "Slovan",
            "away_team": "Trnava",
            "start_time": "2026-08-12T17:00:00Z",
        }]
    )
    from mocksite.store import store_sharp_event_link

    store_sharp_event_link("sharp-ui", "m1", 1.0)
    store_sharp_odds(
        [{
            "event_id": "sharp-ui",
            "sportsbook": "draftkings",
            "market_type": "total_goals",
            "selection": "over_2_5",
            "odds_decimal": 2.4,
        }]
    )
    response = create_app().test_client().get("/livescore/match/m1")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "draftkings" in body
    assert "SharpAPI" in body


def test_index_shows_best_sharpapi_1x2_and_dash_when_absent(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    monkeypatch.setenv("SHARPAPI_DISABLED", "true")
    monkeypatch.setenv("MOCK_CLOCK", "demo")
    monkeypatch.setenv("MOCK_FIXTURES", "synthetic")
    from dataclasses import replace
    from mocksite import app as app_module, simulator
    from mocksite.data import FIXTURES

    monkeypatch.setattr(app_module, "_start_refresh_worker", lambda app: None)
    live_state = simulator.state_of("m1")
    upcoming_state = replace(live_state, status=simulator.SCHEDULED)
    monkeypatch.setattr(
        app_module,
        "_rows",
        lambda live_only=False: [
            (FIXTURES[0], live_state),
            (FIXTURES[1], upcoming_state),
        ],
    )
    store_sharp_events([{"id": "sharp-index", "sport": "soccer", "home_team": "Slovan", "away_team": "Trnava"}])
    from mocksite.store import store_sharp_event_link

    store_sharp_event_link("sharp-index", "m1", 1.0)
    store_sharp_odds(
        [
            {"event_id": "sharp-index", "sportsbook": "fanduel", "market_type": "moneyline", "selection": "Home FC", "odds_decimal": 2.1, "market_concept": "1x2", "selection_key": "home"},
            {"event_id": "sharp-index", "sportsbook": "draftkings", "market_type": "moneyline", "selection": "Home FC", "odds_decimal": 2.4, "market_concept": "1x2", "selection_key": "home"},
        ]
    )
    body = create_app().test_client().get("/").get_data(as_text=True)
    assert 'data-field="sharpapi-odds"' in body
    assert "2.4 (draftkings)" in body
    assert 'data-selection="draw">–' in body
    rows = re.findall(r'<tr data-testid="match-row".*?</tr>', body, flags=re.DOTALL)
    assert len(rows) == 2
    assert all(row.count("<td") == 5 for row in rows)
    assert body.count('data-testid="match-link"') == 2

    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "empty.sqlite3"))
    body = create_app().test_client().get("/").get_data(as_text=True)
    assert 'data-selection="home">–' in body


def test_sharp_status_reports_counts_and_unlinked(monkeypatch, capsys):
    monkeypatch.setenv("MOCK_FIXTURES", "synthetic")
    monkeypatch.setenv("SHARPAPI_DISABLED", "true")
    from sandbox_bot.cli import main

    assert main(["sharp", "status"]) == 0
    output = capsys.readouterr().out
    assert "Databáza:" in output
    assert "Zobrazené fixture:" in output


def test_match_detail_skips_refresh_when_rate_budget_is_full(tmp_path, monkeypatch):
    monkeypatch.setenv("MOCK_DB_PATH", str(tmp_path / "sharp.sqlite3"))
    monkeypatch.setenv("SHARPAPI_DISABLED", "false")
    from mocksite import app as app_module

    monkeypatch.setattr(app_module, "_start_refresh_worker", lambda app: None)
    monkeypatch.setattr(app_module, "sharpapi_enabled", lambda: True)
    monkeypatch.setattr(app_module, "sharpapi_request_available", lambda requests=1: False)
    refreshed = []
    monkeypatch.setattr(
        app_module,
        "refresh_for_fixtures",
        lambda fixtures, limit=10: refreshed.append((fixtures, limit)),
    )

    response = create_app().test_client().get("/livescore/match/m1")
    assert response.status_code == 200
    assert refreshed == []
