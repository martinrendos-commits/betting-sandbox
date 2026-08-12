"""Live monitor: livescore stats vs. bookmaker prices, with a human in the loop."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime

from .analysis import PregameModel, build_pregame_model
from .browser import BrowserSession, open_session
from .config import SETTINGS, Settings
from .models import LiveStats, ValueSignal
from .odds import bookmaker_margin, evaluate_quote, is_value
from .pacing import sleep_between_polls
from .pages import BookmakerPage, LivescorePage

log = logging.getLogger("sandbox_bot.monitor")


def terminal_confirm(signal: ValueSignal, screenshot: str) -> bool:
    """Default approval gate: a human types 'y' in the terminal."""
    print("\n=== NÁJDENÁ HODNOTA (+EV) ===")
    print(f"  Zápas:        {signal.quote.event_name} ({signal.quote.minute}')")
    print(f"  Trh:          {signal.quote.market}")
    print(f"  Kurz kancelárie: {signal.quote.odds:.2f}")
    print(f"  Náš férový kurz: {signal.fair_odds:.2f}")
    print(f"  Prevaha:      {signal.edge:+.1%}   EV/1 €: {signal.expected_value_per_eur:+.3f} €")
    print(f"  Screenshot:   {screenshot}")
    try:
        answer = input("Podať tiket v sandboxe? [y/N]: ").strip().lower()
    except EOFError:
        # No interactive terminal: never submit without a human saying so.
        print("\nBez interaktívneho terminálu – tiket sa nepodáva.")
        return False
    return answer == "y"


class LiveMonitor:
    def __init__(
        self,
        session: BrowserSession,
        settings: Settings = SETTINGS,
        confirm: Callable[[ValueSignal, str], bool] = terminal_confirm,
    ) -> None:
        self.settings = settings
        self.confirm = confirm
        self.livescore = LivescorePage(session.livescore_page, settings.livescore_url)
        self.bookmaker = BookmakerPage(session.bookmaker_page, settings.bookmaker_url)
        self.pregame: dict[str, PregameModel] = {}

    # -- phase 1: pre-game ---------------------------------------------------
    def build_pregame_models(self, live_only: bool = False) -> dict[str, PregameModel]:
        self.livescore.open_match_list(live_only=live_only)
        matches = self.livescore.list_matches()
        if not matches:
            log.warning(
                "Portál nevrátil žiadne zápasy. Skontroluj MOCK_FIXTURES / MOCK_DATE / MOCK_LEAGUE."
            )
        for match in matches:
            self._model_for(match["match_id"])
            sleep_between_polls(1.0)
        return self.pregame

    def _model_for(self, match_id: str) -> PregameModel:
        """Build (and cache) the pre-game model for one match.

        Done lazily so matches that kick off later in the day are picked up as
        soon as they appear in the live offer.
        """
        cached = self.pregame.get(match_id)
        if cached is not None:
            return cached
        self.livescore.open_match(match_id)
        stats = self.livescore.read_live_stats()
        model = build_pregame_model(match_id, stats.home, stats.away, self.livescore.read_h2h())
        self.pregame[match_id] = model
        log.info("Pregame model:\n%s", model.describe())
        return model

    # -- phase 2: live -------------------------------------------------------
    def poll_once(self) -> list[ValueSignal]:
        self.bookmaker.refresh()
        quotes = self.bookmaker.read_quotes()
        signals: list[ValueSignal] = []
        stats_cache: dict[str, LiveStats] = {}

        for quote in quotes:
            model = self._model_for(quote.match_id)
            if quote.match_id not in stats_cache:
                self.livescore.open_match(quote.match_id)
                stats_cache[quote.match_id] = self.livescore.read_live_stats()
            stats = stats_cache[quote.match_id]
            if not stats.is_live:
                # The portal says the match is not being played right now.
                continue
            signals.append(evaluate_quote(quote, model.expected_goals, stats))

        for match_id in {q.match_id for q in quotes}:
            pair = [q for q in quotes if q.match_id == match_id]
            if len(pair) == 2:
                over = next(q for q in pair if q.market.startswith("over"))
                under = next(q for q in pair if q.market.startswith("under"))
                log.debug(
                    "%s margin=%.1f%%", match_id, 100 * bookmaker_margin(over.odds, under.odds)
                )
        return signals

    def run(self, max_rounds: int | None = None) -> None:
        self.bookmaker.open()
        rounds = 0
        while max_rounds is None or rounds < max_rounds:
            rounds += 1
            signals = self.poll_once()
            best = max(signals, key=lambda s: s.edge, default=None)
            if best is None:
                log.info("kolo %s: v ponuke nie je žiadny live zápas", rounds)
            else:
                log.info(
                    "kolo %s: najlepšia prevaha %.1f%% (%s %s @ %.2f, fér %.2f)",
                    rounds,
                    100 * best.edge,
                    best.quote.event_name,
                    best.quote.market,
                    best.quote.odds,
                    best.fair_odds,
                )
            for signal in sorted(signals, key=lambda s: s.edge, reverse=True):
                if is_value(signal, self.settings.value_threshold):
                    self.handle_value(signal)
                    break
            sleep_between_polls(self.settings.poll_interval_s)

    # -- phase 3: staged bet, never auto-submitted ---------------------------
    def handle_value(self, signal: ValueSignal) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        path = self.settings.screenshot_dir / f"staged-{signal.quote.match_id}-{stamp}.png"
        self.bookmaker.stage_bet(
            signal.quote.match_id, signal.quote.market, self.settings.stake_eur, path
        )
        if self.confirm(signal, str(path)):
            status = self.bookmaker.confirm_submit()
            log.info("Tiket podaný v sandboxe: %s", status)
        else:
            log.info("Tiket zamietnutý používateľom, nič sa nepodalo.")


def run_monitor(settings: Settings = SETTINGS, max_rounds: int | None = None) -> None:
    with open_session(settings) as session:
        LiveMonitor(session, settings).run(max_rounds=max_rounds)
