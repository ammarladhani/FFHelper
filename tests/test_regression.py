"""
Minimal end-to-end regression tests over a tiny in-memory league.

There were previously ZERO tests in this repo, which is how
waiver.py calling simulate_roster(..., player_info_cache, projection_cache)
against a simulate_roster() that didn't accept those two extra
positional arguments shipped and sat there - `python cli.py waiver`
would TypeError on literally every invocation. These tests exist so
that specific class of "the call site and the function signature
drifted apart" bug gets caught by `python -m pytest` instead of by a
user running the CLI.

Run with:
    python -m pytest tests/test_regression.py -v
"""

import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db
import espn_client
import ingest
import league_info
import season_records as records
import repo
import simulator
import waiver
import trades
import weighting

LEAGUE = "test_league"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    db.init_schema(connection)

    db.upsert_league(connection, LEAGUE, 1, 2026)

    db.upsert_team(connection, LEAGUE, 1, "Team A", "Alice")
    db.upsert_team(connection, LEAGUE, 2, "Team B", "Bob")

    db.upsert_setting(connection, LEAGUE, 0, "QB", 1)
    db.upsert_setting(connection, LEAGUE, 1, "RB", 1)
    db.upsert_setting(connection, LEAGUE, 2, "BE", 2)

    players = [
        # player_id, name, position, team_id (owner), weekly points (week1, week2)
        ("p_qb1", "Alice QB", "QB", 1, (20.0, 18.0)),
        ("p_rb1", "Alice RB", "RB", 1, (10.0, 9.0)),
        ("p_bn1", "Alice Bench", "RB", 1, (5.0, 5.0)),
        ("p_qb2", "Bob QB", "QB", 2, (16.0, 17.0)),
        ("p_rb2", "Bob RB", "RB", 2, (12.0, 11.0)),
        ("p_fa1", "Free Agent RB", "RB", None, (25.0, 25.0)),  # obviously better than anyone's bench
        ("p_fa2", "Free Agent QB", "QB", None, (5.0, 5.0)),
    ]

    for player_id, name, position, team_id, (w1, w2) in players:
        db.upsert_player(connection, player_id, name, position, None, [position, "BE"])
        for week, pts in ((1, w1), (2, w2)):
            db.upsert_projection(connection, LEAGUE, player_id, week, pts)
            db.upsert_ownership(connection, LEAGUE, player_id, week, team_id)

    connection.commit()
    yield connection
    connection.close()


def test_simulate_roster_accepts_shared_caches(conn):
    """This is the exact call shape waiver.py and trades.py use - it
    used to TypeError because simulate_roster() didn't accept cache
    kwargs at all."""
    slot_counts = {"QB": 1, "RB": 1, "BE": 2}
    cache_a, cache_b = {}, {}

    result = simulator.simulate_roster(
        conn, LEAGUE, ["p_qb1", "p_rb1", "p_bn1"], slot_counts, 1, 2,
        cache_a, cache_b,
    )
    assert result["total"] == pytest.approx(20.0 + 10.0 + 18.0 + 9.0)
    # caches actually got populated, not just accepted-and-ignored
    assert "p_qb1" in cache_a
    assert ("p_qb1", 1) in cache_b


def test_simulate_all_teams(conn):
    results = simulator.simulate_all_teams(conn, LEAGUE, 1, 2)
    assert results[1]["total"] == pytest.approx(20.0 + 10.0 + 18.0 + 9.0)
    assert results[2]["total"] == pytest.approx(16.0 + 12.0 + 17.0 + 11.0)


def test_waiver_best_pickups_runs_and_finds_the_obvious_upgrade(conn):
    """The whole point of best_pickups - this used to crash before ever
    reaching this assertion."""
    picks = waiver.best_pickups(conn, LEAGUE, team_id=1, start_week=1, end_week=2, top_n=5)
    assert picks, "expected at least one pickup suggestion"
    top = picks[0]
    assert top["add"]["player_id"] == "p_fa1"  # the free agent is strictly better than the bench player
    assert top["drop"]["player_id"] == "p_bn1"
    assert top["projected_gain"] > 0


def test_waiver_explain_pickup(conn):
    explanation = waiver.explain_pickup(conn, LEAGUE, 1, "p_fa1", "p_bn1", 1, 2)
    assert explanation["delta"] > 0
    assert set(explanation["weekly_before"].keys()) == {1, 2}


def test_trade_suggest_runs_without_crashing(conn):
    # Just needs to not crash and return a list - this repo's docs claim
    # combo_sizes defaults to (1,) only; verify the default actually is.
    assert trades.DEFAULT_COMBO_SIZES == (1,)
    proposals = trades.suggest_trades(conn, LEAGUE, my_team_id=1, start_week=1, end_week=2)
    assert isinstance(proposals, list)


def test_trade_evaluate(conn):
    result = trades.evaluate_trade(
        conn, LEAGUE, 1, ["p_bn1"], 2, ["p_rb2"], 1, 2,
    )
    assert "team_a" in result and "team_b" in result
    assert result["team_a"]["delta"] == pytest.approx(result["team_a"]["after"] - result["team_a"]["before"])

def test_get_free_agents_ranked_by_position(conn):
    ids = repo.get_free_agents_ranked_by_position(conn, LEAGUE, as_of_week=1, start_week=1, end_week=2, limit_per_position=1)
    assert "p_fa1" in ids  # top RB free agent
    assert "p_fa2" in ids  # top (only) QB free agent - wouldn't survive a global top-1 cut


def test_plan_waiver_moves_chains_until_no_gain(conn):
    plan = waiver.plan_waiver_moves(conn, LEAGUE, team_id=1, start_week=1, end_week=2)
    assert len(plan["moves"]) == 1  # only p_fa1/p_bn1 is a real upgrade here
    assert plan["moves"][0]["add"]["player_id"] == "p_fa1"
    assert plan["moves"][0]["drop"]["player_id"] == "p_bn1"
    assert plan["total_gain"] > 0


# ---------------------------------------------------------------------------
# Current week (slider default) and per-league season length
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("day, expected", [
    (date(2026, 9, 6), 1),     # the Sunday week 1 starts
    (date(2026, 9, 12), 1),    # Saturday - still week 1
    (date(2026, 9, 13), 2),    # Sunday rolls to week 2
    (date(2026, 9, 19), 2),
    (date(2026, 9, 20), 3),    # Sunday rolls to week 3
    (date(2026, 9, 21), 3),
    (date(2026, 8, 1), 1),     # before the season: clamps to week 1
    (date(2027, 3, 1), 18),    # after the season: clamps to the last week
])
def test_current_week_rolls_over_on_sunday(day, expected):
    assert league_info.current_week(day, last_week=18) == expected


def test_current_week_clamps_to_the_leagues_own_last_week():
    assert league_info.current_week(date(2026, 12, 25), last_week=16) == 16


def test_each_league_has_its_own_end_week():
    assert league_info.league_end_week("my_league") == 18
    assert league_info.league_end_week("sleeper_league") == 16


def test_league_end_week_falls_back_to_global_default(monkeypatch):
    monkeypatch.setattr(config, "LEAGUES", [{"platform": "sleeper", "name": "x", "league_id": "1"}])
    assert league_info.league_end_week("x") == config.END_WEEK


def test_playoff_settings(monkeypatch):
    monkeypatch.setattr(config, "LEAGUES", [
        {"name": "ok", "end_week": 16, "playoff_teams": 6, "playoff_weeks": 3},
        {"name": "unset", "end_week": 16, "playoff_teams": None, "playoff_weeks": None},
    ])
    ps = league_info.playoff_settings("ok")
    assert ps == {"end_week": 16, "playoff_teams": 6, "playoff_weeks": 3, "regular_season_weeks": 13}
    with pytest.raises(ValueError, match="playoff_teams and playoff_weeks"):
        league_info.playoff_settings("unset")


# ---------------------------------------------------------------------------
# Recency weighting
# ---------------------------------------------------------------------------

@pytest.fixture
def conn_early_late(conn):
    """Adds two free-agent QBs: one that scores early, one that scores late.
    Plain sums favor the late one (32 vs 30); with decay=0.5 the early one
    wins (30 vs 16)."""
    for pid, (w1, w2) in (("p_early", (30.0, 0.0)), ("p_late", (0.0, 32.0))):
        db.upsert_player(conn, pid, pid, "QB", None, ["QB", "BE"])
        for week, pts in ((1, w1), (2, w2)):
            db.upsert_projection(conn, LEAGUE, pid, week, pts)
            db.upsert_ownership(conn, LEAGUE, pid, week, None)
    conn.commit()
    return conn


def test_week_weights():
    assert weighting.week_weights(3, 5, 0.5) == {3: 1.0, 4: 0.5, 5: 0.25}
    assert weighting.week_weights(3, 5, None) == {3: 1.0, 4: 1.0, 5: 1.0}
    with pytest.raises(ValueError):
        weighting.week_weights(1, 2, 0)
    with pytest.raises(ValueError):
        weighting.week_weights(1, 2, 1.5)


def test_simulate_roster_weighting(conn):
    slot_counts = {"QB": 1, "RB": 1, "BE": 2}
    ids = ["p_qb1", "p_rb1", "p_bn1"]
    plain = simulator.simulate_roster(conn, LEAGUE, ids, slot_counts, 1, 2)
    weighted = simulator.simulate_roster(conn, LEAGUE, ids, slot_counts, 1, 2, decay=0.5)

    assert plain["total"] == pytest.approx(30.0 + 27.0)
    assert weighted["raw_total"] == pytest.approx(57.0)
    assert weighted["total"] == pytest.approx(30.0 + 0.5 * 27.0)
    assert weighted["weekly"] == plain["weekly"]  # weekly stays raw


def test_simulate_all_teams_weighting(conn):
    results = simulator.simulate_all_teams(conn, LEAGUE, 1, 2, decay=0.5)
    assert results[1]["total"] == pytest.approx(30.0 + 0.5 * 27.0)
    assert results[1]["raw_total"] == pytest.approx(57.0)


def test_free_agent_ranking_respects_decay(conn_early_late):
    c = conn_early_late
    plain = repo.get_free_agents_ranked(c, LEAGUE, 1, 1, 2, limit=10)
    weighted = repo.get_free_agents_ranked(c, LEAGUE, 1, 1, 2, limit=10, decay=0.5)
    assert plain.index("p_late") < plain.index("p_early")
    assert weighted.index("p_early") < weighted.index("p_late")

    by_pos_plain = repo.get_free_agents_ranked_by_position(c, LEAGUE, 1, 1, 2, limit_per_position=1)
    by_pos_weighted = repo.get_free_agents_ranked_by_position(c, LEAGUE, 1, 1, 2, limit_per_position=1, decay=0.5)
    assert "p_late" in by_pos_plain and "p_early" not in by_pos_plain
    assert "p_early" in by_pos_weighted and "p_late" not in by_pos_weighted


def test_projection_totals(conn):
    totals = repo.get_projection_totals(conn, LEAGUE, ["p_qb1", "nobody"], 1, 2, decay=0.5)
    assert totals["p_qb1"]["raw"] == pytest.approx(38.0)
    assert totals["p_qb1"]["weighted"] == pytest.approx(20.0 + 0.5 * 18.0)
    assert totals["nobody"] == {"raw": 0.0, "weighted": 0.0}


def test_trade_candidate_prefilter_respects_decay(conn_early_late):
    c = conn_early_late
    ids = ["p_early", "p_late"]
    assert trades._top_players_by_rest_of_season(c, LEAGUE, ids, 1, 2, 1) == ["p_late"]
    assert trades._top_players_by_rest_of_season(c, LEAGUE, ids, 1, 2, 1, decay=0.5) == ["p_early"]


def test_waiver_gain_is_weighted_and_raw_gain_is_reported(conn):
    # Adding the 25-pt free agent RB: +15 in week 1, +16 in week 2.
    plain = waiver.best_pickups(conn, LEAGUE, 1, 1, 2, top_n=1)[0]
    weighted = waiver.best_pickups(conn, LEAGUE, 1, 1, 2, top_n=1, decay=0.5)[0]
    assert plain["projected_gain"] == pytest.approx(31.0)
    assert plain["raw_gain"] == pytest.approx(31.0)
    assert weighted["projected_gain"] == pytest.approx(15.0 + 0.5 * 16.0)
    assert weighted["raw_gain"] == pytest.approx(31.0)


def test_waiver_plan_and_explain_with_decay(conn):
    plan = waiver.plan_waiver_moves(conn, LEAGUE, 1, 1, 2, decay=0.5)
    assert plan["total_gain"] == pytest.approx(23.0)
    assert plan["total_raw_gain"] == pytest.approx(31.0)

    why = waiver.explain_pickup(conn, LEAGUE, 1, "p_fa1", "p_bn1", 1, 2, decay=0.5)
    assert why["delta"] == pytest.approx(23.0)
    assert why["raw_delta"] == pytest.approx(31.0)
    assert why["weights"] == {1: 1.0, 2: 0.5}


def test_trade_evaluate_and_explain_with_decay(conn):
    # Team A gets Bob's RB (12/11) over its own RB (10/9): +2 each week.
    # Team B loses 7 then 6 (12/11 -> Alice's bench RB at 5/5).
    result = trades.evaluate_trade(conn, LEAGUE, 1, ["p_bn1"], 2, ["p_rb2"], 1, 2, decay=0.5)
    assert result["team_a"]["raw_delta"] == pytest.approx(4.0)
    assert result["team_a"]["delta"] == pytest.approx(2.0 + 0.5 * 2.0)
    assert result["team_b"]["raw_delta"] == pytest.approx(-13.0)
    assert result["team_b"]["delta"] == pytest.approx(-(7.0 + 0.5 * 6.0))

    why = trades.explain_trade(conn, LEAGUE, 1, ["p_bn1"], 2, ["p_rb2"], 1, 2, decay=0.5)
    assert why["team_a"]["delta"] == pytest.approx(3.0)
    assert why["team_a"]["raw_delta"] == pytest.approx(4.0)


def test_suggest_trades_with_decay_runs(conn):
    proposals = trades.suggest_trades(conn, LEAGUE, 1, 1, 2, combo_sizes=(1,), decay=0.5)
    assert isinstance(proposals, list)
    for p in proposals:
        assert "my_raw_delta" in p and "partner_raw_delta" in p


# ---------------------------------------------------------------------------
# Projected records / playoffs / champion
# ---------------------------------------------------------------------------

def test_project_records_uses_actuals_then_projections():
    weekly = {1: {1: 999.0, 2: 100.0, 3: 100.0}, 2: {1: 1.0, 2: 90.0, 3: 100.0}}
    schedule = [
        (1, 1, 2, 80.0, 95.0),    # played: team 2 won 95-80 (the 999-vs-1 "projection" is ignored)
        (2, 1, 2, None, None),    # unplayed: projected 100-90, team 1 wins
        (3, 1, 2, None, None),    # projected tie 100-100
        (4, 1, 2, None, None),    # after the regular season - ignored
    ]
    recs = records.project_records(schedule, weekly, regular_season_weeks=3)
    r1, r2 = recs[1], recs[2]
    assert (r1["wins"], r1["losses"], r1["ties"]) == (1, 1, 1)
    assert (r2["wins"], r2["losses"], r2["ties"]) == (1, 1, 1)
    assert r1["points_for"] == pytest.approx(80.0 + 100.0 + 100.0)
    assert [g["actual"] for g in r1["games"]] == [True, False, False]
    assert [g["result"] for g in r1["games"]] == ["L", "W", "T"]
    assert records.record_str({"wins": 1, "losses": 1, "ties": 1}) == "1-1-1"
    assert records.record_str({"wins": 10, "losses": 4, "ties": 0}) == "10-4"


def test_seed_teams_orders_by_wins_then_points_for():
    recs = {
        1: {"wins": 8, "ties": 0, "points_for": 1000.0},
        2: {"wins": 9, "ties": 0, "points_for": 900.0},
        3: {"wins": 8, "ties": 0, "points_for": 1100.0},
        4: {"wins": 7, "ties": 2, "points_for": 2000.0},   # 8 win-equivalents
    }
    assert records.seed_teams(recs) == [2, 4, 3, 1]


def test_bracket_order():
    assert records.bracket_order(1) == [1]
    assert records.bracket_order(4) == [1, 4, 2, 3]
    assert records.bracket_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_six_team_playoffs_give_the_top_two_seeds_byes():
    seeds = [10, 20, 30, 40, 50, 60]   # team ids, best seed first
    weekly = {
        10: {13: 0.0, 14: 120.0, 15: 100.0},
        20: {13: 0.0, 14: 95.0, 15: 0.0},
        30: {13: 100.0, 14: 110.0, 15: 100.0},
        40: {13: 80.0},
        50: {13: 85.0, 14: 100.0},
        60: {13: 90.0},
    }
    result = records.project_playoffs(seeds, weekly, first_week=13, playoff_weeks=3)
    r1, r2, r3 = result["rounds"]
    assert r1["byes"] == [10, 20]
    assert [(m["team_a"], m["team_b"], m["winner"]) for m in r1["matchups"]] == [
        (40, 50, 50),   # 5 seed upsets the 4
        (30, 60, 30),
    ]
    assert [(m["team_a"], m["team_b"], m["winner"]) for m in r2["matchups"]] == [
        (10, 50, 10),
        (20, 30, 30),
    ]
    # final is a 100-100 tie -> the better seed (10) advances
    final = r3["matchups"][0]
    assert (final["team_a"], final["team_b"], final["winner"]) == (10, 30, 10)
    assert result["champion"] == 10
    assert [r["week"] for r in result["rounds"]] == [13, 14, 15]


def test_playoffs_need_enough_weeks_and_handle_tiny_fields():
    with pytest.raises(ValueError, match="needs 3 rounds"):
        records.project_playoffs([1, 2, 3, 4, 5, 6], {}, first_week=13, playoff_weeks=2)
    assert records.project_playoffs([7], {}, 13, 1) == {"rounds": [], "champion": 7}
    assert records.project_playoffs([], {}, 13, 1) == {"rounds": [], "champion": None}


def test_project_league_requires_a_schedule(conn):
    with pytest.raises(ValueError, match="No regular-season schedule"):
        records.project_league(conn, LEAGUE, end_week=2, regular_season_weeks=1,
                               playoff_teams=2, playoff_weeks=1)


def test_project_league_end_to_end(conn):
    # Week 1 (regular season): A 30 vs B 28 -> A goes 1-0 and is the 1 seed.
    # Week 2 (final): A 27 vs B 28 -> B is the projected champion.
    db.replace_schedule(conn, LEAGUE, [(1, 1, 2, None, None)])
    result = records.project_league(conn, LEAGUE, end_week=2, regular_season_weeks=1,
                                    playoff_teams=2, playoff_weeks=1, as_of_week=1)
    first, second = result["teams"]
    assert (first["team_id"], records.record_str(first), first["seed"]) == (1, "1-0", 1)
    assert (second["team_id"], records.record_str(second), second["seed"]) == (2, "0-1", 2)
    assert first["made_playoffs"] and second["made_playoffs"]
    assert result["champion"]["team_id"] == 2
    assert second["is_champion"] and not first["is_champion"]


def test_only_top_n_teams_make_the_playoffs(conn):
    db.replace_schedule(conn, LEAGUE, [(1, 1, 2, None, None)])
    result = records.project_league(conn, LEAGUE, end_week=2, regular_season_weeks=1,
                                    playoff_teams=1, playoff_weeks=1, as_of_week=1)
    assert [t["made_playoffs"] for t in result["teams"]] == [True, False]
    assert result["champion"]["team_id"] == 1   # a one-team "bracket": the 1 seed


# ---------------------------------------------------------------------------
# Schedule ingestion
# ---------------------------------------------------------------------------

def test_espn_parse_schedule():
    entries = [
        {"matchupPeriodId": 1, "playoffTierType": "NONE", "winner": "HOME",
         "home": {"teamId": 1, "totalPoints": 100.5}, "away": {"teamId": 2, "totalPoints": 90.0}},
        {"matchupPeriodId": 2, "playoffTierType": "NONE", "winner": "UNDECIDED",
         "home": {"teamId": 1, "totalPoints": 0.0}, "away": {"teamId": 3, "totalPoints": 0.0}},
        {"matchupPeriodId": 3, "playoffTierType": "NONE", "home": {"teamId": 4}},           # bye
        {"matchupPeriodId": 4, "playoffTierType": "WINNERS_BRACKET",
         "home": {"teamId": 1}, "away": {"teamId": 2}},                                       # playoffs
        {"matchupPeriodId": 5, "playoffTierType": "NONE", "winner": "UNDECIDED",
         "home": {"teamId": 1}, "away": {"teamId": 2}},                                       # past reg season
    ]
    assert espn_client.parse_schedule(entries, regular_season_weeks=4) == [
        (1, 1, 2, 100.5, 90.0),
        (2, 1, 3, None, None),
    ]


def test_sleeper_matchups_only_keeps_scores_for_played_weeks(monkeypatch):
    def fake_fetch(league_id, week):
        if week == 1:
            return [{"roster_id": 1, "matchup_id": 1, "points": 100.0},
                    {"roster_id": 2, "matchup_id": 1, "points": 90.0},
                    {"roster_id": 3, "matchup_id": None, "points": 0.0}]   # bye
        return [{"roster_id": 1, "matchup_id": 1, "points": 0.0},
                {"roster_id": 2, "matchup_id": 1, "points": 0.0}]
    monkeypatch.setattr(ingest.sleeper_client, "fetch_matchups", fake_fetch)
    assert ingest.sleeper_matchups("x", regular_season_weeks=2, played_through=1) == [
        (1, 1, 2, 100.0, 90.0),
        (2, 1, 2, None, None),   # 0.0 points for an unplayed week is NOT a real 0-0 result
    ]


def _one_league(**extra):
    return {"platform": "sleeper", "name": LEAGUE, "league_id": "x", "end_week": 3, **extra}


def test_ingest_schedule_stores_and_is_idempotent(conn, monkeypatch):
    cfg = _one_league(playoff_teams=2, playoff_weeks=1)
    monkeypatch.setattr(config, "LEAGUES", [cfg])
    # pin "now" to week 1 (nothing played yet) so this doesn't depend on today's date
    monkeypatch.setattr(league_info, "current_week", lambda today=None, last_week=None: 1)
    monkeypatch.setattr(ingest.sleeper_client, "fetch_matchups", lambda league_id, week: [
        {"roster_id": 1, "matchup_id": 1, "points": 0.0}, {"roster_id": 2, "matchup_id": 1, "points": 0.0}])
    for _ in range(2):
        ingest.ingest_schedule(conn, cfg, LEAGUE, "sleeper", 2026)
    assert repo.get_schedule(conn, LEAGUE) == [(1, 1, 2, None, None), (2, 1, 2, None, None)]


def test_ingest_schedule_manual_override_and_skip(conn, monkeypatch, capsys):
    cfg = _one_league(playoff_teams=2, playoff_weeks=1, schedule={1: [(1, 2)], 2: [(2, 1)], 3: [(1, 2)]})
    monkeypatch.setattr(config, "LEAGUES", [cfg])
    ingest.ingest_schedule(conn, cfg, LEAGUE, "sleeper", 2026)
    assert [m[0] for m in repo.get_schedule(conn, LEAGUE)] == [1, 2]   # week 3 is a playoff week: dropped

    unset = _one_league()
    monkeypatch.setattr(config, "LEAGUES", [unset])
    ingest.ingest_schedule(conn, unset, LEAGUE, "sleeper", 2026)
    assert "skipping schedule ingest" in capsys.readouterr().out