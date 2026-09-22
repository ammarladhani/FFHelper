"""
Projected records, playoff seeding, and league champion.

Pipeline (project_league):
  1. simulator.simulate_all_teams gives each team's projected score for
     every week (optimal lineup from its current roster).
  2. project_records plays out the stored regular-season schedule: a week
     that has already been played uses the ACTUAL final score ingested
     from ESPN/Sleeper, every other week uses the projected scores - higher
     score wins. So a record is "actual results so far + projected rest".
  3. seed_teams ranks by wins (a tie counts half), then points for.
  4. project_playoffs runs a single-elimination bracket over the playoff
     weeks, again higher projected score wins each game. The last team
     standing is the projected champion.

Assumptions (deliberately simple - say so if your league differs):
  * The bracket is fixed (1 v 8, 4 v 5, 2 v 7, 3 v 6 ... with the top seeds
    getting byes when the field isn't a power of two), not re-seeded
    between rounds. One playoff week per round.
  * If playoff_weeks is larger than the number of rounds needed, the
    bracket uses the FIRST playoff weeks and the rest are ignored.
  * It's a deterministic projection ("who does the math favor each
    week?"), not a probability - there's no randomness / upset modeling.
"""

import repo
import simulator


def project_records(schedule: list, weekly_scores: dict, regular_season_weeks: int) -> dict:
    """
    schedule: (week, team_a, team_b, score_a, score_b) tuples - the two
        scores are actual finals for played weeks, None otherwise.
    weekly_scores: {team_id: {week: projected points}}.

    Returns {team_id: {"team_id", "wins", "losses", "ties", "points_for",
    "points_against", "games": [{"week", "opponent", "points_for",
    "points_against", "result": "W"/"L"/"T", "actual": bool}]}} - every
    team in weekly_scores is present, even with zero games.
    """
    records = {
        tid: {"team_id": tid, "wins": 0, "losses": 0, "ties": 0,
              "points_for": 0.0, "points_against": 0.0, "games": []}
        for tid in weekly_scores
    }

    for week, a, b, score_a, score_b in schedule:
        if week > regular_season_weeks or a not in records or b not in records:
            continue

        actual = score_a is not None and score_b is not None
        if not actual:
            score_a = weekly_scores[a].get(week, 0.0) or 0.0
            score_b = weekly_scores[b].get(week, 0.0) or 0.0

        if score_a > score_b:
            result_a, result_b = "W", "L"
        elif score_b > score_a:
            result_a, result_b = "L", "W"
        else:
            result_a = result_b = "T"

        for tid, opp, pf, pa, result in ((a, b, score_a, score_b, result_a),
                                          (b, a, score_b, score_a, result_b)):
            rec = records[tid]
            rec["wins"] += result == "W"
            rec["losses"] += result == "L"
            rec["ties"] += result == "T"
            rec["points_for"] += pf
            rec["points_against"] += pa
            rec["games"].append({"week": week, "opponent": opp, "points_for": pf,
                                 "points_against": pa, "result": result, "actual": actual})

    return records


def seed_teams(records: dict) -> list:
    """team_ids best-to-worst: win% (tie = half a win), then points for,
    then team_id so the order is always deterministic."""
    def key(tid):
        r = records[tid]
        return (-(r["wins"] + 0.5 * r["ties"]), -r["points_for"], tid)
    return sorted(records, key=key)


def bracket_order(size: int) -> list:
    """Seed numbers in standard bracket slot order for a power-of-two
    field: 8 -> [1, 8, 4, 5, 2, 7, 3, 6]; adjacent pairs meet in round 1."""
    order = [1]
    while len(order) < size:
        n = len(order) * 2
        order = [x for s in order for x in (s, n + 1 - s)]
    return order


def project_playoffs(seeds: list, weekly_scores: dict, first_week: int, playoff_weeks: int) -> dict:
    """
    seeds: playoff team_ids, best seed first. Round r is played in week
    first_week + r. Returns {"rounds": [{"week", "matchups": [{"team_a",
    "team_b", "seed_a", "seed_b", "score_a", "score_b", "winner"}],
    "byes": [team_id]}], "champion": team_id or None}.
    """
    if not seeds:
        return {"rounds": [], "champion": None}

    size = 1
    while size < len(seeds):
        size *= 2
    n_rounds = size.bit_length() - 1
    if n_rounds > playoff_weeks:
        raise ValueError(
            f"{len(seeds)} playoff teams needs {n_rounds} rounds, but only "
            f"{playoff_weeks} playoff weeks are configured."
        )

    seed_of = {tid: i + 1 for i, tid in enumerate(seeds)}
    slots = [seeds[s - 1] if s <= len(seeds) else None for s in bracket_order(size)]

    rounds = []
    for r in range(n_rounds):
        week = first_week + r
        next_slots, matchups, byes = [], [], []
        for i in range(0, len(slots), 2):
            a, b = slots[i], slots[i + 1]
            if a is None or b is None:
                advancing = a if a is not None else b
                byes.append(advancing)
                next_slots.append(advancing)
                continue
            score_a = weekly_scores[a].get(week, 0.0) or 0.0
            score_b = weekly_scores[b].get(week, 0.0) or 0.0
            # higher score wins; an exact tie goes to the better seed
            winner = a if (score_a, -seed_of[a]) > (score_b, -seed_of[b]) else b
            matchups.append({"team_a": a, "team_b": b, "seed_a": seed_of[a], "seed_b": seed_of[b],
                             "score_a": score_a, "score_b": score_b, "winner": winner})
            next_slots.append(winner)
        rounds.append({"week": week, "matchups": matchups, "byes": byes})
        slots = next_slots

    return {"rounds": rounds, "champion": slots[0]}


def project_league(conn, league_key: str, end_week: int, regular_season_weeks: int,
                   playoff_teams: int, playoff_weeks: int, as_of_week: int = None) -> dict:
    """
    Full projection for one league. `as_of_week` picks which week's
    rosters to project forward from (default: the earliest ingested week;
    callers normally pass the current week).

    Returns {"teams": [row, ...] best seed first, "playoffs": {...},
    "champion": row or None} where each row is {team_id, team_name,
    manager_name, seed, wins, losses, ties, points_for, points_against,
    made_playoffs, is_champion, games}.

    Raises ValueError if the league has no stored schedule yet.
    """
    schedule = repo.get_schedule(conn, league_key)
    if not schedule:
        raise ValueError(
            f"No regular-season schedule stored for '{league_key}'. Run `python ingest.py` "
            "(it pulls the schedule from ESPN/Sleeper), or set a manual `schedule` "
            "for this league in config.LEAGUES and re-run ingest."
        )

    sim = simulator.simulate_all_teams(conn, league_key, 1, end_week, as_of_week=as_of_week)
    weekly_scores = {tid: r["weekly"] for tid, r in sim.items()}

    records = project_records(schedule, weekly_scores, regular_season_weeks)
    seeds = seed_teams(records)
    qualifiers = seeds[:playoff_teams]
    playoffs = project_playoffs(qualifiers, weekly_scores, regular_season_weeks + 1, playoff_weeks)

    rows = []
    for seed, tid in enumerate(seeds, start=1):
        rec = records[tid]
        rows.append({
            "team_id": tid,
            "team_name": sim[tid]["team_name"],
            "manager_name": sim[tid]["manager_name"],
            "seed": seed,
            "wins": rec["wins"], "losses": rec["losses"], "ties": rec["ties"],
            "points_for": rec["points_for"], "points_against": rec["points_against"],
            "made_playoffs": tid in qualifiers,
            "is_champion": tid == playoffs["champion"],
            "games": rec["games"],
        })
    champion = next((r for r in rows if r["is_champion"]), None)
    return {"teams": rows, "playoffs": playoffs, "champion": champion}


def record_str(row: dict) -> str:
    """'10-4' or '9-4-1' (ties shown only if there are any)."""
    base = f"{row['wins']}-{row['losses']}"
    return f"{base}-{row['ties']}" if row["ties"] else base