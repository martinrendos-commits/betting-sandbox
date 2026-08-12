"""Page object for the mock bookmaker.

The submit button is never clicked automatically: :meth:`stage_bet` fills the
slip, hovers the submit button and takes a screenshot, then hands control back
to the caller. Confirmation is a separate, explicit call.
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page

from ..models import MarketQuote


class BookmakerPage:
    SELECTORS: dict[str, str] = {
        "offer_card": '[data-testid="offer-card"]',
        "odds_button": '[data-testid="odds-button"]',
        # A card + market pair; format with match_id and market.
        "odds_button_for": (
            '[data-testid="offer-card"][data-match-id="{match_id}"] '
            '[data-testid="odds-button"][data-market="{market}"]'
        ),
        "slip": '[data-testid="betslip"]',
        "slip_selection": '[data-testid="slip-selection"]',
        "stake_input": '[data-testid="stake-input"]',
        "potential_return": '[data-testid="potential-return"]',
        "submit_button": '[data-testid="submit-ticket"]',
        "ticket_status": '[data-testid="ticket-status"]',
    }

    def __init__(self, page: Page, url: str) -> None:
        self.page = page
        self.url = url

    # -- navigation ---------------------------------------------------------
    def open(self) -> None:
        self.page.goto(self.url, wait_until="domcontentloaded")
        self.page.wait_for_selector(self.SELECTORS["offer_card"])

    def refresh(self) -> None:
        self.page.reload(wait_until="domcontentloaded")

    # -- reading ------------------------------------------------------------
    def read_quotes(self) -> list[MarketQuote]:
        cards = self.page.locator(self.SELECTORS["offer_card"])
        quotes: list[MarketQuote] = []
        for index in range(cards.count()):
            card = cards.nth(index)
            match_id = card.get_attribute("data-match-id") or ""
            event = card.locator('[data-field="event-name"]').inner_text().strip()
            minute = int(card.locator('[data-field="minute"]').inner_text().strip().rstrip("'"))
            buttons = card.locator(self.SELECTORS["odds_button"])
            for btn_index in range(buttons.count()):
                button = buttons.nth(btn_index)
                quotes.append(
                    MarketQuote(
                        match_id=match_id,
                        event_name=event,
                        market=button.get_attribute("data-market") or "",
                        odds=float(button.get_attribute("data-odds") or "0"),
                        minute=minute,
                    )
                )
        return quotes

    def slip_is_empty(self) -> bool:
        return self.page.locator(self.SELECTORS["slip_selection"]).get_attribute("data-empty") == "true"

    def potential_return(self) -> float:
        return float(self.page.locator(self.SELECTORS["potential_return"]).inner_text())

    def ticket_status(self) -> str:
        return self.page.locator(self.SELECTORS["ticket_status"]).inner_text().strip()

    # -- actions ------------------------------------------------------------
    def select_market(self, match_id: str, market: str) -> None:
        selector = self.SELECTORS["odds_button_for"].format(match_id=match_id, market=market)
        button = self.page.locator(selector)
        button.scroll_into_view_if_needed()
        button.hover()
        button.click()
        self.page.wait_for_selector(f'{self.SELECTORS["slip_selection"]}[data-empty="false"]')

    def enter_stake(self, stake: float) -> None:
        field = self.page.locator(self.SELECTORS["stake_input"])
        field.click()
        field.fill("")
        field.type(f"{stake:.2f}", delay=60)

    def stage_bet(self, match_id: str, market: str, stake: float, screenshot_path: Path) -> Path:
        """Put a selection on the slip and stop right before submitting."""
        self.select_market(match_id, market)
        self.enter_stake(stake)
        self.page.locator(self.SELECTORS["submit_button"]).hover()
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(screenshot_path))
        return screenshot_path

    def confirm_submit(self) -> str:
        """Only ever called after an explicit human confirmation."""
        self.page.locator(self.SELECTORS["submit_button"]).click()
        self.page.wait_for_function(
            "el => el.textContent.trim().length > 0",
            arg=self.page.locator(self.SELECTORS["ticket_status"]).element_handle(),
        )
        return self.ticket_status()
