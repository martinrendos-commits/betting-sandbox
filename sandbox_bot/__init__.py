"""Educational sandbox: scraping, fair-odds modelling and +EV detection.

The bot only ever talks to the local mock sites in :mod:`mocksite`. It contains
no anti-detection code and never submits a bet without an explicit human
confirmation.
"""

__all__ = ["config", "models", "odds", "analysis", "monitor", "backtest"]
