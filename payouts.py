"""
Expected prize money per team, layered on the analytic season distribution
in win_probability.py (no sampling).

Each league in config.LEAGUES may set:

    "buy_in": 15,
    "payouts": {
        "placement":   {1: 100, 2: 65, 3: 15},            # top-3 finishes only
        "weekly_high": {"amount": 10, "start_week": 1, "end_week": 14},  # optional
    }

For every team, expected_total = earned + expected_remaining, where
  * earned = weekly-high prizes from weeks that are fully final (real scores).
    Placement prizes can't be earned before the season ends.
  * expected_remaining = placement prizes weighted by P(1st / 2nd / 3rd),
    plus weekly-high prizes weighted by P(top score) for weeks not yet final.

Where the numbers come from:
  * Placement: win_probability.league_distribution gives each team's chance
    of finishing 1st/2nd/3rd (its bracket recursion is exact given seeds;
    the seed distribution is the one approximate piece - see that module).
  * Weekly high: EXACT. Each team's score is an independent Normal, so
    P(team has the week's top score) is a one-dimensional integral
    (win_probability.week_high_probabilities); scores already final enter as
    known constants.

Assumptions (see README): 3rd place is decided by a 3rd-place game between the
two semifinal losers in the final week; a weekly-high tie splits the prize;
every team's score counts toward weekly-high, including teams that are
eliminated or on a playoff bye; and the same score-uncertainty model as
win_probability.py applies (Normal, stdev = std_fraction * projection).
"""

from typing import Optional

import league_info
import repo
import simulator
import win_probability as wp

PLACE_KEYS = {1: "first", 2: "second", 3: "third"}


def payout_settings(league_key: str) -> dict:
    """Validated {"buy_in", "placement": {place: $}, "weekly_high": {...} or None}."""
    league = league_info.get_league(league_key)
    buy_in, payouts = league.get("buy_in"), league.get("payouts")
    if buy_in is None or not payouts:
        raise ValueError(
            f"League '{league_key}' is missing buy_in and/or payouts in config.LEAGUES - "
            "set what you paid in and the prize scheme."
        )

    placement = {int(k): float(v) for k, v in (payouts.get("placement") or {}).items()}
    bad = [p for p in placement if p not in PLACE_KEYS]
    if bad:
        raise ValueError(f"League '{league_key}': only 1st-3rd place payouts are supported, got {bad}.")

    weekly = None
    if payouts.get("weekly_high"):
        wh = payouts["weekly_high"]
        last = league_info.league_end_week(league_key)
        start = int(wh.get("start_week", 1))
        end = min(int(wh.get("end_week") or last), last)
        if end < start:
            raise ValueError(f"League '{league_key}': weekly_high end_week ({end}) < start_week ({start}).")
        weekly = {"amount": float(wh["amount"]), "start_week": start, "end_week": end}

    return {"buy_in": float(buy_in), "placement": placement, "weekly_high": weekly}


def _actual_scores(schedule: list) -> tuple:
    """({(week, team_id): actual score}, {weeks where EVERY matchup is final})."""
    actual, by_week = {}, {}
    for week, a, b, score_a, score_b in schedule:
        final = score_a is not None and score_b is not None
        by_week.setdefault(week, []).append(final)
        if final:
            actual[(week, a)] = score_a
            actual[(week, b)] = score_b
    return actual, {w for w, flags in by_week.items() if all(flags)}


def _high_scorers(scores: dict) -> list:
    if not scores:
        return []
    top = max(scores.values())
    return [tid for tid, s in scores.items() if s == top]


def expected_payouts(conn, league_key: str, as_of_week: Optional[int] = None,
                     std_fraction: float = wp.DEFAULT_STD_FRACTION) -> dict:
    """
    Returns {"teams": [...], "summary": {...}}, teams sorted by expected_total.
    Each team row: team_id, team_name, manager_name, earned, expected_remaining,
    expected_placement, expected_weekly_high, expected_total, buy_in, expected_net,
    first_pct, second_pct, third_pct.

    Raises ValueError if payouts/playoff settings/schedule are missing.
    """
    cfg = payout_settings(league_key)
    ps = league_info.playoff_settings(league_key)
    schedule = repo.get_schedule(conn, league_key)
    if not schedule:
        raise ValueError(
            f"No regular-season schedule stored for '{league_key}'. Run `python ingest.py` first."
        )

    sim = simulator.simulate_all_teams(conn, league_key, 1, ps["end_week"], as_of_week=as_of_week)
    weekly_means = {tid: r["weekly"] for tid, r in sim.items()}
    team_ids = list(weekly_means)
    actual, completed = _actual_scores(schedule)

    dist = wp.league_distribution(
        schedule, weekly_means, ps["regular_season_weeks"], ps["playoff_teams"],
        ps["playoff_weeks"], std_fraction,
    )

    # Weekly-high: finalized weeks are settled now; the rest are exact expectations.
    weekly = cfg["weekly_high"]
    earned = {tid: 0.0 for tid in team_ids}
    weekly_ev = {tid: 0.0 for tid in team_ids}
    weeks_paid, n_weekly_weeks = 0, 0
    if weekly:
        n_weekly_weeks = weekly["end_week"] - weekly["start_week"] + 1
        for w in range(weekly["start_week"], weekly["end_week"] + 1):
            if w in completed:
                weeks_paid += 1
                winners = _high_scorers({t: actual[(w, t)] for t in team_ids if (w, t) in actual})
                for tid in winners:
                    earned[tid] += weekly["amount"] / len(winners)
            else:
                # a real score where one exists (e.g. a partly-final week), else Normal(projection, sd)
                scores = {}
                for t in team_ids:
                    if (w, t) in actual:
                        scores[t] = (actual[(w, t)], 0.0)
                    else:
                        mean = weekly_means[t].get(w, 0.0) or 0.0
                        scores[t] = (mean, mean * std_fraction)
                for tid, share in wp.week_high_probabilities(scores).items():
                    weekly_ev[tid] += weekly["amount"] * share

    rows = []
    for tid in team_ids:
        d = dist[tid]
        placement_ev = sum(cfg["placement"].get(p, 0.0) * d[key] for p, key in PLACE_KEYS.items())
        remaining = placement_ev + weekly_ev[tid]
        total = earned[tid] + remaining
        rows.append({
            "team_id": tid,
            "team_name": sim[tid]["team_name"],
            "manager_name": sim[tid]["manager_name"],
            "earned": round(earned[tid], 2),
            "expected_placement": round(placement_ev, 2),
            "expected_weekly_high": round(weekly_ev[tid], 2),
            "expected_remaining": round(remaining, 2),
            "expected_total": round(total, 2),
            "buy_in": cfg["buy_in"],
            "expected_net": round(total - cfg["buy_in"], 2),
            "first_pct": round(100 * d["first"], 1),
            "second_pct": round(100 * d["second"], 1),
            "third_pct": round(100 * d["third"], 1),
        })
    rows.sort(key=lambda r: -r["expected_total"])

    total_prizes = sum(cfg["placement"].values()) + (weekly["amount"] * n_weekly_weeks if weekly else 0.0)
    return {
        "teams": rows,
        "summary": {
            "buy_in": cfg["buy_in"],
            "n_teams": len(team_ids),
            "total_buy_ins": cfg["buy_in"] * len(team_ids),
            "total_prizes": total_prizes,
            "weekly_high_weeks_paid": weeks_paid,
            "weekly_high_weeks_total": n_weekly_weeks,
            "method": "analytic",
            "std_fraction": std_fraction,
        },
    }