"""
For a given team, find which free agent add/drop combination maximizes
either the team's remaining-season projected points or its expected prize
money, depending on `objective`.

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

Both entry points also take an optional `progress_callback(current, total,
message=None)` - the web UI's jobs.Job.report is passed in here to drive
a live progress bar; it's a no-op by default (None), so nothing here
changes for existing CLI callers or the test suite, which never pass it.
"""

import logging
import time
from typing import Callable, Optional

import repo
import payouts
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


def _search_pickups(
    conn,
    league_key: str,
    team_id: int,
    current_ids: list,
    droppable_ids: list,
    fa_ids: list,
    slot_counts: dict,
    start_week: int,
    end_week: int,
    player_info_cache: dict,
    projection_cache: dict,
    top_n: int,
    decay: Optional[float] = None,
    objective: str = "points",
    money_context: Optional[dict] = None,
    progress_callback: Optional[Callable] = None,
) -> tuple:
    """
    Search every add/drop combination.

    `objective="points"` preserves the existing projected-points behavior.

    `objective="money"` simulates the resulting roster through the end of
    the league season, replaces only this team's weekly projections in the
    shared prize-money model, and ranks by:
        expected_money_after - expected_money_before

    The point metrics are still returned in money mode so the UI can show
    the old point impact alongside the new objective.
    """
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")
    if objective == "money" and money_context is None:
        raise ValueError("Money objective requires a valid payout configuration.")

    sim_end_week = (
        money_context["playoff_settings"]["end_week"]
        if objective == "money"
        else end_week
    )
    sim_decay = None if objective == "money" else decay

    baseline_sim = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        sim_end_week,
        player_info_cache,
        projection_cache,
        decay=sim_decay,
    )
    baseline_points = baseline_sim["total"]
    baseline_raw = baseline_sim["raw_total"]

    baseline_money = None
    if objective == "money":
        baseline_money = payouts.expected_money_from_context(
            money_context,
            focus_team_ids={team_id},
        )[team_id]["expected_total"]

    results = []
    total_fa = len(fa_ids)

    for i, fa_id in enumerate(fa_ids, start=1):
        fa_info = repo.get_player_info(conn, fa_id, cache=player_info_cache)
        if not fa_info:
            if progress_callback:
                progress_callback(i, total_fa)
            continue

        best_objective_gain = None
        best_projected_gain = None
        best_raw_delta = None
        best_drop_info = None
        best_money_after = None

        for drop_id in droppable_ids:
            new_ids = [pid for pid in current_ids if pid != drop_id] + [fa_id]
            sim = simulate_roster(
                conn,
                league_key,
                new_ids,
                slot_counts,
                start_week,
                sim_end_week,
                player_info_cache,
                projection_cache,
                decay=sim_decay,
            )

            projected_gain = sim["total"] - baseline_points
            raw_delta = sim["raw_total"] - baseline_raw

            if objective == "money":
                after_money = payouts.expected_money_from_context(
                    money_context,
                    weekly_overrides={team_id: sim["weekly"]},
                    focus_team_ids={team_id},
                )[team_id]["expected_total"]
                objective_gain = after_money - baseline_money
            else:
                after_money = None
                objective_gain = projected_gain

            if best_objective_gain is None or objective_gain > best_objective_gain:
                best_objective_gain = objective_gain
                best_projected_gain = projected_gain
                best_raw_delta = raw_delta
                best_drop_info = repo.get_player_info(
                    conn, drop_id, cache=player_info_cache
                )
                best_money_after = after_money

        results.append({
            "add": fa_info,
            "drop": best_drop_info,
            "objective": objective,
            "objective_gain": (
                round(best_objective_gain, 2)
                if best_objective_gain is not None
                else None
            ),
            "projected_gain": (
                round(best_projected_gain, 2)
                if best_projected_gain is not None
                else None
            ),
            "raw_gain": round(best_raw_delta, 2) if best_raw_delta is not None else None,
            "money_before": round(baseline_money, 2) if baseline_money is not None else None,
            "money_after": round(best_money_after, 2) if best_money_after is not None else None,
            "money_gain": (
                round(best_money_after - baseline_money, 2)
                if best_money_after is not None and baseline_money is not None
                else None
            ),
        })

        if progress_callback:
            label = (
                f"+${best_objective_gain:.2f}"
                if objective == "money" and best_objective_gain is not None
                else f"+{best_objective_gain:.1f}"
                if best_objective_gain is not None
                else ""
            )
            progress_callback(
                i,
                total_fa,
                f"Checked {fa_info['name']} ({i}/{total_fa}) {label}".strip(),
            )

    results.sort(
        key=lambda r: (
            r["objective_gain"]
            if r["objective_gain"] is not None
            else float("-inf")
        ),
        reverse=True,
    )
    return results[:top_n], baseline_points, baseline_raw, baseline_money


def best_pickups(
    conn,
    league_key: str,
    team_id: int,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    top_n: int = 10,
    fa_prefilter: int = DEFAULT_FA_PREFILTER,
    by_position: bool = False,
    fa_per_position: int = DEFAULT_FA_PER_POSITION,
    decay: Optional[float] = None,
    objective: str = "points",
    progress_callback: Optional[Callable] = None,
) -> list:
    total_start = time.perf_counter()
    as_of_week = as_of_week or start_week

    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    droppable_ids = [pid for pid in current_ids if pid not in reserved_ids]

    player_info_cache: dict = {}
    projection_cache: dict = {}

    money_context = None
    pool_decay = None if objective == "money" else decay
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )

    fa_ids = _get_fa_pool(
        conn,
        league_key,
        as_of_week,
        start_week,
        end_week,
        fa_prefilter,
        by_position,
        fa_per_position,
        pool_decay,
    )

    results, _, _, _ = _search_pickups(
        conn,
        league_key,
        team_id,
        current_ids,
        droppable_ids,
        fa_ids,
        slot_counts,
        start_week,
        end_week,
        player_info_cache,
        projection_cache,
        top_n=top_n,
        decay=decay,
        objective=objective,
        money_context=money_context,
        progress_callback=progress_callback,
    )

    logger.debug(
        "waiver search (%s): %d free agents x %d roster spots in %.2fs",
        objective,
        len(fa_ids),
        len(current_ids),
        time.perf_counter() - total_start,
    )

    return results


def plan_waiver_moves(
    conn,
    league_key: str,
    team_id: int,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    fa_prefilter: int = DEFAULT_FA_PREFILTER,
    by_position: bool = False,
    fa_per_position: int = DEFAULT_FA_PER_POSITION,
    max_moves: int = 50,
    decay: Optional[float] = None,
    objective: str = "points",
    progress_callback: Optional[Callable] = None,
) -> dict:
    """
    Greedily chain add/drop moves using the selected objective.

    In points mode this is unchanged from the previous implementation.

    In money mode, the same greedy process is applied to expected prize-money
    gain. End-of-season placement money is included automatically, as are
    all configured future weekly-high prizes.
    """
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)
    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)

    money_context = None
    pool_decay = None if objective == "money" else decay
    sim_end_week = end_week
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )
        sim_end_week = money_context["playoff_settings"]["end_week"]

    fa_pool = _get_fa_pool(
        conn,
        league_key,
        as_of_week,
        start_week,
        end_week,
        fa_prefilter,
        by_position,
        fa_per_position,
        pool_decay,
    )

    player_info_cache: dict = {}
    projection_cache: dict = {}

    starting_sim = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        sim_end_week,
        player_info_cache,
        projection_cache,
        decay=None if objective == "money" else decay,
    )
    starting_projected_total = starting_sim["total"]
    starting_raw_total = starting_sim["raw_total"]

    starting_money = None
    if objective == "money":
        starting_money = payouts.expected_money_from_context(
            money_context,
            focus_team_ids={team_id},
        )[team_id]["expected_total"]

    running_projected_total = starting_projected_total
    running_raw_total = starting_raw_total
    running_money = starting_money

    if progress_callback:
        progress_callback(
            0,
            max_moves,
            "Checking your current roster for the first move...",
        )

    moves = []
    for step in range(max_moves):
        if not fa_pool:
            break

        droppable_ids = [pid for pid in current_ids if pid not in reserved_ids]
        top_results, baseline_points, baseline_raw, baseline_money = _search_pickups(
            conn,
            league_key,
            team_id,
            current_ids,
            droppable_ids,
            fa_pool,
            slot_counts,
            start_week,
            end_week,
            player_info_cache,
            projection_cache,
            top_n=1,
            decay=decay,
            objective=objective,
            money_context=money_context,
        )

        if not top_results:
            break

        best = top_results[0]
        objective_gain = best["objective_gain"]
        if objective_gain is None or objective_gain <= 0:
            break

        add_id = best["add"]["player_id"]
        drop_id = best["drop"]["player_id"]

        current_ids = [pid for pid in current_ids if pid != drop_id] + [add_id]
        fa_pool = [pid for pid in fa_pool if pid != add_id]

        if best["projected_gain"] is not None:
            running_projected_total = round(
                baseline_points + best["projected_gain"], 2
            )
        running_raw_total = round(
            baseline_raw + best["raw_gain"], 2
        )

        if objective == "money":
            running_money = round(
                baseline_money + best["money_gain"], 2
            )

        moves.append({
            "step": step + 1,
            "add": best["add"],
            "drop": best["drop"],
            "objective": objective,
            "gain": round(objective_gain, 2),
            "objective_gain": round(objective_gain, 2),
            "projected_gain": best["projected_gain"],
            "raw_gain": best["raw_gain"],
            "money_gain": best["money_gain"],
            "running_total": (
                running_money
                if objective == "money"
                else running_projected_total
            ),
            "running_projected_total": running_projected_total,
            "running_raw_total": running_raw_total,
            "running_money": running_money,
        })

        if progress_callback:
            label = (
                f"+${objective_gain:.2f}"
                if objective == "money"
                else f"+{objective_gain:.1f}"
            )
            progress_callback(
                step + 1,
                max_moves,
                f"Step {step + 1}: add {best['add']['name']}, "
                f"drop {best['drop']['name']} ({label})",
            )

    return {
        "objective": objective,
        "starting_total": round(
            starting_money if objective == "money" else starting_projected_total,
            2,
        ),
        "final_total": round(
            running_money if objective == "money" else running_projected_total,
            2,
        ),
        "total_gain": round(
            (
                running_money - starting_money
                if objective == "money"
                else running_projected_total - starting_projected_total
            ),
            2,
        ),
        "starting_projected_total": round(starting_projected_total, 2),
        "final_projected_total": round(running_projected_total, 2),
        "total_projected_gain": round(
            running_projected_total - starting_projected_total, 2
        ),
        "starting_raw_total": round(starting_raw_total, 2),
        "final_raw_total": round(running_raw_total, 2),
        "total_raw_gain": round(running_raw_total - starting_raw_total, 2),
        "starting_money": round(starting_money, 2) if starting_money is not None else None,
        "final_money": round(running_money, 2) if running_money is not None else None,
        "total_money_gain": (
            round(running_money - starting_money, 2)
            if running_money is not None and starting_money is not None
            else None
        ),
        "moves": moves,
    }


def explain_pickup(
    conn,
    league_key: str,
    team_id: int,
    add_player_id: str,
    drop_player_id: str,
    start_week: int,
    end_week: int,
    as_of_week: int = None,
    decay: Optional[float] = None,
    objective: str = "points",
) -> dict:
    """
    Week-by-week comparison of a team's projected total with vs without
    a specific add/drop.

    In money mode, the response also includes the before/after expected
    prize-money totals and their delta.
    """
    as_of_week = as_of_week or start_week
    objective = (objective or "points").lower()
    if objective not in {"points", "money"}:
        raise ValueError("objective must be 'points' or 'money'")

    reserved_ids = repo.get_reserved_player_ids(conn, league_key, team_id, as_of_week)
    if drop_player_id in reserved_ids:
        info = repo.get_player_info(conn, drop_player_id)
        name = info["name"] if info else drop_player_id
        raise ValueError(
            f"Can't drop {name}: currently on IR/Taxi, not eligible to be dropped."
        )

    slot_counts = repo.get_slot_counts(conn, league_key)
    current_ids = repo.get_roster_player_ids(conn, league_key, team_id, as_of_week)

    money_context = None
    sim_end_week = end_week
    sim_decay = decay
    if objective == "money":
        money_context = payouts.build_expected_money_context(
            conn,
            league_key,
            as_of_week=as_of_week,
        )
        sim_end_week = money_context["playoff_settings"]["end_week"]
        sim_decay = None

    before = simulate_roster(
        conn,
        league_key,
        current_ids,
        slot_counts,
        start_week,
        sim_end_week,
        decay=sim_decay,
    )

    new_ids = [pid for pid in current_ids if pid != drop_player_id] + [add_player_id]
    after = simulate_roster(
        conn,
        league_key,
        new_ids,
        slot_counts,
        start_week,
        sim_end_week,
        decay=sim_decay,
    )

    money_before = money_after = money_delta = None
    if objective == "money":
        money_rows = payouts.expected_money_from_context(
            money_context,
            weekly_overrides={team_id: before["weekly"]},
            focus_team_ids={team_id},
        )
        money_before = money_rows[team_id]["expected_total"]

        money_rows = payouts.expected_money_from_context(
            money_context,
            weekly_overrides={team_id: after["weekly"]},
            focus_team_ids={team_id},
        )
        money_after = money_rows[team_id]["expected_total"]
        money_delta = money_after - money_before

    return {
        "objective": objective,
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
        "money_before": round(money_before, 2) if money_before is not None else None,
        "money_after": round(money_after, 2) if money_after is not None else None,
        "money_delta": round(money_delta, 2) if money_delta is not None else None,
    }
