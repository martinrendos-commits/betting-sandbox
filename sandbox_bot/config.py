"""Configuration for the sandbox bot.

Everything the bot talks to is a local mock service started by
``python -m mocksite.app``. There are no credentials here on purpose: the
sandbox has no accounts, no wallet and no real bookmaker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # --- target URLs (local mock sites only) --------------------------------
    livescore_url: str = "http://127.0.0.1:8000/livescore/"
    bookmaker_url: str = "http://127.0.0.1:8000/book/live"

    # --- browser ------------------------------------------------------------
    headless: bool = False
    slow_mo_ms: int = 120
    viewport: dict[str, int] = field(default_factory=lambda: {"width": 1280, "height": 900})

    # --- model --------------------------------------------------------------
    #: Goal line the whole sandbox is built around.
    goal_line: float = 2.5
    #: How much weight the H2H sample gets against the league prior (0..1).
    h2h_weight: float = 0.65
    #: League-average total goals per match, used as the shrinkage prior.
    league_avg_total_goals: float = 2.7

    # --- value detection ------------------------------------------------------
    #: A selection counts as +EV when the offered odds exceed the fair odds by
    #: at least this fraction (0.10 = 10 %).
    value_threshold: float = 0.10
    #: Flat stake used in the demo, in EUR of play money.
    stake_eur: float = 1.0
    #: Seconds between two polling rounds. Keep it generous: polling a site more
    #: often than a human would is both rude and pointless.
    poll_interval_s: float = 4.0

    # --- output ---------------------------------------------------------------
    screenshot_dir: Path = BASE_DIR / "artifacts"

    def __post_init__(self) -> None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)


SETTINGS = Settings()
