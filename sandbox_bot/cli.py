"""Command line entry point: ``python -m sandbox_bot <command>``."""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date, timedelta

from mocksite.env_file import load_env_file
from mocksite.store import connect

from .config import SETTINGS


def _apply_source_env(args: argparse.Namespace) -> None:
    """The mock site reads its fixture source from the environment."""
    os.environ["MOCK_FIXTURES"] = args.fixtures
    os.environ["MOCK_CLOCK"] = args.clock
    if args.league:
        os.environ["MOCK_LEAGUE"] = args.league
    if args.date:
        os.environ["MOCK_DATE"] = args.date


def main(argv: list[str] | None = None) -> int:
    load_env_file()  # before argparse: the defaults below read the environment
    parser = argparse.ArgumentParser(prog="sandbox_bot", description="Local betting sandbox bot")
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_source_args(sub_parser: argparse.ArgumentParser) -> None:
        sub_parser.add_argument(
            "--fixtures",
            default=os.environ.get("MOCK_FIXTURES", "synthetic"),
            help="synthetic | openliga | footballdata | cesta k JSON súboru",
        )
        sub_parser.add_argument(
            "--league",
            default=os.environ.get("MOCK_LEAGUE", ""),
            help="openliga: skratka ligy (bl1); footballdata: league_id alebo časť názvu",
        )
        sub_parser.add_argument("--date", default=os.environ.get("MOCK_DATE"))
        sub_parser.add_argument(
            "--clock",
            choices=["real", "demo"],
            default=os.environ.get("MOCK_CLOCK", "real"),
            help="real = podľa skutočných výkopov, demo = všetko beží hneď zrýchlene",
        )

    serve = sub.add_parser("serve", help="spustí lokálne mock stránky na porte 8000")
    add_source_args(serve)
    serve.add_argument("--port", type=int, default=8000)

    fixtures_cmd = sub.add_parser("fixtures", help="vypíše načítaný rozpis a stav zápasov")
    add_source_args(fixtures_cmd)

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

    backfill = sub.add_parser("backfill", help="stiahne a uloží rozpis do lokálnej DB")
    backfill.add_argument("--fixtures", choices=["footballdata", "openliga"], default="footballdata")
    backfill.add_argument("--days", type=int, default=30)
    backfill.add_argument("--league", default="")

    ratings = sub.add_parser("ratings", help="vypíše Poisson ratingy z uložených výsledkov")
    ratings.add_argument("--league", default="")
    ratings.add_argument("--home-team", default="")
    ratings.add_argument("--away-team", default="")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command in {"serve", "fixtures"}:
        _apply_source_env(args)

    if args.command == "serve":
        try:
            from mocksite.app import create_app
        except RuntimeError as exc:  # missing key, dead API, unusable source
            print(f"\nRozpis sa nepodarilo načítať:\n{exc}\n")
            return 2

        create_app().run(host="127.0.0.1", port=args.port)
        return 0

    if args.command == "fixtures":
        from mocksite import simulator

        try:
            from mocksite.data import FIXTURES
        except RuntimeError as exc:
            print(f"\nRozpis sa nepodarilo načítať:\n{exc}\n")
            return 2

        from mocksite.fixtures_source import footballdata_matchdays, next_matchdays

        if not FIXTURES:
            print("Pre zvolený deň a ligu nie sú žiadne zápasy.")
            upcoming: list[date] = []
            if args.fixtures == "openliga":
                upcoming = next_matchdays(args.league or "bl1", date.today())
            elif args.fixtures == "footballdata":
                upcoming = footballdata_matchdays()
            if upcoming:
                print("Najbližšie hracie dni:", ", ".join(d.isoformat() for d in upcoming))
            return 0
        for fixture in FIXTURES:
            state = simulator.state_of(fixture.match_id)
            print(
                f"{fixture.kickoff}  {fixture.home} – {fixture.away}"
                f"  [{state.status}] {state.minute}'  H2H={len(fixture.h2h)}"
            )
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

    if args.command == "backfill":
        if args.days < 1 or args.days > 365:
            parser.error("--days musí byť v intervale 1 až 365")
        from mocksite.fixtures_source import load_fixtures

        total = 0
        for offset in range(args.days):
            day = date.today() - timedelta(days=offset)
            try:
                fixtures = load_fixtures(args.fixtures, league=args.league or None, on_date=day)
                total += len(fixtures)
                with connect() as connection:
                    stored = connection.execute(
                        "SELECT COUNT(*) FROM matches WHERE source = ?", (args.fixtures,)
                    ).fetchone()[0]
                print(
                    f"{day.isoformat()}: v rozvrhu po filtrovaní {len(fixtures)} zápasov; "
                    f"uložené do DB celkom {stored}"
                )
            except (OSError, RuntimeError, ValueError) as exc:
                print(f"{day.isoformat()}: chyba – {exc}")
        with connect() as connection:
            stored = connection.execute(
                "SELECT COUNT(*) FROM matches WHERE source = ?", (args.fixtures,)
            ).fetchone()[0]
        print(f"Uložené do DB: {stored}; vhodné do rozvrhu spolu: {total}")
        return 0

    if args.command == "ratings":
        from .ratings import load_league_rating
        from .ratings import pair_odds

        if bool(args.home_team) != bool(args.away_team):
            parser.error("--home-team a --away-team treba zadať spolu")

        with connect() as connection:
            league_rows = connection.execute(
                "SELECT league_id, name FROM leagues ORDER BY name"
            ).fetchall()
        selected = [
            row
            for row in league_rows
            if not args.league
            or str(row[0]).lower() == args.league.lower()
            or args.league.lower() in str(row[1]).lower()
        ]
        if not selected:
            print("V databáze nie sú uložené žiadne ligy.")
            return 0
        for league_id, league_name in selected:
            rating = load_league_rating(str(league_id))
            if rating is None:
                continue
            print(f"\n{league_name} ({league_id}) – {rating.matches} dokončených zápasov")
            print(f"Priemer gólov: doma {rating.average_home_goals:.2f}, vonku {rating.average_away_goals:.2f}")
            if rating.has_small_sample:
                print(f"Malá vzorka (< {3} zápasy tímu alebo ligy): použije sa ligový/xG odhad.")
            for team in sorted(rating.teams, key=lambda item: item.home_attack, reverse=True)[:10]:
                print(f"  {team.name}: útok doma {team.home_attack:.2f}, obrana doma {team.home_defence:.2f}, "
                      f"útok vonku {team.away_attack:.2f}, obrana vonku {team.away_defence:.2f}")
            if args.home_team and args.away_team:
                odds = pair_odds(rating, args.home_team, args.away_team)
                if odds is None:
                    print(f"Pár {args.home_team} – {args.away_team}: tím sa v lige nenašiel.")
                else:
                    mode = "fallback ligového/xG odhadu" if odds.used_fallback else "sily tímov"
                    print(
                        f"Pár {args.home_team} – {args.away_team}: {mode}; "
                        f"λ {odds.lambda_home:.2f} – {odds.lambda_away:.2f}; "
                        f"1X2 {odds.home:.2f}/{odds.draw:.2f}/{odds.away:.2f}; "
                        f"O/U 2.5 {odds.over_25:.2f}/{odds.under_25:.2f}"
                    )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
