"""
For a given team, find which free agent add/drop combination maximizes
the team's remaining-season optimized point total.

To keep the search tractable, free agents are pre-filtered down to the
top `fa_prefilter` by naive rest-of-season projected sum (a cheap SQL
query) before running the expensive lineup-optimizer-based delta
calculation on each candidate.
"""

import repo
from simulator import simulate_roster


def best_pickups(conn, league_key: str, team_id: int, start_week: int, end_week: int,
                  as_of_week: int = None, top_n: int = 10, fa_prefilter: int = 40) -> list:
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)

    baseline = simulate_roster(conn, league_key, current_ids, slot_counts, start_week, end_week)
    baseline_total = baseline["total"]

    fa_ids = repo.get_free_agents_ranked(conn, league_key, as_of_week, start_week, end_week, limit=fa_prefilter)

    results = []
    for fa_id in fa_ids:
        fa_info = repo.get_player_info(conn, fa_id)
        if not fa_info:
            continue

        best_delta = None
        best_drop_info = None

        for drop_id in current_ids:
            new_ids = [pid for pid in current_ids if pid != drop_id] + [fa_id]
            sim = simulate_roster(conn, league_key, new_ids, slot_counts, start_week, end_week)
            delta = sim["total"] - baseline_total

            if best_delta is None or delta > best_delta:
                best_delta = delta
                best_drop_info = repo.get_player_info(conn, drop_id)

        results.append({
            "add": fa_info,
            "drop": best_drop_info,
            "projected_gain": round(best_delta, 2) if best_delta is not None else None,
        })

    results.sort(key=lambda r: (r["projected_gain"] or float("-inf")), reverse=True)
    return results[:top_n]


def explain_pickup(conn, league_key: str, team_id: int, add_player_id: int, drop_player_id: int,
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