"""
For a given team, find which free agent add/drop combination maximizes
the team's remaining-season optimized point total.

Two entry points:
- best_pickups: one-shot search - top N single add/drop combos against
  the CURRENT roster, unmodified.
- plan_waiver_moves: chains moves - finds the single best add/drop,
  applies it, then searches again against the now-modified roster
  (and a free agent pool with that pickup removed), repeating until no
  move gains anything. Answers "after I make that first move, what's
  the NEXT best thing to do?" instead of just the first move alone.

To keep the search tractable, free agents are pre-filtered before the
expensive lineup-optimizer-based delta calculation on each candidate -
either to the top `fa_prefilter` overall, or (with by_position=True)
to the top `fa_per_position` at EACH position separately, so a deep
position (WR) can't flood a shallow one (QB/TE/K/...) out of the search
entirely.

Every entry point takes an optional `decay` (None = off). When given,
both the free-agent prefilter ranking AND the add/drop gain that results
are recency-weighted (see weighting.py) - near-term weeks count for more.
`projected_gain` is always the metric the results are sorted by (weighted
when `decay` is set); `raw_gain` is the same move's plain, unweighted
point gain, so you can see what it's worth in real points too.
"""

import logging
import time
from typing import Optional

import repo
from simulator import simulate_roster

logger = logging.getLogger(__name__)

# Matches the README: the whole point of this prefilter is to avoid
# running the lineup optimizer against every free agent in the league.
# This had drifted to 2533 in a previous commit, which for most leagues
# is "every free agent" - i.e. the prefilter was doing nothing and
# waiver searches were taking O(free agents x roster size) full-season
# simulations. Raise it explicitly per-call if you want a wider search.
DEFAULT_FA_PREFILTER = 40

# Default when by_position=True - top N free agents AT EACH position,
# rather than DEFAULT_FA_PREFILTER total across all positions combined.
DEFAULT_FA_PER_POSITION = 10


def _get_fa_pool(conn, league_key: str, as_of_week: int, start_week: int, end_week: int,
                  fa_prefilter: int, by_position: bool, fa_per_position: int,
                  decay: Optional[float] = None) -> list:
    if by_position:
        return repo.get_free_agents_ranked_by_position(
            conn, league_key, as_of_week, start_week, end_week,
            limit_per_position=fa_per_position, decay=decay,
        )
    return repo.get_free_agents_ranked(
        conn, league_key, as_of_week, start_week, end_week, limit=fa_prefilter, decay=decay,
    )


def _search_pickups(conn, league_key: str, current_ids: list, droppable_ids: list, fa_ids: list,
                     slot_counts: dict, start_week: int, end_week: int, player_info_cache: dict,
                     projection_cache: dict, top_n: int, decay: Optional[float] = None) -> tuple:
    """
    ...same docstring, plus:
    `droppable_ids` is the subset of current_ids allowed to be dropped
    (IR/Taxi players excluded) - current_ids itself still gets simulated
    in full, since a reserved player still occupies a roster spot and
    affects the baseline; only the "drop" half of each candidate move is
    restricted to droppable_ids.
    """
    baseline_sim = simulate_roster(
        conn, league_key, current_ids, slot_counts, start_week, end_week,
        player_info_cache, projection_cache, decay=decay,
    )
    baseline = baseline_sim["total"]
    baseline_raw = baseline_sim["raw_total"]

    results = []
    for fa_id in fa_ids:
        fa_info = repo.get_player_info(conn, fa_id, cache=player_info_cache)
        if not fa_info:
            continue

        best_delta = None
        best_raw_delta = None
        best_drop_info = None

        for drop_id in droppable_ids:   # was: current_ids
            new_ids = [pid for pid in current_ids if pid != drop_id] + [fa_id]
            sim = simulate_roster(
                conn, league_key, new_ids, slot_counts, start_week, end_week,
                player_info_cache, projection_cache, decay=decay,
            )
            delta = sim["total"] - baseline
            if best_delta is None or delta > best_delta:
                best_delta = delta
                best_raw_delta = sim["raw_total"] - baseline_raw
                best_drop_info = repo.get_player_info(conn, drop_id, cache=player_info_cache)

        results.append({
            "add": fa_info,
            "drop": best_drop_info,
            "projected_gain": round(best_delta, 2) if best_delta is not None else None,
            "raw_gain": round(best_raw_delta, 2) if best_raw_delta is not None else None,
        })

    results.sort(key=lambda r: (r["projected_gain"] or float("-inf")), reverse=True)
    return results[:top_n], baseline


def best_pickups(conn, league_key: str, team_id: int, start_week: int, end_week: int,
                  as_of_week: int = None, top_n: int = 10,
                  fa_prefilter: int = DEFAULT_FA_PREFILTER, by_position: bool = False,
                  fa_per_position: int = DEFAULT_FA_PER_POSITION,
                  decay: Optional[float] = None) -> list:
    total_start = time.perf_counter()
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    droppable_ids = [pid for pid in current_ids if pid not in reserved_ids]

    player_info_cache: dict = {}
    projection_cache: dict = {}

    fa_ids = _get_fa_pool(conn, league_key, as_of_week, start_week, end_week,
                          fa_prefilter, by_position, fa_per_position, decay)

    results, _baseline = _search_pickups(
        conn, league_key, current_ids, droppable_ids, fa_ids, slot_counts, start_week, end_week,
        player_info_cache, projection_cache, top_n=top_n, decay=decay,
    )

    logger.debug(
        "waiver search: %d free agents x %d roster spots in %.2fs",
        len(fa_ids), len(current_ids), time.perf_counter() - total_start,
    )

    return results


def plan_waiver_moves(conn, league_key: str, team_id: int, start_week: int, end_week: int,
                       as_of_week: int = None, fa_prefilter: int = DEFAULT_FA_PREFILTER,
                       by_position: bool = False, fa_per_position: int = DEFAULT_FA_PER_POSITION,
                       max_moves: int = 50, decay: Optional[float] = None) -> dict:
    """
    Greedily chain add/drop moves: find the single best move, apply it,
    then search AGAIN against the resulting roster and a free agent
    pool with that pickup removed - repeat until no move improves the
    projected total (or max_moves is hit, a safety cap against a
    pathological league, not something normally reached).

    Each step re-simulates from scratch against the roster as it stands
    AFTER every prior step, so step 2's answer already accounts for
    step 1 having happened - unlike just calling best_pickups() once,
    which always evaluates against your CURRENT real roster.

    A player dropped in an earlier step is not reconsidered as a pickup
    later in the same plan (no immediate buy-back) - only the free
    agent pool shrinks as players get ADDED, not as they get dropped.

    With `decay` set, "total"/"gain" figures are recency-weighted (the
    metric the plan is optimizing); the `*_raw_*` fields are the same
    numbers in plain, unweighted projected points.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    fa_pool = _get_fa_pool(conn, league_key, as_of_week, start_week, end_week,
                            fa_prefilter, by_position, fa_per_position, decay)

    player_info_cache: dict = {}
    projection_cache: dict = {}

    starting_sim = simulate_roster(
        conn, league_key, current_ids, slot_counts, start_week, end_week,
        player_info_cache, projection_cache, decay=decay,
    )
    starting_total = starting_sim["total"]
    starting_raw_total = starting_sim["raw_total"]
    running_total = starting_total
    running_raw_total = starting_raw_total

    moves = []
    for step in range(max_moves):
        if not fa_pool:
            break

        droppable_ids = [pid for pid in current_ids if pid not in reserved_ids]
        top_results, baseline = _search_pickups(
            conn, league_key, current_ids, droppable_ids, fa_pool, slot_counts, start_week, end_week,
            player_info_cache, projection_cache, top_n=1, decay=decay,
        )
        if not top_results or not top_results[0]["projected_gain"] or top_results[0]["projected_gain"] <= 0:
            break  # no move left that actually helps - stop here

        best = top_results[0]
        add_id = best["add"]["player_id"]
        drop_id = best["drop"]["player_id"]

        current_ids = [pid for pid in current_ids if pid != drop_id] + [add_id]
        fa_pool = [pid for pid in fa_pool if pid != add_id]
        running_total = round(baseline + best["projected_gain"], 2)
        running_raw_total = round(running_raw_total + best["raw_gain"], 2)

        moves.append({
            "step": step + 1,
            "add": best["add"],
            "drop": best["drop"],
            "gain": best["projected_gain"],
            "raw_gain": best["raw_gain"],
            "running_total": running_total,
            "running_raw_total": running_raw_total,
        })

    return {
        "starting_total": round(starting_total, 2),
        "final_total": round(running_total, 2),
        "total_gain": round(running_total - starting_total, 2),
        "starting_raw_total": round(starting_raw_total, 2),
        "final_raw_total": round(running_raw_total, 2),
        "total_raw_gain": round(running_raw_total - starting_raw_total, 2),
        "moves": moves,
    }


def explain_pickup(conn, league_key: str, team_id: int, add_player_id: str, drop_player_id: str,
                    start_week: int, end_week: int, as_of_week: int = None,
                    decay: Optional[float] = None) -> dict:
    """
    Week-by-week comparison of a team's projected total with vs without
    a specific add/drop, for the "why does this help" view.

    weekly_before/weekly_after are always raw per-week projected points.
    total_*/delta are recency-weighted when `decay` is set (matching the
    metric waiver results are ranked by); raw_total_*/raw_delta are the
    plain sums; `weights` is the {week: weight} used.
    """
    as_of_week = as_of_week or start_week
    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    if drop_player_id in reserved_ids:
        info = repo.get_player_info(conn, drop_player_id)
        name = info["name"] if info else drop_player_id
        raise ValueError(f"Can't drop {name}: currently on IR/Taxi, not eligible to be dropped.")
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)

    before = simulate_roster(conn, league_key, current_ids, slot_counts, start_week, end_week,
                             decay=decay)

    new_ids = [pid for pid in current_ids if pid != drop_player_id] + [add_player_id]
    after = simulate_roster(conn, league_key, new_ids, slot_counts, start_week, end_week,
                            decay=decay)

    return {
        "add": repo.get_player_info(conn, add_player_id),
        "drop": repo.get_player_info(conn, drop_player_id),
        "weekly_before": before["weekly"],
        "weekly_after": after["weekly"],
        "weights": before["weights"],
        "total_before": before["total"],
        "total_after": after["total"],
        "delta": after["total"] - before["total"],
        "raw_total_before": before["raw_total"],
        "raw_total_after": after["raw_total"],
        "raw_delta": after["raw_total"] - before["raw_total"],
    }