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
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import simulator
import waiver
import trades

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