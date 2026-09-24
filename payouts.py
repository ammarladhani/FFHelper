"""
Expected prize money per team, layered on the Monte Carlo season replay in
win_probability.py.

Each league in config.LEAGUES may set:

    "buy_in": 15,
    "payouts": {
        "placement":   {1: 100, 2: 65, 3: 15},            # top-3 finishes only
        "weekly_high": {"amount": 10, "start_week": 1, "end_week": 14},  # optional
    }

For every team, expected_total = earned + expected_remaining, where
  * earned = weekly-high prizes from weeks that are fully final (real scores).
    Placement prizes can't be earned before the season ends.
  * expected_remaining = probability-weighted placement prizes + weekly-high
    prizes for weeks not yet final, averaged over n_sims simulated seasons.

Assumptions (see README): 3rd place is decided by a 3rd-place game between the
two semifinal losers in the final week; a weekly-high tie splits the prize;
every team's score counts toward weekly-high, including teams that are
eliminated or on a playoff bye; and the same score-uncertainty model as
win_probability.py applies (Normal, stdev = std_fraction * projection).
"""

import random
from typing import Optional

import league_info
import repo
import season_records as records
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
                     std_fraction: float = wp.DEFAULT_STD_FRACTION,
                     n_sims: int = wp.DEFAULT_N_SIMS, seed: Optional[int] = None) -> dict:
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
    played = wp._already_played_weeks(schedule)
    actual, completed = _actual_scores(schedule)

    # Weekly-high: finalized weeks are settled now; the rest are simulated.
    weekly = cfg["weekly_high"]
    earned = {tid: 0.0 for tid in team_ids}
    sim_weeks, weeks_paid, n_weekly_weeks = [], 0, 0
    if weekly:
        n_weekly_weeks = weekly["end_week"] - weekly["start_week"] + 1
        for w in range(weekly["start_week"], weekly["end_week"] + 1):
            if w in completed:
                weeks_paid += 1
                winners = _high_scorers({t: actual[(w, t)] for t in team_ids if (w, t) in actual})
                for tid in winners:
                    earned[tid] += weekly["amount"] / len(winners)
            else:
                sim_weeks.append(w)

    rng = random.Random(seed)
    place_count = {tid: {p: 0 for p in PLACE_KEYS} for tid in team_ids}
    weekly_sum = {tid: 0.0 for tid in team_ids}

    for _ in range(n_sims):
        _recs, _seeds, _quals, playoffs, sampled = wp._simulate_once(
            schedule, weekly_means, played, ps["regular_season_weeks"],
            ps["playoff_teams"], ps["playoff_weeks"], std_fraction, rng,
        )
        pod = records.podium(playoffs, sampled)
        for place, key in PLACE_KEYS.items():
            if pod[key] is not None:
                place_count[pod[key]][place] += 1

        for w in sim_weeks:
            # real score where one exists (e.g. a partly-final week), else this trial's sample
            scores = {t: actual.get((w, t), sampled[t].get(w, 0.0)) for t in team_ids}
            winners = _high_scorers(scores)
            for tid in winners:
                weekly_sum[tid] += weekly["amount"] / len(winners)

    rows = []
    for tid in team_ids:
        placement_ev = sum(cfg["placement"].get(p, 0.0) * place_count[tid][p] / n_sims for p in PLACE_KEYS)
        weekly_ev = weekly_sum[tid] / n_sims
        remaining = placement_ev + weekly_ev
        total = earned[tid] + remaining
        rows.append({
            "team_id": tid,
            "team_name": sim[tid]["team_name"],
            "manager_name": sim[tid]["manager_name"],
            "earned": round(earned[tid], 2),
            "expected_placement": round(placement_ev, 2),
            "expected_weekly_high": round(weekly_ev, 2),
            "expected_remaining": round(remaining, 2),
            "expected_total": round(total, 2),
            "buy_in": cfg["buy_in"],
            "expected_net": round(total - cfg["buy_in"], 2),
            "first_pct": round(100 * place_count[tid][1] / n_sims, 1),
            "second_pct": round(100 * place_count[tid][2] / n_sims, 1),
            "third_pct": round(100 * place_count[tid][3] / n_sims, 1),
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
            "n_sims": n_sims,
            "std_fraction": std_fraction,
        },
    }