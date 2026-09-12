"""
Given a roster (list of players with a position and a projected score
for some week) and a league's slot counts, figure out the best legal
starting lineup and its total projected points.

Algorithm: fill fixed single-position slots first with the best
players at that exact position, then fill flex-like slots (FLEX,
OP, etc.) with the best remaining eligible players. This greedy
approach is optimal for the standard case of exact-position slots
plus a small number of flex slots — it's a well-known result for
this specific structure (not true for arbitrary multi-slot overlap
problems in general, but fine for how fantasy leagues are built).

A "player" here is any dict-like with: player_id, name, position,
projected (float or None).
"""

import config


def optimize_lineup(players: list, slot_counts: dict) -> dict:
    """
    players: list of dicts with keys player_id, name, position, projected
    slot_counts: dict of slot_name -> count, e.g. {"QB": 1, "RB": 2, ...}

    Returns:
        {
          "lineup": {slot_name: [player, ...]},
          "bench": [player, ...],
          "total_points": float,
        }
    """
    pool = [dict(p, projected=(p.get("projected") or 0.0)) for p in players]
    lineup = {}
    used_ids = set()

    # 1. Fixed single-position slots first.
    fixed_slots = [
        (name, count) for name, count in slot_counts.items()
        if name not in config.FLEX_SLOT_ELIGIBILITY and name not in config.NON_STARTING_SLOTS
    ]
    for slot_name, count in fixed_slots:
        candidates = sorted(
            (p for p in pool if p["position"] == slot_name and p["player_id"] not in used_ids),
            key=lambda p: p["projected"],
            reverse=True,
        )
        chosen = candidates[:count]
        lineup[slot_name] = chosen
        used_ids.update(p["player_id"] for p in chosen)

    # 2. Flex-like slots, best remaining eligible players.
    flex_slots = [
        (name, count) for name, count in slot_counts.items()
        if name in config.FLEX_SLOT_ELIGIBILITY
    ]
    for slot_name, count in flex_slots:
        eligible_positions = config.FLEX_SLOT_ELIGIBILITY[slot_name]
        candidates = sorted(
            (p for p in pool if p["position"] in eligible_positions and p["player_id"] not in used_ids),
            key=lambda p: p["projected"],
            reverse=True,
        )
        chosen = candidates[:count]
        lineup[slot_name] = chosen
        used_ids.update(p["player_id"] for p in chosen)

    bench = [p for p in pool if p["player_id"] not in used_ids]
    total_points = sum(p["projected"] for group in lineup.values() for p in group)
    print("")
    for player in lineup.values():
        print(player)
    return {"lineup": lineup, "bench": bench, "total_points": total_points}
