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
    FIXTURES_BY_ID,
    REMOTE_SOURCES,
    refresh_if_stale,
    refresh_live_data,
    refresh_remote_data,
)
from .store import latest_match_events, latest_match_stats

livescore = Blueprint("livescore", __name__, url_prefix="/livescore")
bookmaker = Blueprint("bookmaker", __name__, url_prefix="/book")


def _rows(live_only: bool = False):
    refresh_if_stale()
    rows = [(fixture, simulator.state_of(fixture.match_id)) for fixture in FIXTURES]
    return [row for row in rows if row[1].is_live] if live_only else rows


def _provider_match_id(match_id: str) -> str:
    return match_id[1:] if match_id.startswith("m") and match_id[1:].isdigit() else match_id


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
                "label": str(row["metric"]).replace("_", " ").capitalize(),
                "home": row["home_value"],
                "away": row["away_value"],
                "total": row["total_value"],
            }
            for row in stats
        ],
        "events": events,
    }


def _start_refresh_worker(app: Flask) -> None:
    source = os.environ.get("MOCK_FIXTURES", "synthetic")
    if source not in REMOTE_SOURCES:
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
    stop_event = threading.Event()

    def worker() -> None:
        last_schedule = 0.0
        last_live = 0.0
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
    fixture = FIXTURES_BY_ID[match_id]
    state = simulator.state_of(match_id)
    return render_template(
        "livescore_detail.html",
        fixture=fixture,
        state=state,
        live_details=_live_details(match_id, state),
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
        live_rows = [row for row in rows if row[1].is_live]
        upcoming_rows = [row for row in rows if row[1].status == simulator.SCHEDULED]
        finished_rows = [row for row in rows if row[1].status == simulator.FINISHED]
        finished_rows.sort(key=lambda row: row[0].kickoff_utc, reverse=True)
        return render_template(
            "index.html",
            live_rows=live_rows,
            upcoming_rows=upcoming_rows,
            finished_rows=finished_rows[:20],
            clock_mode=simulator.clock_mode(),
            source=os.environ.get("MOCK_FIXTURES", "synthetic"),
        )

    _start_refresh_worker(app)
    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000, debug=False)
