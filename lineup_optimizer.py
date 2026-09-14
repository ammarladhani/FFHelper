"""
Given a roster and league slot counts, determine the best legal
starting lineup based on fantasy eligibility and projections. Works
for any platform (ESPN, Sleeper, ...) as long as each player's
`eligible_slots` is a list of SLOT NAME STRINGS scoped to that
player's own league (e.g. ["QB", "BN"] or ["DT", "DL", "BN", "IR"]) -
ingestion is responsible for translating a platform's native
eligibility format (numeric IDs for ESPN, position strings natively
for Sleeper) into this common shape.

This solves the lineup as an actual optimal assignment problem
(max-weight bipartite matching via scipy.optimize.linear_sum_assignment)
rather than filling slots greedily in a fixed order. That matters
whenever eligibility overlaps - e.g. a defensive tackle who's also
eligible at DL and DP - since a greedy fill can lock a flexible
player into the wrong slot before a less-flexible player is
considered, which is not always fixable after the fact.
"""

import numpy as np
from scipy.optimize import linear_sum_assignment

import config

BIG = 1e9  # cost for an ineligible player/slot pairing - effectively "never assign this"


def _eligible_for_slot(player: dict, slot_name: str) -> bool:
    """
    Determine whether this player can occupy this slot, per their
    (platform-normalized) eligible_slots list of slot name strings.

    Falls back to a position-string match for older rows that don't
    have eligibility populated yet.
    """
    eligible_slots = player.get("eligible_slots")
    if eligible_slots:
        return slot_name in eligible_slots
    return player.get("position") == slot_name


def _is_eligible(player: dict, slot_name: str) -> bool:
    """Flex-type slots (FLEX, RB/WR, OP, ...) are league lineup categories
    rather than a real eligibility slot on most platforms, so they're
    checked against the player's own position instead of eligible_slots."""
    if slot_name in config.FLEX_SLOT_ELIGIBILITY:
        return player.get("position") in config.FLEX_SLOT_ELIGIBILITY[slot_name]
    return _eligible_for_slot(player, slot_name)


def optimize_lineup(players: list, slot_counts: dict) -> dict:
    """
    players: list of dicts with player_id, name, position, eligible_slots, projected
    slot_counts: {"QB": 1, "RB": 2, ..., "BE": 7, "IR": 2}

    Returns:
        {
            "lineup": {slot_name: [player, ...]},   # scoring slots only
            "bench": [player, ...],                 # BE/IR + anyone unassigned
            "total_points": float,
        }
    """
    pool = [dict(p, projected=(p.get("projected") or 0.0)) for p in players]

    # Expand slot_counts into individual slot instances, e.g. RB:2 -> two
    # separate "RB" instances, so each can be assigned to a different player.
    slot_instances = []
    for slot_name, count in slot_counts.items():
        scores = slot_name not in config.NON_STARTING_SLOTS
        for _ in range(count):
            slot_instances.append((slot_name, scores))

    if not pool or not slot_instances:
        return {"lineup": {}, "bench": pool, "total_points": 0.0}

    n_players = len(pool)
    n_slots = len(slot_instances)

    cost = np.full((n_players, n_slots), BIG)
    for i, p in enumerate(pool):
        for j, (slot_name, scores) in enumerate(slot_instances):
            if _is_eligible(p, slot_name):
                cost[i, j] = -p["projected"] if scores else 0.0

    row_ind, col_ind = linear_sum_assignment(cost)

    lineup = {}
    assigned_ids = set()
    total_points = 0.0

    for r, c in zip(row_ind, col_ind):
        if cost[r, c] >= BIG:
            continue  # no eligible slot found for this player - leave on bench
        player = pool[r]
        slot_name, scores = slot_instances[c]
        assigned_ids.add(player["player_id"])
        if scores:
            lineup.setdefault(slot_name, []).append(player)
            total_points += player["projected"]

    bench = [p for p in pool if p["player_id"] not in assigned_ids]

    return {"lineup": lineup, "bench": bench, "total_points": total_points}
