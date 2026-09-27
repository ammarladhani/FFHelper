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

The same payout engine is also used by the waiver/trade optimizers. Those
optimizers build hypothetical weekly score projections for one or more teams,
then call `expected_money_from_context()` without re-simulating the other teams.
For a team's OWN unmodified roster, `baseline_sim_from_context()` lets those
same callers reuse this context's already-computed weekly_means instead of
re-running the lineup optimizer a second time for a roster that's already been
simulated once (build_expected_money_context() simulates every team via
simulate_all_teams() to build the context in the first place).

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

# Weekly-high winner detection compares floats that have round-tripped
# through JSON/SQLite (actual scores from ESPN/Sleeper, or computed means) -
# exact `==` is fragile for a genuine tie, so ties are detected within this
# tolerance instead.
_TIE_EPS = 1e-6


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
        raise ValueError(
            f"League '{league_key}': only 1st-3rd place payouts are supported, got {bad}."
        )

    weekly = None
    if payouts.get("weekly_high"):
        wh = payouts["weekly_high"]
        last = league_info.league_end_week(league_key)
        start = int(wh.get("start_week", 1))
        end = min(int(wh.get("end_week") or last), last)
        if end < start:
            raise ValueError(
                f"League '{league_key}': weekly_high end_week ({end}) < start_week ({start})."
            )
        weekly = {
            "amount": float(wh["amount"]),
            "start_week": start,
            "end_week": end,
        }

    return {
        "buy_in": float(buy_in),
        "placement": placement,
        "weekly_high": weekly,
    }


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
    # abs(...) <= _TIE_EPS rather than == top: scores here can be either
    # actual final scores (floats from the platform APIs) or computed means,
    # and exact float equality misses genuine ties that differ only by
    # floating-point noise.
    return [tid for tid, s in scores.items() if abs(s - top) <= _TIE_EPS]


def build_expected_money_context(
    conn,
    league_key: str,
    as_of_week: Optional[int] = None,
    std_fraction: float = wp.DEFAULT_STD_FRACTION,
) -> dict:
    """
    Build the shared inputs needed to evaluate expected prize money.

    This deliberately simulates every current roster exactly once. The
    waiver/trade engines then reuse the returned weekly_means when evaluating
    many hypothetical rosters, replacing only the hypothetical team's
    weekly projection dictionary.

    The context is an internal Python object, not an API response.
    """
    cfg = payout_settings(league_key)
    ps = league_info.playoff_settings(league_key)
    schedule = repo.get_schedule(conn, league_key)
    if not schedule:
        raise ValueError(
            f"No regular-season schedule stored for '{league_key}'. "
            "Run `python ingest.py` first."
        )

    sim = simulator.simulate_all_teams(
        conn,
        league_key,
        1,
        ps["end_week"],
        as_of_week=as_of_week,
    )
    weekly_means = {
        tid: dict(result["weekly"])
        for tid, result in sim.items()
    }
    team_meta = {
        tid: {
            "team_name": result["team_name"],
            "manager_name": result["manager_name"],
        }
        for tid, result in sim.items()
    }

    return {
        "league_key": league_key,
        "cfg": cfg,
        "playoff_settings": ps,
        "schedule": schedule,
        "weekly_means": weekly_means,
        "team_meta": team_meta,
        "std_fraction": std_fraction,
        "as_of_week": as_of_week,
    }


def baseline_sim_from_context(context: dict, team_id, start_week: int, end_week: int) -> dict:
    """
    A simulate_roster()-shaped result for a team's CURRENT, unmodified
    roster, built from weekly_means an expected-money context already holds
    - instead of re-running the lineup optimizer for a roster that's already
    been simulated once (build_expected_money_context() simulates every team
    via simulate_all_teams() to build the context in the first place).

    Callers in waiver.py/trades.py that already hold a money_context for a
    team's baseline (as opposed to a hypothetical modified roster, which
    still needs a real simulate_roster() call - the context has no way to
    know about those) should use this instead of re-simulating.

    IMPORTANT: context["weekly_means"] spans week 1 through the league's end
    week regardless of what window the caller cares about (it comes from
    simulate_all_teams(conn, league_key, 1, ps["end_week"], ...) inside
    build_expected_money_context), so this filters down to exactly
    [start_week, end_week] to match what simulate_roster(conn, ...,
    start_week, end_week) would have totaled. Passing the wrong window here
    would silently produce a baseline total that includes weeks the caller
    never asked about.

    Money mode never applies decay (every waiver.py/trades.py caller sets
    sim_decay=None whenever objective=="money"), so weighted_total ==
    raw_total here, matching what simulate_roster(..., decay=None) returns.
    """
    full_weekly = context["weekly_means"][team_id]
    weekly = {w: (full_weekly.get(w, 0.0) or 0.0) for w in range(start_week, end_week + 1)}
    raw_total = sum(weekly.values())
    return {
        "weekly": weekly,
        "weights": {w: 1.0 for w in weekly},
        "raw_total": raw_total,
        "weighted_total": raw_total,
        "total": raw_total,
    }


def _evaluate_expected_money(
    context: dict,
    weekly_overrides: Optional[dict] = None,
    focus_team_ids: Optional[set] = None,
) -> dict:
    """
    Evaluate expected money from an already-built context.

    `weekly_overrides` is:
        {team_id: {week: projected_points, ...}}

    Only the overridden teams need new weekly projections; all other teams
    reuse the context's cached baseline projections.
    """
    cfg = context["cfg"]
    ps = context["playoff_settings"]
    schedule = context["schedule"]
    base_weekly = context["weekly_means"]
    team_meta = context["team_meta"]
    std_fraction = context["std_fraction"]

    weekly_means = {
        tid: dict(weeks)
        for tid, weeks in base_weekly.items()
    }
    for tid, override in (weekly_overrides or {}).items():
        weekly_means[tid] = dict(override)

    team_ids = list(weekly_means)
    actual, completed = _actual_scores(schedule)

    dist = wp.league_distribution(
        schedule,
        weekly_means,
        ps["regular_season_weeks"],
        ps["playoff_teams"],
        ps["playoff_weeks"],
        std_fraction,
    )

    weekly = cfg["weekly_high"]
    earned = {tid: 0.0 for tid in team_ids}
    weekly_ev = {tid: 0.0 for tid in team_ids}
    weeks_paid = 0
    n_weekly_weeks = 0

    if weekly:
        n_weekly_weeks = weekly["end_week"] - weekly["start_week"] + 1

        # NOTE: every team_id is considered for every weekly-high week here,
        # including a team that's actually on a bye in a given regular-season
        # week or already eliminated from the playoffs. That's a deliberate,
        # documented simplifying assumption (see the module docstring above),
        # not an oversight - `schedule` only carries REGULAR-SEASON matchups
        # (see db.py's replace_schedule comment), so a playoff week has no
        # entries there at all, and restricting eligibility to "teams present
        # in `schedule` this week" would wrongly zero out every team's
        # weekly-high odds during the playoffs, not just the ones on a bye.
        for week in range(weekly["start_week"], weekly["end_week"] + 1):
            if week in completed:
                weeks_paid += 1
                winners = _high_scorers(
                    {
                        tid: actual[(week, tid)]
                        for tid in team_ids
                        if (week, tid) in actual
                    }
                )
                if winners:
                    share = weekly["amount"] / len(winners)
                    for tid in winners:
                        earned[tid] += share
            else:
                scores = {}
                for tid in team_ids:
                    if (week, tid) in actual:
                        scores[tid] = (actual[(week, tid)], 0.0)
                    else:
                        mean = weekly_means[tid].get(week, 0.0) or 0.0
                        scores[tid] = (mean, mean * std_fraction)

                for tid, share in wp.week_high_probabilities(scores).items():
                    weekly_ev[tid] += weekly["amount"] * share

    rows = {}
    for tid in team_ids:
        d = dist[tid]
        placement_ev = sum(
            cfg["placement"].get(place, 0.0) * d[key]
            for place, key in PLACE_KEYS.items()
        )

        remaining = placement_ev + weekly_ev[tid]
        total = earned[tid] + remaining

        rows[tid] = {
            "team_id": tid,
            "team_name": team_meta[tid]["team_name"],
            "manager_name": team_meta[tid]["manager_name"],
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
        }

    if focus_team_ids is not None:
        rows = {tid: rows[tid] for tid in focus_team_ids if tid in rows}

    return {
        "teams": rows,
        "summary": {
            "buy_in": cfg["buy_in"],
            "n_teams": len(team_ids),
            "total_buy_ins": cfg["buy_in"] * len(team_ids),
            "total_prizes": sum(cfg["placement"].values())
            + (weekly["amount"] * n_weekly_weeks if weekly else 0.0),
            "weekly_high_weeks_paid": weeks_paid,
            "weekly_high_weeks_total": n_weekly_weeks,
            "method": "analytic",
            "std_fraction": std_fraction,
        },
    }


def expected_money_from_context(
    context: dict,
    weekly_overrides: Optional[dict] = None,
    focus_team_ids: Optional[set] = None,
) -> dict:
    """
    Public optimizer-facing helper.

    Returns a compact mapping:
        {
            team_id: {
                "expected_total": ...,
                "expected_net": ...,
                "expected_placement": ...,
                "expected_weekly_high": ...,
                ...
            }
        }

    This is intentionally separate from `expected_payouts()` because the
    optimizer evaluates many hypothetical rosters and should not re-run
    `simulate_all_teams()` for every candidate.
    """
    return _evaluate_expected_money(
        context,
        weekly_overrides=weekly_overrides,
        focus_team_ids=focus_team_ids,
    )["teams"]


def expected_payouts(
    conn,
    league_key: str,
    as_of_week: Optional[int] = None,
    std_fraction: float = wp.DEFAULT_STD_FRACTION,
) -> dict:
    """
    Returns {"teams": [...], "summary": {...}}, teams sorted by expected_total.

    Each team row: team_id, team_name, manager_name, earned,
    expected_remaining, expected_placement, expected_weekly_high,
    expected_total, buy_in, expected_net, first_pct, second_pct, third_pct.

    Raises ValueError if payouts/playoff settings/schedule are missing.
    """
    context = build_expected_money_context(
        conn,
        league_key,
        as_of_week=as_of_week,
        std_fraction=std_fraction,
    )
    evaluated = _evaluate_expected_money(context)

    rows = list(evaluated["teams"].values())
    rows.sort(key=lambda r: -r["expected_total"])

    return {
        "teams": rows,
        "summary": evaluated["summary"],
    }