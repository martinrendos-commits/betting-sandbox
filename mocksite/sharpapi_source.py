"""SharpAPI odds client and normalization helpers."""

from __future__ import annotations

import json
import hashlib
import logging
import os
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from .env_file import load_env_file
from .fixtures_source import Fixture, USER_AGENT
from .store import store_api_payload, store_sharp_event_link, store_sharp_events, store_sharp_odds

log = logging.getLogger("mocksite.sharpapi")

BASE_URL = "https://api.sharpapi.io/api/v1"
DEFAULT_RPM = 12
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".cache"
_LIMITER_LOCK = threading.Lock()
_REQUEST_TIMES: list[float] = []
_ACCOUNT_LOCK = threading.Lock()
_ACCOUNT: dict | None = None
_SERVER_LIMIT: int | None = None
_DISABLED_LOGGED = False


class SharpAPIError(RuntimeError):
    """A user-facing SharpAPI failure with optional plan information."""

    def __init__(self, message: str, *, code: str = "", required_tier: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.required_tier = required_tier


def sharpapi_key() -> str:
    load_env_file()
    return os.environ.get("SHARPAPI_API_KEY", "").strip()


def sharpapi_enabled() -> bool:
    disabled = os.environ.get("SHARPAPI_DISABLED", "").strip().lower()
    return disabled not in {"1", "true", "yes", "on"} and bool(sharpapi_key())


def sharpapi_status() -> str:
    if os.environ.get("SHARPAPI_DISABLED", "").strip().lower() in {"1", "true", "yes", "on"}:
        return "SharpAPI je vypnuté nastavením SHARPAPI_DISABLED."
    if not sharpapi_key():
        return "SharpAPI je vypnuté: chýba SHARPAPI_API_KEY."
    return "SharpAPI je zapnuté."


def _cache_dir() -> Path:
    return Path(os.environ.get("MOCK_CACHE_DIR", str(DEFAULT_CACHE_DIR)))


def _cache_ttl(endpoint: str) -> float:
    if endpoint in {"sports", "leagues", "sportsbooks", "markets", "teams"}:
        return 86400.0
    if endpoint.startswith("account"):
        return 3600.0
    if endpoint.startswith("events"):
        return 900.0
    if endpoint.startswith("odds"):
        return 60.0
    return 300.0


def _wait_for_rate_limit() -> None:
    with _LIMITER_LOCK:
        now = time.monotonic()
        _REQUEST_TIMES[:] = [stamp for stamp in _REQUEST_TIMES if now - stamp < 60.0]
        limit = _account_limit()
        if len(_REQUEST_TIMES) >= limit:
            delay = 60.0 - (now - _REQUEST_TIMES[0]) + 0.01
            if delay > 0:
                time.sleep(min(delay, 60.0))
                now = time.monotonic()
                _REQUEST_TIMES[:] = [stamp for stamp in _REQUEST_TIMES if now - stamp < 60.0]
        _REQUEST_TIMES.append(time.monotonic())


def sharpapi_request_available(requests: int = 1) -> bool:
    with _LIMITER_LOCK:
        now = time.monotonic()
        _REQUEST_TIMES[:] = [stamp for stamp in _REQUEST_TIMES if now - stamp < 60.0]
        return len(_REQUEST_TIMES) + requests <= _account_limit()


def _account_limit() -> int:
    if _SERVER_LIMIT is not None and _SERVER_LIMIT > 0:
        return _SERVER_LIMIT
    if _ACCOUNT:
        value = _ACCOUNT.get("requests_per_minute")
        if value is None and isinstance(_ACCOUNT.get("usage"), dict):
            value = _ACCOUNT["usage"].get("requests_per_minute")
        if isinstance(value, int) and value > 0:
            return value
        value = _ACCOUNT.get("rate_limit")
        if isinstance(value, dict):
            value = value.get("requests_per_minute")
        if isinstance(value, int) and value > 0:
            return value
    return DEFAULT_RPM


def _update_limit(headers: Mapping[str, str]) -> None:
    global _SERVER_LIMIT
    value = headers.get("X-RateLimit-Limit")
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return
    if parsed > 0:
        _SERVER_LIMIT = parsed


def _error_from_payload(payload: dict, status: int) -> SharpAPIError:
    error = payload.get("error") or {}
    code = str(error.get("code") or "")
    message = str(error.get("message") or f"SharpAPI odpovedalo HTTP {status}.")
    required = str(error.get("required_tier") or error.get("tier_required") or "")
    if code in {"missing_api_key", "invalid_api_key", "unauthorized"} or status == 401:
        return SharpAPIError("SharpAPI kľúč chýba alebo je neplatný; zvyšok servera pokračuje bez SharpAPI.", code=code or "invalid_api_key")
    if code == "tier_restricted" or status == 403:
        suffix = f" (potrebný plán: {required})" if required else ""
        return SharpAPIError(f"SharpAPI endpoint je mimo vášho plánu{suffix}; ostatné funkcie pokračujú.", code=code or "tier_restricted", required_tier=required)
    if code == "rate_limited" or status == 429:
        return SharpAPIError("SharpAPI prekročilo limit požiadaviek; skúsi sa exponenciálny návrat.", code="rate_limited")
    return SharpAPIError(f"SharpAPI chyba: {message}", code=code)


def _account_info() -> dict:
    global _ACCOUNT
    if _ACCOUNT is not None:
        return _ACCOUNT
    with _ACCOUNT_LOCK:
        if _ACCOUNT is None and sharpapi_key():
            try:
                payload = _request("account", "GET", persist=False)
                data = payload.get("data") or {}
                _ACCOUNT = data if isinstance(data, dict) else {}
            except SharpAPIError:
                _ACCOUNT = {}
    return _ACCOUNT or {}


def _request_with_id(
    endpoint: str,
    method: str = "GET",
    params: dict[str, object] | None = None,
    body: dict | None = None,
    persist: bool = True,
) -> tuple[dict, int | None]:
    key = sharpapi_key()
    if not key:
        raise SharpAPIError("SharpAPI je vypnuté: chýba SHARPAPI_API_KEY.", code="missing_api_key")
    query = urllib.parse.urlencode([(name, value) for name, value in (params or {}).items() if value is not None])
    url = f"{BASE_URL}/{endpoint}" + (f"?{query}" if query else "")
    cache_suffix = query.replace("&", "_").replace("=", "-") if query else ""
    if len(cache_suffix) > 120:
        cache_suffix = hashlib.sha256(query.encode("utf-8")).hexdigest()
    cache_key = "sharpapi-" + endpoint.replace("/", "-") + ("-" + cache_suffix if cache_suffix else "")
    cache_file = _cache_dir() / f"{cache_key}.json"
    if method == "GET" and cache_file.exists() and time.time() - cache_file.stat().st_mtime < _cache_ttl(endpoint):
        return json.loads(cache_file.read_text(encoding="utf-8")), None
    attempts = 0
    while True:
        _wait_for_rate_limit()
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT, "X-API-Key": key}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, headers=headers, method=method, data=data)
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
                _update_limit(response.headers)
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8", "replace"))
            except json.JSONDecodeError:
                payload = {}
            if error.code == 429 and attempts < 3:
                time.sleep(0.5 * (2**attempts))
                attempts += 1
                continue
            raise _error_from_payload(payload, error.code) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise SharpAPIError(f"SharpAPI sa nedá načítať: {error}") from error
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        payload_id = store_api_payload(payload, endpoint=endpoint, source="sharpapi") if persist else None
        return payload, payload_id


def _request(
    endpoint: str,
    method: str = "GET",
    params: dict[str, object] | None = None,
    body: dict | None = None,
    persist: bool = True,
) -> dict:
    payload, _ = _request_with_id(endpoint, method, params, body, persist)
    return payload


def _page_cap() -> int:
    try:
        value = int(os.environ.get("SHARPAPI_PAGE_CAP", "3"))
    except ValueError:
        return 3
    return max(1, min(value, 10))


def _request_pages(
    endpoint: str,
    *,
    params: dict[str, object],
) -> list[tuple[dict, int | None]]:
    pages: list[tuple[dict, int | None]] = []
    page_params = dict(params)
    for page_number in range(_page_cap()):
        payload, payload_id = _request_with_id(endpoint, params=page_params)
        pages.append((payload, payload_id))
        pagination = payload.get("pagination")
        if not isinstance(pagination, dict) or not pagination.get("has_more"):
            break
        next_cursor = pagination.get("next_cursor")
        next_offset = pagination.get("next_offset")
        if next_cursor:
            page_params = {**params, "cursor": next_cursor}
        elif next_offset is not None:
            page_params = {**params, "offset": next_offset}
        else:
            break
        if not sharpapi_request_available():
            break
    return pages


def _get(endpoint: str, **params: object) -> dict:
    return _request(endpoint, params=params)


def account() -> dict:
    global _ACCOUNT
    payload = _request("account")
    data = payload.get("data") or {}
    _ACCOUNT = data if isinstance(data, dict) else {}
    return payload


def usage() -> dict:
    return _get("account/usage")


def sports() -> dict:
    return _get("sports")


def leagues() -> dict:
    return _get("leagues")


def sportsbooks() -> dict:
    return _get("sportsbooks")


def markets() -> dict:
    return _get("markets")


def teams() -> dict:
    return _get("teams")


def injuries(**params: object) -> dict:
    return _get("injuries", **params)


def account_keys() -> dict:
    return _get("account/keys")


def events(**params: object) -> dict:
    return _get("events", **params)


def event(event_id: str) -> dict:
    return _get(f"events/{urllib.parse.quote(event_id, safe='')}")


def event_odds(event_id: str) -> dict:
    payload = _get(f"events/{urllib.parse.quote(event_id, safe='')}/odds")
    data = payload.get("data") or []
    if isinstance(data, list):
        for row in data:
            if isinstance(row, dict):
                row.setdefault("event_id", event_id)
    return payload


def event_markets(event_id: str) -> dict:
    return _get(f"events/{urllib.parse.quote(event_id, safe='')}/markets")


def odds(**params: object) -> dict:
    return _get("odds", **params)


def odds_delta(**params: object) -> dict:
    return _get("odds/delta", **params)


def odds_best(**params: object) -> dict:
    return _get("odds/best", **params)


def odds_comparison(**params: object) -> dict:
    return _get("odds/comparison", **params)


def odds_batch(requests: list[dict]) -> dict:
    return _request("odds/batch", "POST", body={"requests": requests})


def odds_closing(**params: object) -> dict:
    return _get("odds/closing", **params)


def opportunities_ev(**params: object) -> dict:
    return _get("opportunities/ev", **params)


def opportunities_arbitrage(**params: object) -> dict:
    return _get("opportunities/arbitrage", **params)


def opportunities_middles(**params: object) -> dict:
    return _get("opportunities/middles", **params)


def opportunities_middles_summary(**params: object) -> dict:
    return _get("opportunities/middles/summary", **params)


def opportunity_middle(opportunity_id: str) -> dict:
    return _get(f"opportunities/middles/{urllib.parse.quote(opportunity_id, safe='')}")


def opportunities_low_hold(**params: object) -> dict:
    return _get("opportunities/low_hold", **params)


def splits(**params: object) -> dict:
    return _get("splits", **params)


def splits_history(**params: object) -> dict:
    return _get("splits/history", **params)


def gamestate(**params: object) -> dict:
    return _get("gamestate", **params)


def gamestate_sport(sport: str) -> dict:
    return _get(f"gamestate/{urllib.parse.quote(sport, safe='')}")


def historical(path: str, **params: object) -> dict:
    return _get(f"historical/{path}", **params)


def futures(path: str = "", **params: object) -> dict:
    endpoint = "futures" + (f"/{path}" if path else "")
    return _get(endpoint, **params)


def deeplink(event_id: str) -> dict:
    return _get(f"deeplink/{urllib.parse.quote(event_id, safe='')}")


def deeplinks_batch(ids: list[str]) -> dict:
    return _request("deeplinks/batch", "POST", body={"ids": ids})


def normalize_events(payload: dict) -> list[dict]:
    data = payload.get("data") or []
    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        if not item.get("home_team") or not item.get("away_team"):
            continue
        if str(item.get("market_type", "")).casefold() == "outright":
            continue
        if bool(item.get("is_player_prop")):
            continue
        rows.append(dict(item))
    return rows


def _selection_key(row: dict) -> str:
    market = str(row.get("market_type") or "").casefold()
    selection = str(row.get("selection") or "").casefold()
    if market == "moneyline":
        if selection in {"draw", "tie", "x"} or "draw" in selection or selection.startswith("tie"):
            return "draw"
        home = str(row.get("home_team") or "").casefold()
        away = str(row.get("away_team") or "").casefold()
        if selection == home:
            return "home"
        if selection == away:
            return "away"
    if market == "total_goals":
        try:
            line = float(row.get("line") or 0)
        except (TypeError, ValueError):
            line = 0.0
        if selection.startswith("over"):
            return "over_2_5" if line == 2.5 else "over"
        if selection.startswith("under"):
            return "under_2_5" if line == 2.5 else "under"
    return selection


def normalize_odds(payload: dict) -> list[dict]:
    data = payload.get("data") or []
    rows: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        event = item.get("event")
        event_id = item.get("event_id") or (event.get("id") if isinstance(event, dict) else event)
        if not event_id:
            continue
        row = dict(item)
        row["event_id"] = str(event_id)
        row["is_player_prop"] = bool(item.get("is_player_prop")) or str(item.get("market_type", "")).casefold().startswith("player_")
        row["market_concept"] = {
            "moneyline": "1x2",
            "total_goals": "over_under",
        }.get(str(item.get("market_type") or "").casefold())
        row["selection_key"] = _selection_key(row)
        rows.append(row)
    return rows


def _norm_team(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().casefold()
    tokens = [token for token in text.replace("-", " ").split() if token not in {"fc", "afc", "sc", "cf", "mls"}]
    tokens = ["saint" if token == "st" else token for token in tokens]
    return " ".join(tokens)


def _event_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def match_events_to_fixtures(events_payload: list[dict], fixtures: list[object], tolerance_minutes: int = 90) -> list[dict]:
    links: list[dict] = []
    for event_row in events_payload:
        if str(event_row.get("sport", "")).casefold() not in {"soccer", "football"}:
            continue
        event_time = _event_time(event_row.get("start_time"))
        if event_time is None:
            continue
        for fixture in fixtures:
            if not isinstance(fixture, Fixture):
                continue
            home = _norm_team(fixture.home)
            away = _norm_team(fixture.away)
            fixture_time = fixture.kickoff_utc
            if abs((event_time - fixture_time).total_seconds()) > tolerance_minutes * 60:
                continue
            if _norm_team(str(event_row.get("home_team", ""))) != home or _norm_team(str(event_row.get("away_team", ""))) != away:
                continue
            links.append({"event_id": str(event_row["id"]), "match_id": fixture.match_id, "confidence": 1.0})
            break
    return links


def persist_normalized(payload: dict) -> tuple[int, int]:
    events_rows = normalize_events(payload)
    odds_rows = normalize_odds(payload)
    event_count = store_sharp_events(events_rows)
    odds_count = store_sharp_odds(odds_rows)
    return event_count, odds_count


def refresh_for_fixtures(fixtures: list[object], *, limit: int = 10) -> tuple[int, int]:
    """Fetch soccer events and a bounded odds set for current fixtures."""
    event_pages = _request_pages("events", params={"sport": "soccer", "limit": 200})
    event_rows: list[dict] = []
    event_rows_by_payload: dict[int | None, list[dict]] = {}
    for payload, payload_id in event_pages:
        normalized = normalize_events(payload)
        event_rows.extend(normalized)
        event_rows_by_payload.setdefault(payload_id, []).extend(normalized)
    market_payload = markets()
    market_data = market_payload.get("data") or []
    market_types = {
        str(row.get("id") or row.get("name")).casefold()
        for row in market_data
        if isinstance(row, dict)
        and (not row.get("sport") or str(row.get("sport")).casefold() == "soccer")
    }
    if "moneyline" in market_types:
        main_market = "main"
    elif "total_goals" in market_types:
        main_market = "total_goals"
    else:
        main_market = None
    odds_rows: list[tuple[dict, int | None]] = []
    main_params: dict[str, object] = {"sport": "soccer", "limit": 200}
    if main_market:
        main_params["market"] = main_market
    odds_payloads = _request_pages("odds", params=main_params)
    odds_payloads.extend(
        _request_pages(
            "odds",
            params={"sport": "soccer", "is_live": True, "limit": 200},
        )
    )
    existing_event_ids = {str(event["id"]) for event in event_rows}
    for odds_payload, odds_payload_id in odds_payloads:
        normalized = normalize_odds(odds_payload)
        odds_rows.extend((row, odds_payload_id) for row in normalized)
        for row in normalized:
            if not row.get("home_team") or not row.get("away_team") or row["event_id"] in existing_event_ids:
                continue
            event_rows.append(
                {
                    "id": row["event_id"],
                    "sport": row.get("sport"),
                    "league": row.get("league"),
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "start_time": row.get("event_start_time"),
                    "status": "live" if row.get("is_live") else "upcoming",
                    "is_live": row.get("is_live"),
                }
            )
            existing_event_ids.add(row["event_id"])
    for payload_id, payload_rows in event_rows_by_payload.items():
        store_sharp_events(payload_rows, raw_payload_id=payload_id)
    links = match_events_to_fixtures(event_rows, fixtures)
    for link in links:
        store_sharp_event_link(link["event_id"], link["match_id"], link["confidence"])
    linked_ids = {str(link["event_id"]) for link in links}
    odds_count = 0
    for payload_id in {payload_id for row, payload_id in odds_rows if row["event_id"] in linked_ids}:
        payload_rows = [row for row, row_payload_id in odds_rows if row["event_id"] in linked_ids and row_payload_id == payload_id]
        odds_count += store_sharp_odds(payload_rows, raw_payload_id=payload_id)
    return len(event_rows), odds_count
