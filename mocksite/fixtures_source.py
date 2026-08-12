"""Where the sandbox gets its fixture list from.

Four interchangeable providers:

* ``synthetic``    – the original generated fixtures, always available offline;
* ``openliga``     – the real schedule from OpenLigaDB, a free open-data API that
  needs no key and whose data is explicitly published for reuse;
* ``footballdata`` – the real schedule from footballdata.io (needs an API key in
  ``FOOTBALLDATA_API_KEY``); covers 1200+ leagues worldwide;
* ``file``         – your own JSON file, so you can hand-write any fixture list.

Only the *schedule, past results and team names* are real. Live minute-by-minute
statistics and the bookmaker prices are still simulated locally: nothing in this
project scrapes a live results portal or a bookmaker.
"""

from __future__ import annotations

import json
import logging
import os
import ssl
import urllib.error
import urllib.request
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import certifi

from .env_file import ENV_FILE, load_env_file

log = logging.getLogger("mocksite.fixtures")

CACHE_DIR = Path(os.environ.get("MOCK_CACHE_DIR", Path(__file__).resolve().parent.parent / ".cache"))
OPENLIGA_BASE = "https://api.openligadb.de"
FOOTBALLDATA_BASE = "https://footballdata.io/api/v1"
HTTP_TIMEOUT_S = 20
#: Plain, honest identification of this client – no browser spoofing.
USER_AGENT = "betting-sandbox/1.0 (educational local sandbox)"
CA_BUNDLE_ENV = "FOOTBALLDATA_CA_BUNDLE"
#: How many past seasons are pulled in to build H2H history and team strengths.
HISTORY_SEASONS = 3
LEAGUE_AVG_GOALS_PER_TEAM = 1.45


@dataclass(frozen=True)
class H2HMatch:
    played_on: date
    home: str
    away: str
    home_goals: int
    away_goals: int

    @property
    def total_goals(self) -> int:
        return self.home_goals + self.away_goals


@dataclass
class Fixture:
    match_id: str
    home: str
    away: str
    kickoff_utc: datetime
    competition: str = ""
    #: Status reported by the data provider (``""`` when it does not report one).
    #: Only used to keep already finished or cancelled matches out of the live
    #: offer; the running clock still decides when a match goes live.
    provider_status: str = ""
    home_attack: float = LEAGUE_AVG_GOALS_PER_TEAM
    home_defence: float = LEAGUE_AVG_GOALS_PER_TEAM
    away_attack: float = LEAGUE_AVG_GOALS_PER_TEAM
    away_defence: float = LEAGUE_AVG_GOALS_PER_TEAM
    h2h: list[H2HMatch] = field(default_factory=list)

    @property
    def kickoff(self) -> str:
        return self.kickoff_utc.astimezone().strftime("%H:%M")


# ---------------------------------------------------------------------------
# HTTP with an on-disk cache (one request per league-season per day at most)
# ---------------------------------------------------------------------------
def _cached_get_json(
    url: str,
    cache_key: str,
    max_age_s: float = 3600.0,
    headers: dict[str, str] | None = None,
):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{cache_key}.json"
    if cache_file.exists():
        age = datetime.now().timestamp() - cache_file.stat().st_mtime
        if age < max_age_s:
            return json.loads(cache_file.read_text(encoding="utf-8"))

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": USER_AGENT, **(headers or {})},
    )
    ca_bundle = os.environ.get(CA_BUNDLE_ENV, "").strip()
    context = ssl.create_default_context(cafile=ca_bundle or certifi.where())
    try:
        with urllib.request.urlopen(
            request,
            timeout=HTTP_TIMEOUT_S,
            context=context,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        # HTTPError must be handled before URLError: it is a subclass of it.
        body = error.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            # Not the API talking – usually a proxy or WAF in the way.
            raise RuntimeError(
                f"{url} odpovedalo HTTP {error.code} ({error.reason}) a odpoveď nie je "
                f"JSON od API – pravdepodobne ju zablokoval proxy/firewall. "
                f"Začiatok odpovede: {body[:200]!r}"
            ) from error
        if payload.get("success") is not False:
            raise
        return payload  # deliberately not cached
    except urllib.error.URLError as error:
        if "CERTIFICATE_VERIFY_FAILED" in str(error.reason):
            raise RuntimeError(
                "SSL certifikát footballdata.io sa nedá overiť. "
                "Aktualizuj Python certifikáty alebo nastav "
                f"{CA_BUNDLE_ENV} na PEM certifikát firemného proxy."
            ) from error
        raise
    cache_file.write_text(json.dumps(payload), encoding="utf-8")
    return payload


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------
def _parse_utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _final_score(match: dict) -> tuple[int, int] | None:
    if not match.get("matchIsFinished"):
        return None
    results = match.get("matchResults") or []
    # resultTypeID 2 = final score, 1 = half time. Fall back to the last entry.
    final = next((r for r in results if r.get("resultTypeID") == 2), results[-1] if results else None)
    if not final:
        return None
    return int(final["pointsTeam1"]), int(final["pointsTeam2"])


def load_openligadb(league: str, on_date: date, seasons_back: int = HISTORY_SEASONS) -> list[Fixture]:
    """Real schedule + real past results for one league from OpenLigaDB."""
    seasons = [on_date.year - offset for offset in range(seasons_back + 1)]
    all_matches: list[dict] = []
    for season in seasons:
        try:
            payload = _cached_get_json(
                f"{OPENLIGA_BASE}/getmatchdata/{league}/{season}",
                cache_key=f"openligadb-{league}-{season}",
                max_age_s=3600 if season == seasons[0] else 7 * 24 * 3600,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            log.warning("OpenLigaDB %s/%s nedostupné: %s", league, season, exc)
            continue
        if isinstance(payload, list):
            all_matches.extend(payload)

    if not all_matches:
        raise RuntimeError(f"OpenLigaDB nevrátilo žiadne zápasy pre ligu {league!r}")

    finished: list[tuple[datetime, str, str, int, int]] = []
    for match in all_matches:
        score = _final_score(match)
        if score is None:
            continue
        finished.append(
            (
                _parse_utc(match["matchDateTimeUTC"]),
                match["team1"]["teamName"],
                match["team2"]["teamName"],
                score[0],
                score[1],
            )
        )

    scored: dict[str, list[int]] = defaultdict(list)
    conceded: dict[str, list[int]] = defaultdict(list)
    for _, home, away, home_goals, away_goals in finished:
        scored[home].append(home_goals)
        conceded[home].append(away_goals)
        scored[away].append(away_goals)
        conceded[away].append(home_goals)

    def strength(team: str) -> tuple[float, float]:
        goals_for = scored.get(team) or [LEAGUE_AVG_GOALS_PER_TEAM]
        goals_against = conceded.get(team) or [LEAGUE_AVG_GOALS_PER_TEAM]
        return sum(goals_for) / len(goals_for), sum(goals_against) / len(goals_against)

    fixtures: list[Fixture] = []
    for match in all_matches:
        kickoff = _parse_utc(match["matchDateTimeUTC"])
        if kickoff.astimezone().date() != on_date:
            continue
        home = match["team1"]["teamName"]
        away = match["team2"]["teamName"]
        home_attack, home_defence = strength(home)
        away_attack, away_defence = strength(away)
        h2h = [
            H2HMatch(played_on=played.date(), home=h, away=a, home_goals=hg, away_goals=ag)
            for played, h, a, hg, ag in sorted(finished, reverse=True)
            if {h, a} == {home, away}
        ][:10]
        fixtures.append(
            Fixture(
                match_id=f"m{match['matchID']}",
                home=home,
                away=away,
                kickoff_utc=kickoff,
                competition=match.get("leagueName", league),
                home_attack=home_attack,
                home_defence=home_defence,
                away_attack=away_attack,
                away_defence=away_defence,
                h2h=h2h,
            )
        )
    return sorted(fixtures, key=lambda f: f.kickoff_utc)


# ---------------------------------------------------------------------------
# footballdata.io
# ---------------------------------------------------------------------------
#: Statuses that mean the match will not be played (again) today.
FD_DEAD_STATUSES = frozenset({"complete", "canceled", "cancelled", "postponed", "abandoned"})


def footballdata_key() -> str:
    key = os.environ.get("FOOTBALLDATA_API_KEY", "").strip()
    if not key:
        load_env_file()
        key = os.environ.get("FOOTBALLDATA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "Chýba FOOTBALLDATA_API_KEY. Buď ho nastav v aktuálnom okne\n"
            '  PowerShell:  $env:FOOTBALLDATA_API_KEY = "fd_..."\n'
            "  bash:        export FOOTBALLDATA_API_KEY=fd_...\n"
            f"alebo ho zapíš do súboru {ENV_FILE} ako riadok\n"
            "  FOOTBALLDATA_API_KEY=fd_..."
        )
    return key


def _footballdata_get(path: str, cache_key: str, max_age_s: float) -> dict:
    payload = _cached_get_json(
        f"{FOOTBALLDATA_BASE}/{path}",
        cache_key=cache_key,
        max_age_s=max_age_s,
        headers={"Authorization": f"Bearer {footballdata_key()}"},
    )
    if not payload.get("success"):
        error = payload.get("error") or {}
        raise RuntimeError(f"footballdata.io: {error.get('message', 'neznáma chyba')}")
    return payload["data"]


def _footballdata_h2h(home_id: int, away_id: int, home: str, away: str) -> list[H2HMatch]:
    """Real head-to-head results for one pairing (one request, cached for a day)."""
    try:
        data = _footballdata_get(
            f"teams/{home_id}/h2h/{away_id}?limit=10",
            cache_key=f"footballdata-h2h-{home_id}-{away_id}",
            max_age_s=24 * 3600,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        log.warning("footballdata.io H2H %s vs %s nedostupné: %s", home, away, exc)
        return []

    h2h: list[H2HMatch] = []
    for match in data.get("matches", []):
        score = match.get("score") or {}
        if match.get("status") != "complete" or score.get("home") is None:
            continue
        h2h.append(
            H2HMatch(
                played_on=datetime.strptime(match["match_date"], "%Y-%m-%d %H:%M:%S").date(),
                home=match["home_team"]["team_name"],
                away=match["away_team"]["team_name"],
                home_goals=int(score["home"]),
                away_goals=int(score["away"]),
            )
        )
    return h2h


def _fixture_from_footballdata(match: dict, with_h2h: bool) -> Fixture:
    home_team, away_team = match["home_team"], match["away_team"]
    home, away = home_team["team_name"], away_team["team_name"]
    # Pre-match xG is the provider's own expected-goals estimate, which is exactly
    # the lambda the local Poisson simulator needs.
    prematch = (match.get("xg") or {}).get("prematch") or {}
    lam_home = float(prematch.get("home") or LEAGUE_AVG_GOALS_PER_TEAM)
    lam_away = float(prematch.get("away") or LEAGUE_AVG_GOALS_PER_TEAM)
    h2h = (
        _footballdata_h2h(home_team["team_id"], away_team["team_id"], home, away)
        if with_h2h
        else []
    )
    return Fixture(
        match_id=f"m{match['match_id']}",
        home=home,
        away=away,
        kickoff_utc=datetime.fromtimestamp(int(match["date_unix"]), tz=timezone.utc),
        competition=(match.get("league") or {}).get("name", ""),
        provider_status=str(match.get("status", "")),
        # simulator: lam_home = (home_attack + away_defence) / 2, and vice versa.
        home_attack=lam_home,
        away_defence=lam_home,
        away_attack=lam_away,
        home_defence=lam_away,
        h2h=h2h,
    )


def load_footballdata(
    on_date: date, league: str | None = None, max_fixtures: int = 20
) -> list[Fixture]:
    """Real schedule for one day from footballdata.io.

    ``league`` filters by numeric ``league_id`` or by a case-insensitive part of
    the league name (e.g. ``"Premier League"``). H2H history is fetched per
    fixture, so the fixture count is capped to keep the request budget sane.
    """
    today = date.today()
    path = "fixtures/today?limit=100" if on_date == today else f"matches/date/{on_date}?limit=100"
    data = _footballdata_get(
        path,
        cache_key=f"footballdata-day-{on_date}",
        # Today's list carries the live status, so keep it fresh.
        max_age_s=120 if on_date == today else 12 * 3600,
    )

    matches = data.get("matches", [])
    if league:
        wanted = league.strip().lower()
        matches = [
            match
            for match in matches
            if wanted in {str((match.get("league") or {}).get("league_id"))}
            or wanted in str((match.get("league") or {}).get("name", "")).lower()
        ]
    matches = [m for m in matches if str(m.get("status", "")) not in FD_DEAD_STATUSES]
    matches.sort(key=lambda match: int(match["date_unix"]))

    return [
        _fixture_from_footballdata(match, with_h2h=index < max_fixtures)
        for index, match in enumerate(matches)
    ]


def footballdata_matchdays(limit: int = 10) -> list[date]:
    """Days with upcoming fixtures, useful when today's list is empty."""
    try:
        data = _footballdata_get(
            "fixtures/upcoming?limit=100",
            cache_key="footballdata-upcoming",
            max_age_s=3600,
        )
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
        log.warning("footballdata.io upcoming nedostupné: %s", exc)
        return []
    days = {
        datetime.fromtimestamp(int(match["date_unix"]), tz=timezone.utc).astimezone().date()
        for match in data.get("matches", [])
    }
    return sorted(days)[:limit]


def load_json_file(path: Path) -> list[Fixture]:
    """Fixtures from your own JSON file.

    ``[{"match_id": "m1", "home": "A", "away": "B", "kickoff_utc": "2026-08-12T17:00:00Z",
        "h2h": [{"played_on": "2025-04-01", "home": "A", "away": "B",
                 "home_goals": 2, "away_goals": 1}]}]``
    """
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    fixtures: list[Fixture] = []
    for index, item in enumerate(payload):
        h2h = [
            H2HMatch(
                played_on=date.fromisoformat(entry["played_on"]),
                home=entry["home"],
                away=entry["away"],
                home_goals=int(entry["home_goals"]),
                away_goals=int(entry["away_goals"]),
            )
            for entry in item.get("h2h", [])
        ]
        goals = [m.total_goals for m in h2h] or [2 * LEAGUE_AVG_GOALS_PER_TEAM]
        half = sum(goals) / len(goals) / 2
        fixtures.append(
            Fixture(
                match_id=str(item.get("match_id") or f"m{index + 1}"),
                home=item["home"],
                away=item["away"],
                kickoff_utc=_parse_utc(item["kickoff_utc"]),
                competition=item.get("competition", ""),
                home_attack=float(item.get("home_attack", half)),
                home_defence=float(item.get("home_defence", half)),
                away_attack=float(item.get("away_attack", half)),
                away_defence=float(item.get("away_defence", half)),
                h2h=h2h,
            )
        )
    return sorted(fixtures, key=lambda f: f.kickoff_utc)


def load_synthetic(on_date: date | None = None) -> list[Fixture]:
    """Offline fallback: made-up teams with a generated H2H history."""
    import random

    from .data import SYNTHETIC_TEAMS, poisson_sample

    rng = random.Random(20240817)
    today = on_date or date.today()
    fixtures: list[Fixture] = []
    for index, (home_idx, away_idx) in enumerate([(0, 4), (1, 5), (2, 6), (3, 7)]):
        home, home_att, home_def = SYNTHETIC_TEAMS[home_idx]
        away, away_att, away_def = SYNTHETIC_TEAMS[away_idx]
        kickoff = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc) + timedelta(
            hours=17 + index
        )
        h2h = []
        for back in range(1, 9):
            h2h.append(
                H2HMatch(
                    played_on=today - timedelta(days=back * 97),
                    home=home if back % 2 else away,
                    away=away if back % 2 else home,
                    home_goals=poisson_sample(rng, (home_att + away_def) / 2),
                    away_goals=poisson_sample(rng, (away_att + home_def) / 2),
                )
            )
        fixtures.append(
            Fixture(
                match_id=f"m{index + 1}",
                home=home,
                away=away,
                kickoff_utc=kickoff,
                competition="Sandbox liga",
                home_attack=home_att,
                home_defence=home_def,
                away_attack=away_att,
                away_defence=away_def,
                h2h=h2h,
            )
        )
    return fixtures


def load_fixtures(
    source: str | None = None,
    *,
    league: str | None = None,
    on_date: date | None = None,
) -> list[Fixture]:
    """Pick a provider from the arguments or the environment.

    ``MOCK_FIXTURES=synthetic|openliga|footballdata|/path/to/file.json``
    ``MOCK_LEAGUE=bl1`` ``MOCK_DATE=2026-08-29``
    """
    source = source or os.environ.get("MOCK_FIXTURES", "synthetic")
    if on_date is None:
        raw_date = os.environ.get("MOCK_DATE")
        on_date = date.fromisoformat(raw_date) if raw_date else date.today()

    if source == "synthetic":
        return load_synthetic(on_date)
    if source == "footballdata":
        # No league filter by default: footballdata.io covers 1200+ competitions.
        wanted = league if league is not None else os.environ.get("MOCK_LEAGUE", "")
        fixtures = load_footballdata(on_date, wanted)
        if not fixtures:
            log.warning(
                "footballdata.io: na %s%s nie sú žiadne (ešte nehrané) zápasy.",
                on_date,
                f" v lige {wanted!r}" if wanted else "",
            )
        return fixtures

    league = league or os.environ.get("MOCK_LEAGUE", "bl1")
    if source == "openliga":
        fixtures = load_openligadb(league, on_date)
        if not fixtures:
            log.warning(
                "Na %s sa v lige %s nehrá nič – použi MOCK_DATE=YYYY-MM-DD alebo inú ligu.",
                on_date,
                league,
            )
        return fixtures
    return load_json_file(Path(source))


def next_matchdays(league: str, from_date: date, limit: int = 10) -> list[date]:
    """Days with scheduled matches, useful when 'today' is empty."""
    seasons = [from_date.year, from_date.year - 1]
    days: set[date] = set()
    for season in seasons:
        try:
            payload = _cached_get_json(
                f"{OPENLIGA_BASE}/getmatchdata/{league}/{season}",
                cache_key=f"openligadb-{league}-{season}",
                max_age_s=3600,
            )
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            continue
        for match in payload if isinstance(payload, list) else []:
            kickoff = _parse_utc(match["matchDateTimeUTC"]).astimezone().date()
            if kickoff >= from_date:
                days.add(kickoff)
    return sorted(days)[:limit]
