"""
Win-probability projections, layered on top of season_records.py's
deterministic bracket.

season_records.py always assumes projections are exactly right and gives
the higher-projected team the win every time - useful for "who does the
math currently favor", but it can't answer "what's the actual percentage
chance Team X wins the league" (a team that's a slight favorite in five
straight games isn't a guaranteed lock the way the deterministic bracket
implies). This module adds that:

  * matchup_win_probability / week_matchup_probabilities - a closed-form
    win percentage for one matchup, from each side's projected score
    treated as uncertain rather than exact.
  * project_league_probabilities - Monte Carlo: replays the whole season
    thousands of times, each time drawing a random score for every
    not-yet-played team-week, and tallies how often each team makes the
    playoffs / wins the championship.

IMPORTANT ASSUMPTION - please read before trusting these numbers: ingestion
only stores a point PROJECTION per team-week, not any measure of how much
that projection has actually varied historically, so there's nothing to
calibrate real variance against. A team's score in an unplayed week is
therefore modeled as Normal(mean=projected optimal-lineup points,
stdev=std_fraction * mean), sampled independently week to week and team to
team. std_fraction defaults to config.DEFAULT_SCORE_STD_FRACTION (20%, a
reasonable fantasy-football ballpark) and is adjustable everywhere it's
used. Treat the resulting percentages as a rough, better-than-a-guess
guide, not a precise forecast - raise std_fraction for a league that runs
more boom/bust, lower it for a league that plays close to its projections.
"""

import random
import statistics
from typing import Optional

from scipy.stats import norm

import config
import repo
import simulator
import season_records as records

DEFAULT_STD_FRACTION = config.DEFAULT_SCORE_STD_FRACTION
DEFAULT_N_SIMS = config.DEFAULT_N_SIMS


def matchup_win_probability(mean_a: float, mean_b: float, std_a: float, std_b: float) -> float:
    """
    P(team A outscores team B), modeling each side's score as an
    independent Normal(mean, std). A's score minus B's score is then
    Normal(mean_a - mean_b, sqrt(std_a^2 + std_b^2)), so this is just that
    difference's probability of landing above zero. Falls back to a coin
    flip on an exact tie with zero spread on both sides (e.g. two teams
    with a 0 projection).
    """
    combined_std = (std_a ** 2 + std_b ** 2) ** 0.5
    if combined_std == 0:
        if mean_a == mean_b:
            return 0.5
        return 1.0 if mean_a > mean_b else 0.0
    return float(norm.cdf((mean_a - mean_b) / combined_std))


def week_matchup_probabilities(schedule: list, weekly_means: dict, week: int,
                               std_fraction: float = DEFAULT_STD_FRACTION) -> list:
    """
    Win probability for every NOT-YET-PLAYED matchup in `week`. Matchups
    that already have a real recorded score are skipped - that's a known
    result, not something to put a probability on.

    Returns [{"team_a", "team_b", "mean_a", "mean_b", "win_prob_a", "win_prob_b"}].
    """
    out = []
    for w, a, b, score_a, score_b in schedule:
        if w != week or score_a is not None:
            continue
        mean_a = weekly_means.get(a, {}).get(week, 0.0) or 0.0
        mean_b = weekly_means.get(b, {}).get(week, 0.0) or 0.0
        std_a, std_b = mean_a * std_fraction, mean_b * std_fraction
        p = matchup_win_probability(mean_a, mean_b, std_a, std_b)
        out.append({"team_a": a, "team_b": b, "mean_a": mean_a, "mean_b": mean_b,
                    "win_prob_a": round(p, 4), "win_prob_b": round(1 - p, 4)})
    return out


def _sample_score(mean: float, std: float, rng: random.Random) -> float:
    if std <= 0:
        return mean
    return max(0.0, rng.gauss(mean, std))  # a fantasy score can't go negative


def _already_played_weeks(schedule: list) -> set:
    """{(week, team_id)} for every team-week with a real recorded score, so
    a simulation trial never overwrites an actual result with a guess."""
    played = set()
    for w, a, b, score_a, score_b in schedule:
        if score_a is not None:
            played.add((w, a))
            played.add((w, b))
    return played


def _simulate_once(schedule: list, weekly_means: dict, played: set, regular_season_weeks: int,
                   playoff_teams: int, playoff_weeks: int, std_fraction: float,
                   rng: random.Random) -> tuple:
    sampled = {
        tid: {
            w: (mean if (w, tid) in played else _sample_score(mean, mean * std_fraction, rng))
            for w, mean in weeks.items()
        }
        for tid, weeks in weekly_means.items()
    }
    recs = records.project_records(schedule, sampled, regular_season_weeks)
    seeds = records.seed_teams(recs)
    qualifiers = seeds[:playoff_teams]
    playoffs = records.project_playoffs(qualifiers, sampled, regular_season_weeks + 1, playoff_weeks)
    return recs, seeds, qualifiers, playoffs, sampled


def project_league_probabilities(conn, league_key: str, end_week: int, regular_season_weeks: int,
                                 playoff_teams: int, playoff_weeks: int, as_of_week: int = None,
                                 std_fraction: float = DEFAULT_STD_FRACTION,
                                 n_sims: int = DEFAULT_N_SIMS, seed: Optional[int] = None) -> dict:
    """
    Monte Carlo season simulation - see the module docstring for the scoring
    model and its caveats. Replays the season `n_sims` times; every week
    already played uses its real actual score in every trial, everything
    else is sampled fresh each trial. Only the SCORES are randomized -
    lineup composition still comes from each team's single best current
    optimal lineup (re-running the lineup optimizer per trial would be far
    more expensive for no real benefit, since eligibility/roster doesn't
    change trial to trial).

    `seed` fixes the RNG for reproducible results (mainly for tests);
    leave it None for a fresh random draw each call.

    Returns {"teams": [{"team_id", "team_name", "manager_name",
    "playoff_pct", "champion_pct", "avg_wins", "avg_losses", "avg_seed"}]
    sorted by championship odds descending, "n_sims", "std_fraction"}.

    Raises ValueError (same message as season_records.project_league) if
    the league has no stored schedule yet.
    """
    schedule = repo.get_schedule(conn, league_key)
    if not schedule:
        raise ValueError(
            f"No regular-season schedule stored for '{league_key}'. Run `python ingest.py` "
            "(it pulls the schedule from ESPN/Sleeper), or set a manual `schedule` "
            "for this league in config.LEAGUES and re-run ingest."
        )

    sim = simulator.simulate_all_teams(conn, league_key, 1, end_week, as_of_week=as_of_week)
    weekly_means = {tid: r["weekly"] for tid, r in sim.items()}
    team_ids = list(weekly_means)
    played = _already_played_weeks(schedule)

    rng = random.Random(seed)
    playoff_count = {tid: 0 for tid in team_ids}
    champion_count = {tid: 0 for tid in team_ids}
    win_sum = {tid: 0.0 for tid in team_ids}
    loss_sum = {tid: 0.0 for tid in team_ids}
    seed_sum = {tid: 0 for tid in team_ids}

    for _ in range(n_sims):
        recs, seeds, qualifiers, playoffs, _sampled = _simulate_once(
            schedule, weekly_means, played, regular_season_weeks, playoff_teams, playoff_weeks,
            std_fraction, rng,
        )
        for s, tid in enumerate(seeds, start=1):
            seed_sum[tid] += s
        for tid in qualifiers:
            playoff_count[tid] += 1
        if playoffs["champion"] is not None:
            champion_count[playoffs["champion"]] += 1
        for tid, rec in recs.items():
            win_sum[tid] += rec["wins"] + 0.5 * rec["ties"]
            loss_sum[tid] += rec["losses"] + 0.5 * rec["ties"]

    rows = []
    for tid in team_ids:
        rows.append({
            "team_id": tid,
            "team_name": sim[tid]["team_name"],
            "manager_name": sim[tid]["manager_name"],
            "playoff_pct": round(100 * playoff_count[tid] / n_sims, 1),
            "champion_pct": round(100 * champion_count[tid] / n_sims, 1),
            "avg_wins": round(win_sum[tid] / n_sims, 2),
            "avg_losses": round(loss_sum[tid] / n_sims, 2),
            "avg_seed": round(seed_sum[tid] / n_sims, 2),
        })
    rows.sort(key=lambda r: (-r["champion_pct"], -r["playoff_pct"], r["avg_seed"]))
    return {"teams": rows, "n_sims": n_sims, "std_fraction": std_fraction}


def calibrate_std_fraction(conn, league_key: str, min_games: int = 8) -> dict:
    """
    Estimate std_fraction FROM THIS LEAGUE'S OWN HISTORY instead of guessing:
    for every already-played week in the stored schedule, compare each
    team's ACTUAL matchup score against what that team's roster (as of that
    week) was projected to score with its optimal lineup. std_fraction is
    then the standard deviation of (actual - projected) / projected across
    every one of those team-weeks.

    CAVEAT: this compares against the OPTIMAL lineup's projection, not
    necessarily the lineup the manager actually started that week (the
    schedule only stores the final score, not who was in the lineup) - so
    if managers sometimes start a suboptimal lineup, this estimate runs a
    bit high. It also only reflects the variance actually observed so far,
    which is noisy with few weeks of data.

    Returns {"std_fraction": estimated stdev (e.g. 0.18), "bias": mean of
    the same ratio - positive means actual scores have been running ABOVE
    projections, negative means below, near 0 means no systematic
    over/under-projection - "n_games": how many team-weeks this is based
    on, "residuals": the raw per-team-week ratios}.

    Raises ValueError if there are fewer than `min_games` played team-weeks
    yet - too early in the season to trust an estimate built on that little
    data; keep the default std_fraction until more weeks are in.
    """
    schedule = repo.get_schedule(conn, league_key)
    slot_counts = repo.get_slot_counts(conn, league_key)
    player_info_cache: dict = {}
    projection_cache: dict = {}

    residuals = []
    for week, a, b, score_a, score_b in schedule:
        if score_a is None:
            continue
        for team_id, actual in ((a, score_a), (b, score_b)):
            player_ids = repo.get_roster_player_ids(conn, league_key, team_id, week)
            sim = simulator.simulate_roster(
                conn, league_key, player_ids, slot_counts, week, week,
                player_info_cache, projection_cache,
            )
            projected = sim["weekly"][week]
            if projected > 0:
                residuals.append((actual - projected) / projected)

    if len(residuals) < min_games:
        raise ValueError(
            f"Only {len(residuals)} played team-week(s) of history for '{league_key}' - need at "
            f"least {min_games} for a stable estimate. Keep the default "
            f"({config.DEFAULT_SCORE_STD_FRACTION:g}) until more weeks are played, then re-run this."
        )

    return {
        "std_fraction": round(statistics.stdev(residuals), 3),
        "bias": round(statistics.mean(residuals), 3),
        "n_games": len(residuals),
        "residuals": residuals,
    }