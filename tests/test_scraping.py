"""End-to-end tests driving the real Playwright page objects against the mock site."""

from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from sandbox_bot.analysis import build_pregame_model
from sandbox_bot.odds import evaluate_quote
from sandbox_bot.pages import BookmakerPage, LivescorePage


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture()
def page(browser):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    yield page
    context.close()


def test_scrape_h2h_and_build_model(page, mock_site):
    livescore = LivescorePage(page, f"{mock_site}/livescore/")
    livescore.open_match_list()
    matches = livescore.list_matches()
    assert len(matches) == 4

    livescore.open_match(matches[0]["match_id"])
    history = livescore.read_h2h()
    assert len(history) == 8

    model = build_pregame_model(
        matches[0]["match_id"], matches[0]["home"], matches[0]["away"], history
    )
    assert model.fair_over_odds > 1.0
    assert 0.0 <= model.fair_over_probability <= 1.0


def test_read_live_stats(page, mock_site):
    livescore = LivescorePage(page, f"{mock_site}/livescore/")
    livescore.open_match("m2")
    stats = livescore.read_live_stats()
    assert stats.match_id == "m2"
    assert stats.home and stats.away
    assert 0 <= stats.minute <= 90
    assert stats.total_shots >= 0


def test_quotes_and_value_evaluation(page, mock_site):
    bookmaker = BookmakerPage(page, f"{mock_site}/book/live")
    bookmaker.open()
    quotes = bookmaker.read_quotes()
    assert quotes and len(quotes) % 2 == 0
    signal = evaluate_quote(quotes[0], 2.7)
    assert signal.fair_odds > 1.0


def test_stage_bet_does_not_submit(page, mock_site, tmp_path: Path):
    bookmaker = BookmakerPage(page, f"{mock_site}/book/live")
    bookmaker.open()
    assert bookmaker.slip_is_empty()

    quote = bookmaker.read_quotes()[0]
    shot = bookmaker.stage_bet(quote.match_id, quote.market, 1.0, tmp_path / "staged.png")
    assert shot.exists()
    assert not bookmaker.slip_is_empty()
    assert bookmaker.potential_return() > 0
    # The critical assertion: nothing was submitted while staging.
    assert bookmaker.ticket_status() == ""


def test_confirm_submit_is_explicit(page, mock_site, tmp_path: Path):
    bookmaker = BookmakerPage(page, f"{mock_site}/book/live")
    bookmaker.open()
    quote = bookmaker.read_quotes()[0]
    bookmaker.stage_bet(quote.match_id, quote.market, 1.0, tmp_path / "staged.png")
    status = bookmaker.confirm_submit()
    assert "SANDBOX-0001" in status
