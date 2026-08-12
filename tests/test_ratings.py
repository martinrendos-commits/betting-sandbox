from sandbox_bot.ratings import MIN_FINISHED_MATCHES, load_league_rating, pair_odds
from mocksite.store import store_match_payloads


def match(index, home, away, home_goals, away_goals):
    return {
        "match_id": str(index),
        "date_unix": 1760000000 + index * 86400,
        "status": "complete",
        "league": {"league_id": 9, "name": "Test Liga"},
        "home_team": {"team_id": home, "team_name": home},
        "away_team": {"team_id": away, "team_name": away},
        "score": {"home": home_goals, "away": away_goals},
    }


def test_strengths_and_league_filtering(tmp_path):
    path = tmp_path / "ratings.sqlite3"
    store_match_payloads(
        [
            match(1, "A", "B", 3, 0),
            match(2, "A", "B", 2, 1),
            match(3, "B", "A", 0, 2),
            {
                **match(4, "X", "Y", 1, 1),
                "league": {"league_id": 10, "name": "Other Liga"},
            },
        ],
        source="test",
        path=path,
    )
    rating = load_league_rating("Test Liga", path)
    assert rating is not None
    assert rating.league_id == "9"
    assert rating.matches == 3
    assert rating.average_home_goals == 5 / 3
    team_a = next(team for team in rating.teams if team.name == "A")
    assert team_a.home_attack > 1.0
    assert pair_odds(rating, "A", "B").lambda_home > pair_odds(rating, "A", "B").lambda_away
    assert load_league_rating("Other Liga", path).matches == 1


def test_small_sample_falls_back_to_xg(tmp_path):
    path = tmp_path / "ratings.sqlite3"
    item = match(1, "A", "B", 5, 0)
    item["xg"] = {"prematch": {"home": 1.2, "away": 0.8}}
    store_match_payloads([item], source="test", path=path)
    rating = load_league_rating("9", path)
    assert rating is not None
    assert rating.has_small_sample
    assert rating.matches < MIN_FINISHED_MATCHES
    odds = pair_odds(rating, "A", "B")
    assert odds is not None
    assert odds.lambda_home == 1.2
    assert odds.lambda_away == 0.8
    assert odds.used_fallback


def test_fallback_is_decided_per_pair(tmp_path):
    path = tmp_path / "ratings.sqlite3"
    results = [
        match(1, "A", "B", 3, 0),
        match(2, "B", "A", 1, 1),
        match(3, "A", "B", 2, 1),
        match(4, "B", "A", 0, 2),
        match(5, "A", "B", 2, 0),
        match(6, "B", "A", 1, 1),
        match(7, "X", "B", 1, 0),
    ]
    store_match_payloads(results, source="test", path=path)
    rating = load_league_rating("Test Liga", path)
    assert rating is not None
    assert rating.has_small_sample
    covered = pair_odds(rating, "A", "B")
    sparse = pair_odds(rating, "X", "B")
    assert covered is not None and sparse is not None
    assert not covered.used_fallback
    assert sparse.used_fallback


def test_numeric_league_id_matches_exactly(tmp_path):
    path = tmp_path / "ratings.sqlite3"
    item = match(1, "A", "B", 1, 0)
    store_match_payloads([item], source="test", path=path)
    other = {**match(2, "C", "D", 2, 0), "league": {"league_id": 19, "name": "League 19"}}
    store_match_payloads([other], source="test", path=path)
    rating = load_league_rating("9", path)
    assert rating is not None
    assert rating.league_name == "Test Liga"
