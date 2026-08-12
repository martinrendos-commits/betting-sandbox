"""Two mock websites served by a single local Flask app.

* ``/livescore`` – a results portal with H2H history and live statistics.
* ``/book``      – a bookmaker with a live offer, a bet slip and a submit flow.

Both are intentionally structured like real sites (tables, cards, a slip panel)
so the Playwright page objects in ``sandbox_bot`` have something realistic to
target, without touching anybody else's service.
"""

from __future__ import annotations

import os

from flask import Blueprint, Flask, jsonify, render_template, request

from . import simulator
from .data import FIXTURES, FIXTURES_BY_ID

livescore = Blueprint("livescore", __name__, url_prefix="/livescore")
bookmaker = Blueprint("bookmaker", __name__, url_prefix="/book")


def _rows(live_only: bool = False):
    rows = [(fixture, simulator.state_of(fixture.match_id)) for fixture in FIXTURES]
    return [row for row in rows if row[1].is_live] if live_only else rows


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
    return render_template(
        "livescore_detail.html",
        fixture=fixture,
        state=simulator.state_of(match_id),
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
        return render_template(
            "index.html",
            rows=_rows(),
            clock_mode=simulator.clock_mode(),
            source=os.environ.get("MOCK_FIXTURES", "synthetic"),
        )

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=8000, debug=False)
