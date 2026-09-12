"""
Given a roster and league slot counts, determine the best legal
starting lineup based on ESPN fantasy eligibility and projections.
"""

import config


def _slot_id(slot_name: str):
    """Return ESPN slot ID for a readable slot name."""
    for slot_id, name in config.SLOT_MAP.items():
        if name == slot_name:
            return slot_id
    return None


def _eligible_for_slot(player: dict, slot_name: str) -> bool:
    """
    Determine whether ESPN says this player can occupy this slot.

    eligible_slots contains ESPN slot IDs. This is the authoritative
    fantasy eligibility information.

    Falls back to the old position-based behavior for players from an
    older database that don't yet have eligibility populated.
    """
    eligible_slots = player.get("eligible_slots")

    if eligible_slots:
        slot_id = _slot_id(slot_name)

        if slot_id is not None:
            return slot_id in eligible_slots

    # Backward-compatible fallback for old/incomplete data.
    return player.get("position") == slot_name


def optimize_lineup(players: list, slot_counts: dict) -> dict:
    """
    players: list of dicts with:
        player_id
        name
        position
        eligible_slots
        projected

    slot_counts:
        {"QB": 1, "RB": 2, "WR": 2, ...}

    Returns:
        {
            "lineup": {slot_name: [player, ...]},
            "bench": [player, ...],
            "total_points": float,
        }
    """

    pool = [
        dict(p, projected=(p.get("projected") or 0.0))
        for p in players
    ]

    lineup = {}
    used_ids = set()

    # 1. Fixed starting slots first.
    fixed_slots = [
        (name, count)
        for name, count in slot_counts.items()
        if (
            name not in config.FLEX_SLOT_ELIGIBILITY
            and name not in config.NON_STARTING_SLOTS
        )
    ]

    for slot_name, count in fixed_slots:
        candidates = sorted(
            (
                p
                for p in pool
                if (
                    _eligible_for_slot(p, slot_name)
                    and p["player_id"] not in used_ids
                )
            ),
            key=lambda p: p["projected"],
            reverse=True,
        )

        chosen = candidates[:count]

        lineup[slot_name] = chosen
        used_ids.update(
            p["player_id"]
            for p in chosen
        )

    # 2. Flex-like slots.
    #
    # These still use the configured fantasy-position rules because
    # FLEX/RB-WR/etc. aren't ESPN IDP eligibility slots.
    flex_slots = [
        (name, count)
        for name, count in slot_counts.items()
        if name in config.FLEX_SLOT_ELIGIBILITY
    ]

    for slot_name, count in flex_slots:
        eligible_positions = config.FLEX_SLOT_ELIGIBILITY[slot_name]

        candidates = sorted(
            (
                p
                for p in pool
                if (
                    p["position"] in eligible_positions
                    and p["player_id"] not in used_ids
                )
            ),
            key=lambda p: p["projected"],
            reverse=True,
        )

        chosen = candidates[:count]

        lineup[slot_name] = chosen
        used_ids.update(
            p["player_id"]
            for p in chosen
        )

    bench = [
        p
        for p in pool
        if p["player_id"] not in used_ids
    ]

    total_points = sum(
        p["projected"]
        for group in lineup.values()
        for p in group
    )
    print()

    for player in lineup.values():
        print(player)

    return {
        "lineup": lineup,
        "bench": bench,
        "total_points": total_points,
    }
