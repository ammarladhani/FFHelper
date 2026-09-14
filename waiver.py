"""
For a given team, find which free agent add/drop combination maximizes
the team's remaining-season optimized point total.

To keep the search tractable, free agents are pre-filtered down to the
top `fa_prefilter` by naive rest-of-season projected sum (a cheap SQL
query) before running the expensive lineup-optimizer-based delta
calculation on each candidate.
"""

import time
import itertools
import repo
from simulator import simulate_roster


def best_pickups(conn, league_key: str, team_id: int, start_week: int, end_week: int,
                 as_of_week: int = None, top_n: int = 10, fa_prefilter: int = 40) -> list:
    total_start = time.perf_counter()

    as_of_week = as_of_week or start_week

    # ---------------------------------------------------------
    # Setup
    # ---------------------------------------------------------
    setup_start = time.perf_counter()

    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(
        conn, league_key, team_id, as_of_week
    )

    # Shared caches for every simulate_roster() call in this run - each
    # candidate add/drop only differs by a couple of players, so the same
    # player info and the same weeks' projections get reused across
    # hundreds of candidate simulations instead of re-queried each time.
    player_info_cache = {}
    projection_cache = {}

    setup_time = time.perf_counter() - setup_start

    # ---------------------------------------------------------
    # Baseline
    # ---------------------------------------------------------
    baseline_start = time.perf_counter()

    baseline = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )
    baseline_total = baseline["total"]

    baseline_time = time.perf_counter() - baseline_start

    # ---------------------------------------------------------
    # Free-agent prefilter
    # ---------------------------------------------------------
    fa_start = time.perf_counter()

    fa_ids = repo.get_free_agents_ranked_by_position(
        conn,
        league_key,
        as_of_week,
        start_week,
        end_week,
        limit_per_position=fa_prefilter,
    )

    fa_time = time.perf_counter() - fa_start

    results = []

    fa_info_time = 0.0
    simulation_time = 0.0
    drop_info_time = 0.0
    simulation_count = 0

    # ---------------------------------------------------------
    # Candidate evaluation
    # ---------------------------------------------------------
    candidate_start = time.perf_counter()

    for fa_id in fa_ids:
        info_start = time.perf_counter()

        fa_info = repo.get_player_info(conn, fa_id)

        fa_info_time += time.perf_counter() - info_start

        if not fa_info:
            continue

        best_delta = None
        best_drop_info = None

        for drop_id in current_ids:
            new_ids = [pid for pid in current_ids if pid != drop_id] + [fa_id]

            sim_start = time.perf_counter()

            sim = simulate_roster(
                conn,
                league_key,
                new_ids,
                slot_counts,
                start_week,
                end_week,
                player_info_cache,
                projection_cache,
            )

            simulation_time += time.perf_counter() - sim_start
            simulation_count += 1

            delta = sim["total"] - baseline_total

            if best_delta is None or delta > best_delta:
                best_delta = delta

                drop_info_start = time.perf_counter()

                best_drop_info = repo.get_player_info(
                    conn,
                    drop_id,
                )

                drop_info_time += time.perf_counter() - drop_info_start

        results.append({
            "add": fa_info,
            "drop": best_drop_info,
            "projected_gain": round(best_delta, 2) if best_delta is not None else None,
        })

    candidate_time = time.perf_counter() - candidate_start

    # ---------------------------------------------------------
    # Sort/finalize
    # ---------------------------------------------------------
    sort_start = time.perf_counter()

    results.sort(
        key=lambda r: (r["projected_gain"] or float("-inf")),
        reverse=True,
    )

    results = results[:top_n]

    sort_time = time.perf_counter() - sort_start

    total_time = time.perf_counter() - total_start

    # ---------------------------------------------------------
    # Benchmark output
    # ---------------------------------------------------------
    print("\n=== WAIVER BENCHMARK ===")
    print(f"Free agents considered:     {len(fa_ids)}")
    print(f"Roster players:             {len(current_ids)}")
    print(f"Simulations:                {simulation_count}")
    print()
    print(f"Setup:                      {setup_time:.3f} sec")
    print(f"Baseline simulation:        {baseline_time:.3f} sec")
    print(f"FA prefilter:               {fa_time:.3f} sec")
    print(f"FA player info:             {fa_info_time:.3f} sec")
    print(f"Candidate simulations:      {simulation_time:.3f} sec")
    print(f"Drop player info:            {drop_info_time:.3f} sec")
    print(f"Candidate evaluation total: {candidate_time:.3f} sec")
    print(f"Sort/finalize:              {sort_time:.3f} sec")
    print()
    print(f"TOTAL:                      {total_time:.3f} sec")
    print("==========================\n")

    return results


def explain_pickup(conn, league_key: str, team_id: int, add_player_id: int, drop_player_id: int,
                    start_week: int, end_week: int, as_of_week: int = None) -> dict:
    """
    Week-by-week comparison of a team's projected total with vs without
    a specific add/drop, for the "why does this help" view.
    """
    as_of_week = as_of_week or start_week
    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(
        conn, league_key, team_id, as_of_week
    )

    player_info_cache = {}
    projection_cache = {}

    before = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )

    new_ids = [pid for pid in current_ids if pid != drop_player_id] + [add_player_id]

    after = simulate_roster(
        conn,
        league_key,
        new_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )

    return {
        "add": repo.get_player_info(conn, add_player_id),
        "drop": repo.get_player_info(conn, drop_player_id),
        "weekly_before": before["weekly"],
        "weekly_after": after["weekly"],
        "total_before": before["total"],
        "total_after": after["total"],
        "delta": after["total"] - before["total"],
    }

def best_pickups_2_for_2(
    conn,
    league_key: str,
    team_id: int,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    top_n: int = 10,
    fa_prefilter: int = 30,
    drop_prefilter: int = 15,
) -> list:
    """
    Find the best 2-for-2 waiver combinations.

    Adds two free agents and drops two players from the current roster.
    Every resulting roster is evaluated with the same lineup optimizer
    used by the normal 1-for-1 waiver search.

    To keep the combinatorial search manageable:

      - Free agents are prefiltered by projected points within each
        position using get_free_agents_ranked_by_position().
      - Roster players are prefiltered from worst to best by projected
        rest-of-season points using get_roster_players_ranked_worst_first().

    The final ranking is based on the actual optimized roster total,
    NOT the naive player projection sum.
    """
    total_start = time.perf_counter()
    as_of_week = as_of_week or start_week

    # ---------------------------------------------------------
    # Setup
    # ---------------------------------------------------------
    slot_counts = repo.get_slot_counts(conn, league_key)

    current_ids = repo.get_roster_player_ids(
        conn,
        league_key,
        team_id,
        as_of_week,
    )

    if len(current_ids) < 2:
        return []

    # Shared caches across every simulation in this search.
    player_info_cache = {}
    projection_cache = {}

    # ---------------------------------------------------------
    # Baseline
    # ---------------------------------------------------------
    baseline = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )

    baseline_total = baseline["total"]

    # ---------------------------------------------------------
    # Free-agent prefilter
    # ---------------------------------------------------------
    fa_ids = repo.get_free_agents_ranked_by_position(
        conn,
        league_key,
        as_of_week,
        start_week,
        end_week,
        limit_per_position=fa_prefilter,
    )

    # Remove duplicates while preserving the position-ranked order.
    fa_ids = list(dict.fromkeys(fa_ids))

    # ---------------------------------------------------------
    # Drop prefilter
    # ---------------------------------------------------------
    drop_ids = repo.get_roster_players_ranked_worst_first(
        conn,
        league_key,
        current_ids,
        start_week,
        end_week,
        limit=drop_prefilter,
    )

    # Can't make a 2-for-2 without at least two candidates on each side.
    if len(fa_ids) < 2 or len(drop_ids) < 2:
        return []

    # ---------------------------------------------------------
    # Build combinations
    # ---------------------------------------------------------
    add_pairs = list(itertools.combinations(fa_ids, 2))
    drop_pairs = list(itertools.combinations(drop_ids, 2))

    total_combinations = len(add_pairs) * len(drop_pairs)

    print("\n=== 2-FOR-2 WAIVER SEARCH ===")
    print(f"Free agents considered:     {len(fa_ids)}")
    print(f"FA pairs:                   {len(add_pairs)}")
    print(f"Drop candidates:            {len(drop_ids)}")
    print(f"Drop pairs:                 {len(drop_pairs)}")
    print(f"Total combinations:        {total_combinations:,}")

    # ---------------------------------------------------------
    # Candidate evaluation
    # ---------------------------------------------------------
    results = []
    simulation_count = 0

    candidate_start = time.perf_counter()

    # For each pair of additions, find the best pair of drops.
    for add_a, add_b in add_pairs:
        best_delta = None
        best_drop_pair = None

        for drop_a, drop_b in drop_pairs:
            new_ids = [
                pid
                for pid in current_ids
                if pid != drop_a and pid != drop_b
            ]

            new_ids.extend([add_a, add_b])

            sim = simulate_roster(
                conn,
                league_key,
                new_ids,
                slot_counts,
                start_week,
                end_week,
                player_info_cache,
                projection_cache,
            )

            simulation_count += 1

            delta = sim["total"] - baseline_total

            if best_delta is None or delta > best_delta:
                best_delta = delta
                best_drop_pair = (drop_a, drop_b)

        if best_drop_pair is None:
            continue

        results.append({
            "add": [
                repo.get_player_info(conn, add_a),
                repo.get_player_info(conn, add_b),
            ],
            "drop": [
                repo.get_player_info(conn, best_drop_pair[0]),
                repo.get_player_info(conn, best_drop_pair[1]),
            ],
            "projected_gain": round(best_delta, 2),
        })

    candidate_time = time.perf_counter() - candidate_start

    # ---------------------------------------------------------
    # Sort
    # ---------------------------------------------------------
    results.sort(
        key=lambda r: (
            r["projected_gain"]
            if r["projected_gain"] is not None
            else float("-inf")
        ),
        reverse=True,
    )

    results = results[:top_n]

    total_time = time.perf_counter() - total_start

    print(f"Simulations:                {simulation_count:,}")
    print(f"Candidate evaluation:       {candidate_time:.3f} sec")
    print(f"TOTAL:                      {total_time:.3f} sec")
    print("============================\n")

    return results


def explain_pickup_2_for_2(
    conn,
    league_key: str,
    team_id: int,
    add_player_ids: list,
    drop_player_ids: list,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
) -> dict:
    """
    Week-by-week comparison of the current roster versus a specific
    2-for-2 waiver move.
    """
    as_of_week = as_of_week or start_week

    slot_counts = repo.get_slot_counts(conn, league_key)

    current_ids = repo.get_roster_player_ids(
        conn,
        league_key,
        team_id,
        as_of_week,
    )

    player_info_cache = {}
    projection_cache = {}

    # ---------------------------------------------------------
    # Before
    # ---------------------------------------------------------
    before = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )

    # ---------------------------------------------------------
    # After
    # ---------------------------------------------------------
    new_ids = [
        pid
        for pid in current_ids
        if pid not in drop_player_ids
    ]

    new_ids.extend(add_player_ids)

    after = simulate_roster(
        conn,
        league_key,
        new_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
    )

    return {
        "add": [
            repo.get_player_info(conn, pid)
            for pid in add_player_ids
        ],
        "drop": [
            repo.get_player_info(conn, pid)
            for pid in drop_player_ids
        ],
        "weekly_before": before["weekly"],
        "weekly_after": after["weekly"],
        "total_before": before["total"],
        "total_after": after["total"],
        "delta": after["total"] - before["total"],
    }
