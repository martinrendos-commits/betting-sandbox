"""Command line entry point: ``python -m sandbox_bot <command>``."""

from __future__ import annotations

import argparse
import logging

from .config import SETTINGS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sandbox_bot", description="Local betting sandbox bot")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("serve", help="spustí lokálne mock stránky na porte 8000")

    pregame = sub.add_parser("pregame", help="vyscrapuje H2H a vypíše férové kurzy")
    pregame.add_argument("--headless", action="store_true")

    monitor = sub.add_parser("monitor", help="live monitor + hľadanie +EV")
    monitor.add_argument("--headless", action="store_true")
    monitor.add_argument("--rounds", type=int, default=None, help="počet kôl (default: bez limitu)")
    monitor.add_argument("--threshold", type=float, default=SETTINGS.value_threshold)

    backtest = sub.add_parser("backtest", help="offline backtest bez prehliadača")
    backtest.add_argument("--staking", choices=["flat", "kelly"], default="flat")
    backtest.add_argument("--threshold", type=float, default=SETTINGS.value_threshold)
    backtest.add_argument("--bankroll", type=float, default=100.0)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "serve":
        from mocksite.app import create_app

        create_app().run(host="127.0.0.1", port=8000)
        return 0

    if args.command == "pregame":
        from .analysis import build_pregame_model
        from .browser import open_session
        from .pages import LivescorePage

        SETTINGS.headless = args.headless
        with open_session(SETTINGS) as session:
            page = LivescorePage(session.livescore_page, SETTINGS.livescore_url)
            page.open_match_list()
            for match in page.list_matches():
                page.open_match(match["match_id"])
                model = build_pregame_model(
                    match["match_id"], match["home"], match["away"], page.read_h2h()
                )
                print(model.describe(), "\n")
        return 0

    if args.command == "monitor":
        from .monitor import run_monitor

        SETTINGS.headless = args.headless
        SETTINGS.value_threshold = args.threshold
        run_monitor(SETTINGS, max_rounds=args.rounds)
        return 0

    if args.command == "backtest":
        from .backtest import run_backtest

        result = run_backtest(
            bankroll=args.bankroll, threshold=args.threshold, staking=args.staking
        )
        print(result.describe())
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
