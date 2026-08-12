"""Playwright browser lifecycle for the sandbox.

Two independent browser contexts are used, mirroring how you would keep a
results portal and a bookmaker side by side:

* context A -> the mock livescore site
* context B -> the mock bookmaker

The browser runs headed by default so you can watch every step. No stealth or
fingerprint-spoofing is applied: the sandbox is our own service and there is
nothing to hide from.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

from .config import SETTINGS, Settings


@dataclass
class BrowserSession:
    browser: Browser
    livescore_page: Page
    bookmaker_page: Page
    _contexts: list[BrowserContext]

    def close(self) -> None:
        for context in self._contexts:
            context.close()
        self.browser.close()


@contextmanager
def open_session(settings: Settings = SETTINGS):
    """Start Playwright with one page per site and clean everything up after."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=settings.headless,
            slow_mo=settings.slow_mo_ms,
        )
        livescore_ctx = browser.new_context(viewport=settings.viewport)
        bookmaker_ctx = browser.new_context(viewport=settings.viewport)
        session = BrowserSession(
            browser=browser,
            livescore_page=livescore_ctx.new_page(),
            bookmaker_page=bookmaker_ctx.new_page(),
            _contexts=[livescore_ctx, bookmaker_ctx],
        )
        try:
            yield session
        finally:
            session.close()
