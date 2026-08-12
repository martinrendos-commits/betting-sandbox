"""Two mock websites served by a single local Flask app.

* ``/livescore`` – a results portal with H2H history and live statistics.
* ``/book``      – a bookmaker with a live offer, a bet slip and a submit flow.

Both are intentionally structured like real sites (tables, cards, a slip panel)
so the Playwright page objects in ``sandbox_bot`` have something realistic to
target, without touching anybody else's service.
"""

from __future__ import annotations

import os
import threading
import time

from flask import Blueprint, Flask, jsonify, render_template, request

from . import simulator
from .data import (
    FIXTURES,
    PROVIDER_LIVE_FIXTURES,
    PROVIDER_LIVE_PAYLOADS,
    PROVIDER_UPCOMING_FIXTURES,
    REMOTE_SOURCES,
    fixture_for_id,
    refresh_if_stale,
    refresh_live_data,
    refresh_remote_data,
)
from .store import latest_match_events, latest_match_stats, latest_sharp_odds, sharp_event_id_for_match

livescore = Blueprint("livescore", __name__, url_prefix="/livescore")
bookmaker = Blueprint("bookmaker", __name__, url_prefix="/book")


def _rows(live_only: bool = False):
    refresh_if_stale()
    rows_by_id = {
        fixture.match_id: (fixture, simulator.state_of(fixture.match_id))
        for fixture in FIXTURES
    }
    for match_id, fixture in PROVIDER_UPCOMING_FIXTURES.items():
        simulator.ensure_timeline(fixture)
        rows_by_id[match_id] = (fixture, simulator.state_of(match_id))
    provider_rows = []
    for match_id, fixture in PROVIDER_LIVE_FIXTURES.items():
        provider_rows.append((fixture, _provider_state(match_id, simulator.state_of(match_id))))
        rows_by_id[match_id] = provider_rows[-1]
    if live_only:
        if os.environ.get("MOCK_FIXTURES", "synthetic") == "footballdata":
            return provider_rows
        return [row for row in rows_by_id.values() if row[1].is_live]
    return list(rows_by_id.values())


def _provider_match_id(match_id: str) -> str:
    return match_id[1:] if match_id.startswith("m") and match_id[1:].isdigit() else match_id


def _provider_state(match_id: str, state):
    payload = PROVIDER_LIVE_PAYLOADS.get(match_id) or {}
    score = payload.get("score") or {}
    minute = payload.get("minute")
    if minute is None:
        minute = payload.get("match_minute") or payload.get("status_minute")
    try:
        provider_minute = int(minute) if minute is not None else state.minute
    except (TypeError, ValueError):
        provider_minute = state.minute
    home_goals = score.get("home") if score.get("home") is not None else state.home_goals
    away_goals = score.get("away") if score.get("away") is not None else state.away_goals
    return simulator.MinuteState(
        minute=provider_minute,
        home_goals=int(home_goals),
        away_goals=int(away_goals),
        home_shots=state.home_shots,
        away_shots=state.away_shots,
        home_corners=state.home_corners,
        away_corners=state.away_corners,
        over25_odds=state.over25_odds,
        under25_odds=state.under25_odds,
        settled=state.settled,
        status=simulator.LIVE,
    )


STAT_LABELS = {
    "xg": "xG",
    "shots": "Strely",
    "shots_on_target": "Strely na bránu",
    "shots_off_target": "Strely mimo bránu",
    "corners": "Rohy",
    "offsides": "Ofsajdy",
    "cards": "Karty",
    "yellow_cards": "Žlté karty",
    "red_cards": "Červené karty",
    "possession": "Držanie lopty",
    "attacks": "Útoky",
    "dangerous_attacks": "Nebezpečné útoky",
    "fouls": "Fauly",
    "throwins": "Vhadzovania",
    "free_kicks": "Voľné kopy",
    "goal_kicks": "Odkopy od brány",
    "penalties_won": "Vybojované penalty",
    "penalty_goals": "Góly z penalty",
    "penalty_missed": "Nepremenené penalty",
    "half_time_goals": "Góly v polčase",
    "second_half_goals": "Góly v druhom polčase",
    "xg_prematch": "Prematch xG",
}


def _stat_label(metric: str) -> str:
    return STAT_LABELS.get(metric, metric.replace("_", " ").capitalize())


def _live_details(match_id: str, state):
    stats = latest_match_stats(_provider_match_id(match_id))
    events = latest_match_events(_provider_match_id(match_id))
    if not stats:
        return {
            "source": "simulated",
            "stats": [
                {"metric": "shots", "label": "Strely", "home": state.home_shots, "away": state.away_shots, "total": None},
                {"metric": "corners", "label": "Rohy", "home": state.home_corners, "away": state.away_corners, "total": None},
            ],
            "events": [],
        }
    return {
        "source": "footballdata",
        "stats": [
            {
                "metric": row["metric"],
                "label": _stat_label(str(row["metric"])),
                "home": row["home_value"],
                "away": row["away_value"],
                "total": row["total_value"],
            }
            for row in stats
        ],
        "events": events,
    }


def _sharp_odds_details(match_id: str, fixture):
    event_id = sharp_event_id_for_match(match_id)
    odds = latest_sharp_odds(event_id) if event_id else []
    if not odds:
        return {"source": "footballdata", "rows": [], "event_id": None}
    return {"source": "sharpapi", "rows": odds, "event_id": event_id}


def _local_fair_odds(fixture) -> list[dict]:
    import math

    lam_home = max((fixture.home_attack + fixture.away_defence) / 2, 0.1)
    lam_away = max((fixture.away_attack + fixture.home_defence) / 2, 0.1)
    under = 0.0
    for home in range(10):
        for away in range(10):
            probability = (
                math.exp(-lam_home) * lam_home**home / math.factorial(home)
                * math.exp(-lam_away) * lam_away**away / math.factorial(away)
            )
            if home + away <= 2:
                under += probability
    return [
        {"market": "total_goals", "selection": "over_2_5", "odds": round(1 / max(1 - under, 0.001), 3)},
        {"market": "total_goals", "selection": "under_2_5", "odds": round(1 / max(under, 0.001), 3)},
    ]


def _start_refresh_worker(app: Flask) -> None:
    source = os.environ.get("MOCK_FIXTURES", "synthetic")
    from .sharpapi_source import sharpapi_enabled

    if source not in REMOTE_SOURCES and not sharpapi_enabled():
        return
    reloader_main = os.environ.get("WERKZEUG_RUN_MAIN")
    if reloader_main is not None and reloader_main != "true":
        return
    if reloader_main is None and (app.debug or os.environ.get("FLASK_DEBUG") == "1"):
        return
    try:
        schedule_interval = float(os.environ.get("MOCK_REFRESH_S", "600"))
    except ValueError:
        schedule_interval = 600.0
    try:
        live_interval = float(os.environ.get("MOCK_LIVE_REFRESH_S", "60"))
    except ValueError:
        live_interval = 60.0
    try:
        sharp_interval = float(os.environ.get("SHARPAPI_REFRESH_S", "60"))
    except ValueError:
        sharp_interval = 60.0
    stop_event = threading.Event()

    def worker() -> None:
        last_schedule = 0.0
        last_live = 0.0
        last_sharp = 0.0
        sharp_logged = False
        while not stop_event.is_set():
            now = time.time()
            if schedule_interval > 0 and now - last_schedule >= schedule_interval:
                try:
                    refresh_remote_data()
                except Exception as exc:
                    app.logger.warning("Obnovenie rozpisu workerom zlyhalo: %s", exc)
                last_schedule = now
            if live_interval > 0 and source == "footballdata" and now - last_live >= live_interval:
                try:
                    refresh_live_data()
                except Exception as exc:
                    app.logger.warning("Obnovenie live štatistík workerom zlyhalo: %s", exc)
                last_live = now
            if sharp_interval > 0 and now - last_sharp >= sharp_interval:
                try:
                    from .sharpapi_source import SharpAPIError, refresh_for_fixtures, sharpapi_enabled, sharpapi_status

                    if sharpapi_enabled():
                        refresh_for_fixtures(FIXTURES + list(PROVIDER_UPCOMING_FIXTURES.values()))
                    elif not sharp_logged:
                        app.logger.info(sharpapi_status())
                        sharp_logged = True
                except SharpAPIError as exc:
                    app.logger.warning("Obnovenie SharpAPI zlyhalo: %s", exc)
                except Exception as exc:
                    app.logger.warning("Obnovenie SharpAPI zlyhalo: %s", exc)
                last_sharp = now
            stop_event.wait(1.0)

    thread = threading.Thread(target=worker, name="mocksite-refresh", daemon=True)
    thread.start()
    app.extensions["mocksite_refresh_stop"] = stop_event
    app.extensions["mocksite_refresh_thread"] = thread


@livescore.get("/")
def match_list():
    return render_template(
        "livescore_list.html",
        rows=_rows(live_only=request.args.get("live") == "1"),
        live_filter=request.args.get("live") == "1",
    )


@livescore.get("/match/<match_id>")
def match_detail(match_id: str):
    fixture = fixture_for_id(match_id)
    state = simulator.state_of(match_id)
    if match_id in PROVIDER_LIVE_FIXTURES:
        state = _provider_state(match_id, state)
    return render_template(
        "livescore_detail.html",
        fixture=fixture,
        state=state,
        live_details=_live_details(match_id, state),
        sharp_odds=_sharp_odds_details(match_id, fixture),
        local_fair_odds=_local_fair_odds(fixture),
    )


@bookmaker.get("/")
def bookmaker_home():
    return render_template("bookmaker_home.html")


@bookmaker.get("/live")
def live_offer():
    """Only matches that are actually in play appear in a live offer."""
    return render_template("bookmaker_live.html", rows=_rows(live_only=True))


@bookmaker.get("/api/odds")
def odds_api():
    """Convenience endpoint for tests and the backtest, not used by the scraper."""
    return jsonify(
        [
            {
                "match_id": fixture.match_id,
                "home": fixture.home,
                "away": fixture.away,
                "status": state.status,
                "minute": state.minute,
                "over_2_5": state.over25_odds,
                "under_2_5": state.under25_odds,
            }
            for fixture, state in _rows()
        ]
    )


@bookmaker.post("/api/betslip")
def submit_betslip():
    payload = request.get_json(force=True)
    return jsonify(
        {
            "status": "accepted",
            "ticket_id": "SANDBOX-0001",
            "selection": payload.get("selection"),
            "stake": payload.get("stake"),
        }
    )


def create_app() -> Flask:
    app = Flask(__name__)
    app.register_blueprint(livescore)
    app.register_blueprint(bookmaker)

    @app.get("/")
    def index():
        rows = _rows()
        if os.environ.get("MOCK_FIXTURES", "synthetic") == "footballdata":
            live_rows = [row for row in rows if row[0].match_id in PROVIDER_LIVE_FIXTURES]
        else:
            live_rows = [row for row in rows if row[1].is_live]
        upcoming_rows = [row for row in rows if row[1].status == simulator.SCHEDULED]
        upcoming_rows.sort(key=lambda row: row[0].kickoff_utc)
        return render_template(
            "index.html",
            live_rows=live_rows,
            upcoming_rows=upcoming_rows[:50],
            clock_mode=simulator.clock_mode(),
            source=os.environ.get("MOCK_FIXTURES", "synthetic"),
        )

    _start_refresh_worker(app)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000, debug=False)
