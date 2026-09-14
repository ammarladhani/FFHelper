"""
For a given team, find which free agent add/drop combination maximizes
the team's remaining-season optimized point total.

To keep the search tractable, free agents are pre-filtered down to the
top `fa_prefilter` by naive rest-of-season projected sum (a cheap SQL
query) before running the expensive lineup-optimizer-based delta
calculation on each candidate.
"""

import logging
import time

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


def best_pickups(conn, league_key: str, team_id: int, start_week: int, end_week: int,
                  as_of_week: int = None, top_n: int = 10,
                  fa_prefilter: int = DEFAULT_FA_PREFILTER) -> list:
    total_start = time.perf_counter()
    as_of_week = as_of_week or start_week

    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)

    # Shared across every simulate_roster() call in this run so a
    # player's info/eligibility and a given week's projection are each
    # only ever fetched from SQLite once, no matter how many candidate
    # rosters we try.
    player_info_cache: dict = {}
    projection_cache: dict = {}

    baseline = simulate_roster(
        conn, league_key, current_ids, slot_counts, start_week, end_week,
        player_info_cache, projection_cache,
    )
    baseline_total = baseline["total"]

    fa_ids = repo.get_free_agents_ranked(
        conn, league_key, as_of_week, start_week, end_week, limit=fa_prefilter,
    )

    results = []
    simulation_count = 0

    for fa_id in fa_ids:
        fa_info = repo.get_player_info(conn, fa_id, cache=player_info_cache)
        if not fa_info:
            continue

        best_delta = None
        best_drop_info = None

        for drop_id in current_ids:
            new_ids = [pid for pid in current_ids if pid != drop_id] + [fa_id]

            sim = simulate_roster(
                conn, league_key, new_ids, slot_counts, start_week, end_week,
                player_info_cache, projection_cache,
            )
            simulation_count += 1

            delta = sim["total"] - baseline_total
            if best_delta is None or delta > best_delta:
                best_delta = delta
                best_drop_info = repo.get_player_info(conn, drop_id, cache=player_info_cache)

        results.append({
            "add": fa_info,
            "drop": best_drop_info,
            "projected_gain": round(best_delta, 2) if best_delta is not None else None,
        })

    results.sort(key=lambda r: (r["projected_gain"] or float("-inf")), reverse=True)
    results = results[:top_n]

    logger.debug(
        "waiver search: %d free agents x %d roster spots = %d simulations in %.2fs",
        len(fa_ids), len(current_ids), simulation_count, time.perf_counter() - total_start,
    )

    return results


def explain_pickup(conn, league_key: str, team_id: int, add_player_id: str, drop_player_id: str,
                    start_week: int, end_week: int, as_of_week: int = None) -> dict:
    """
    Week-by-week comparison of a team's projected total with vs without
    a specific add/drop, for the "why does this help" view.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)

    before = simulate_roster(conn, league_key, current_ids, slot_counts, start_week, end_week)

    new_ids = [pid for pid in current_ids if pid != drop_player_id] + [add_player_id]
    after = simulate_roster(conn, league_key, new_ids, slot_counts, start_week, end_week)

    return {
        "add": repo.get_player_info(conn, add_player_id),
        "drop": repo.get_player_info(conn, drop_player_id),
        "weekly_before": before["weekly"],
        "weekly_after": after["weekly"],
        "total_before": before["total"],
        "total_after": after["total"],
        "delta": after["total"] - before["total"],
    }
