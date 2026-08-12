"""Page object for the mock results portal.

All selectors live in one dictionary at the top of the class. That is the only
place you edit when the markup changes - see README, section "Selektory".
"""

from __future__ import annotations

from datetime import date

from playwright.sync_api import Page

from ..models import H2HResult, LiveStats


class LivescorePage:
    SELECTORS: dict[str, str] = {
        "match_row": '[data-testid="match-row"]',
        "match_link": '[data-testid="match-link"]',
        "live_panel": '[data-testid="live-panel"]',
        "h2h_row": '[data-testid="h2h-row"]',
        "stat_row": '[data-testid="live-stats"] tr[data-stat="{stat}"]',
    }

    def __init__(self, page: Page, base_url: str) -> None:
        self.page = page
        self.base_url = base_url

    # -- navigation ---------------------------------------------------------
    def open_match_list(self) -> None:
        self.page.goto(self.base_url, wait_until="domcontentloaded")
        self.page.wait_for_selector(self.SELECTORS["match_row"])

    def open_match(self, match_id: str) -> None:
        url = f"{self.base_url.rstrip('/')}/match/{match_id}"
        self.page.goto(url, wait_until="domcontentloaded")
        self.page.wait_for_selector(self.SELECTORS["live_panel"])

    def refresh(self) -> None:
        self.page.reload(wait_until="domcontentloaded")

    # -- reading ------------------------------------------------------------
    def list_matches(self) -> list[dict[str, str]]:
        rows = self.page.locator(self.SELECTORS["match_row"])
        matches = []
        for index in range(rows.count()):
            row = rows.nth(index)
            matches.append(
                {
                    "match_id": row.get_attribute("data-match-id") or "",
                    "home": row.locator('[data-field="home"]').inner_text().strip(),
                    "away": row.locator('[data-field="away"]').inner_text().strip(),
                    "kickoff": row.locator('[data-field="kickoff"]').inner_text().strip(),
                }
            )
        return matches

    def read_h2h(self) -> list[H2HResult]:
        rows = self.page.locator(self.SELECTORS["h2h_row"])
        history: list[H2HResult] = []
        for index in range(rows.count()):
            row = rows.nth(index)
            home_goals, away_goals = row.locator('[data-field="score"]').inner_text().split(":")
            history.append(
                H2HResult(
                    played_on=date.fromisoformat(
                        row.locator('[data-field="date"]').inner_text().strip()
                    ),
                    home=row.locator('[data-field="home"]').inner_text().strip(),
                    away=row.locator('[data-field="away"]').inner_text().strip(),
                    home_goals=int(home_goals),
                    away_goals=int(away_goals),
                )
            )
        return history

    def read_live_stats(self) -> LiveStats:
        panel = self.page.locator(self.SELECTORS["live_panel"])
        minute = int(panel.locator('[data-field="minute"]').inner_text().strip().rstrip("'"))
        home_goals, away_goals = panel.locator('[data-field="score"]').inner_text().split(":")
        shots = self._stat_pair("shots")
        corners = self._stat_pair("corners")
        title = self.page.locator('[data-testid="match-title"]').inner_text()
        home, away = (part.strip() for part in title.split("–"))
        return LiveStats(
            match_id=panel.get_attribute("data-match-id") or "",
            home=home,
            away=away,
            minute=minute,
            home_goals=int(home_goals),
            away_goals=int(away_goals),
            home_shots=shots[0],
            away_shots=shots[1],
            home_corners=corners[0],
            away_corners=corners[1],
        )

    def _stat_pair(self, stat: str) -> tuple[int, int]:
        row = self.page.locator(self.SELECTORS["stat_row"].format(stat=stat))
        return (
            int(row.locator('[data-field="home"]').inner_text().strip()),
            int(row.locator('[data-field="away"]').inner_text().strip()),
        )
