"""
Win-probability projections, layered on top of season_records.py's
deterministic bracket - computed ANALYTICALLY, no sampling.

season_records.py always assumes projections are exactly right and gives
the higher-projected team the win every time. This module answers the
probabilistic version ("what's the actual chance Team X wins the league?")
by treating each unplayed team-week score as Normal(mean=projection,
stdev=std_fraction * mean), independent across teams and weeks.

IMPORTANT ASSUMPTION (unchanged from the Monte Carlo version): ingestion
stores only a point PROJECTION per team-week, so there is no real variance
data to calibrate against. std_fraction defaults to
config.DEFAULT_SCORE_STD_FRACTION and is adjustable everywhere;
calibrate_std_fraction() can estimate it from a league's own played weeks.

How it works, and what is exact vs. approximate
-----------------------------------------------
Every team plays at most one game a week, so every game's score pair is
independent of every other game's. That gives a lot of exact structure:

  EXACT
  * Any single game: P(A beats B) is a normal-CDF (matchup_win_probability).
  * A team's regular-season win TOTAL: a Poisson-binomial distribution
    (sum of independent, non-identical Bernoullis), computed by an exact
    O(games^2) DP. Games already played are just fixed 0/1 terms. Ties
    are tracked in half-win units so they seed the same way
    season_records.seed_teams does. avg_wins / avg_losses are exact.
  * A team's regular-season points-for (the seeding tiebreak): a sum of
    independent normals, so itself exactly normal.
  * Weekly-high-score odds: P(team i has the top score among n teams) is
    a 1-D integral of  pdf_i(x) * prod_{j != i} cdf_j(x)  (see
    week_high_probabilities).
  * A playoff BRACKET once the seeds are fixed: scores are independent, so
    "who reaches each round" is a simple recursion up the bracket tree.

  APPROXIMATE  (the one thing with no closed form)
  * The JOINT distribution of the final standings. To get each team's
    seed distribution we treat every team's (wins, points-for) as
    independent of the others', then Poisson-binomial count how many
    finish ahead of a given team. (Independent ranked scores give a
    doubly-stochastic team-by-seed matrix automatically: each team has
    one seed, each seed one team.) That ignores real correlations - a
    game one team wins another loses, so total wins across the league
    are fixed. Measured against brute-force Monte Carlo the effect is
    mild overconfidence: playoff odds off by ~1-2.5 points at the
    extremes, title odds by ~1 point.
  * The bracket then takes those seed marginals as if seed slots were
    filled independently, with one correction that matters a lot (it took
    the title-odds error from ~3 points to under 1): a team can't meet
    itself, so when computing team i's chance in a match, its opponent
    distribution is conditioned on NOT being i.

The tests keep a brute-force Monte Carlo as an oracle and check this
module against it, so the approximation error is measured, not assumed.
"""

import math
import statistics

import numpy as np
from scipy.stats import norm

import config
import repo
import simulator
import season_records as records

DEFAULT_STD_FRACTION = config.DEFAULT_SCORE_STD_FRACTION

# Gauss-Hermite nodes/weights for E[g(X)], X ~ N(mu, sd): sum_k w_k g(mu + sqrt(2) sd x_k).
_GH_NODES = 24
_GH_X, _GH_W = np.polynomial.hermite.hermgauss(_GH_NODES)
_GH_W = _GH_W / math.sqrt(math.pi)


# ------------------------------------------------------------ single game

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


def _win_matrix(means, std_fraction: float) -> np.ndarray:
    """W[i, j] = P(team i outscores team j) for one week; zero diagonal.
    Vectorised matchup_win_probability over every ordered pair."""
    mu = np.asarray(means, dtype=float)
    sd = mu * std_fraction
    diff = mu[:, None] - mu[None, :]
    spread = np.sqrt(sd[:, None] ** 2 + sd[None, :] ** 2)
    with np.errstate(divide="ignore", invalid="ignore"):
        smooth = norm.cdf(diff / spread)
    exact = np.where(diff > 0, 1.0, np.where(diff < 0, 0.0, 0.5))
    W = np.where(spread == 0, exact, smooth)
    np.fill_diagonal(W, 0.0)
    return W


# ------------------------------------------- regular season: wins and points

def _regular_season_summary(schedule: list, weekly_means: dict, team_ids: list,
                            regular_season_weeks: int, std_fraction: float) -> tuple:
    """
    Per team: the exact distribution of regular-season wins (in HALF-WIN
    units, so a tie is 1 and a win is 2) and the mean / stdev of points-for.

    Returns (pmf, games, pf_mean, pf_sd) where pmf[i, h] = P(team i finishes
    with h half-wins), shape (n_teams, 2 * max_games + 1).
    """
    idx = {tid: i for i, tid in enumerate(team_ids)}
    n = len(team_ids)
    outcomes = [[] for _ in range(n)]          # per team: [(p_lose, p_tie, p_win), ...]
    pf_mean = np.zeros(n)
    pf_var = np.zeros(n)

    for week, a, b, score_a, score_b in schedule:
        if week > regular_season_weeks or a not in idx or b not in idx:
            continue
        ia, ib = idx[a], idx[b]
        if score_a is not None and score_b is not None:           # already played: fixed result
            if score_a > score_b:
                oa, ob = (0.0, 0.0, 1.0), (1.0, 0.0, 0.0)
            elif score_b > score_a:
                oa, ob = (1.0, 0.0, 0.0), (0.0, 0.0, 1.0)
            else:
                oa = ob = (0.0, 1.0, 0.0)
            pf_mean[ia] += score_a
            pf_mean[ib] += score_b
        else:
            mu_a = weekly_means[a].get(week, 0.0) or 0.0
            mu_b = weekly_means[b].get(week, 0.0) or 0.0
            if std_fraction == 0 and mu_a == mu_b:                 # deterministic dead heat
                oa = ob = (0.0, 1.0, 0.0)
            else:
                p = matchup_win_probability(mu_a, mu_b, mu_a * std_fraction, mu_b * std_fraction)
                oa, ob = (1.0 - p, 0.0, p), (p, 0.0, 1.0 - p)
            pf_mean[ia] += mu_a
            pf_mean[ib] += mu_b
            pf_var[ia] += (mu_a * std_fraction) ** 2
            pf_var[ib] += (mu_b * std_fraction) ** 2
        outcomes[ia].append(oa)
        outcomes[ib].append(ob)

    games = np.array([len(o) for o in outcomes])
    pmf = np.zeros((n, 2 * int(games.max(initial=0)) + 1))
    for i, outs in enumerate(outcomes):
        dist = np.array([1.0])
        for p_lose, p_tie, p_win in outs:      # Poisson-binomial DP, one game at a time
            nxt = np.zeros(len(dist) + 2)
            nxt[:-2] += dist * p_lose
            nxt[1:-1] += dist * p_tie
            nxt[2:] += dist * p_win
            dist = nxt
        pmf[i, :len(dist)] = dist
    return pmf, games, pf_mean, np.sqrt(pf_var)


# ------------------------------------------------------------------ seeding

def _seed_matrix(pmf: np.ndarray, pf_mean: np.ndarray, pf_sd: np.ndarray, team_ids: list) -> np.ndarray:
    """
    M[i, s] ~ P(team i finishes with seed s + 1).

    Conditional on team i finishing with h half-wins and f points-for, each
    other team j is ahead of it with probability
        q_j = P(H_j > h) + P(H_j == h) * P(PF_j > f)
    (season_records.seed_teams: wins first, points-for as tiebreak). Treating
    the other teams as independent, the number ahead of i is Poisson-binomial
    in the q_j - so P(seed = c + 1 | h, f) is the DP's count-c mass. f is
    integrated out against team i's (normal) points-for by Gauss-Hermite,
    h against its exact win pmf.
    """
    n, H = pmf.shape
    greater = 1.0 - np.cumsum(pmf, axis=1)                        # P(H_j > h)
    M = np.zeros((n, n))
    for i in range(n):
        f = pf_mean[i] + math.sqrt(2.0) * pf_sd[i] * _GH_X        # (K,) points-for nodes
        dp = np.zeros((n, H, len(f)))
        dp[0] = 1.0                                               # count of teams ahead = 0
        for j in range(n):
            if j == i:
                continue
            if pf_sd[j] > 0:
                pf_ahead = norm.sf((f - pf_mean[j]) / pf_sd[j])
            else:                                                 # known points-for; team_id breaks exact ties
                pf_ahead = ((pf_mean[j] > f) | ((pf_mean[j] == f) & (team_ids[j] < team_ids[i]))).astype(float)
            q = greater[j][:, None] + pmf[j][:, None] * pf_ahead[None, :]     # (H, K)
            shifted = np.zeros_like(dp)
            shifted[1:] = dp[:-1] * q
            dp = dp * (1.0 - q) + shifted
        M[i] = np.einsum("h,k,chk->c", pmf[i], _GH_W, dp)
    return M


# ------------------------------------------------------------------ bracket

def _bracket_places(M: np.ndarray, playoff_teams: int, first_week: int, playoff_weeks: int,
                    weekly_means: dict, team_ids: list, std_fraction: float) -> tuple:
    """
    Champion / runner-up / 3rd-place probability vectors (one entry per team).

    Mirrors season_records.project_playoffs + podium: fixed bracket
    (bracket_order), byes for missing seeds, one round per playoff week, 3rd
    place decided in the final week between the two semifinal losers.

    Every bracket node carries a vector: P(team i is the occupant of / winner
    of this node). Two sibling nodes only interact through independent scores,
    so a match is just
        wins_i  = a_i * sum_j W[i, j] * b_j  +  b_i * sum_j W[i, j] * a_j
    with W the round's pairwise win matrix. Slots are not really independent
    (one team can't sit in both subtrees), so for a team i in `a` the
    opponent distribution `b` is conditioned on excluding i: b_j / (1 - b_i).
    """
    n = len(team_ids)
    k = min(playoff_teams, n)
    zeros = np.zeros(n)
    if k <= 0:
        return zeros, zeros, zeros
    if k == 1:
        return M[:, 0].copy(), zeros, zeros

    size = 1
    while size < k:
        size *= 2
    n_rounds = size.bit_length() - 1
    if n_rounds > playoff_weeks:
        raise ValueError(
            f"{k} playoff teams needs {n_rounds} rounds, but only "
            f"{playoff_weeks} playoff weeks are configured."
        )

    def week_matrix(week):
        means = [weekly_means[t].get(week, 0.0) or 0.0 for t in team_ids]
        return _win_matrix(means, std_fraction)

    level = [M[:, s - 1] if s <= k else None for s in records.bracket_order(size)]
    losers_by_round = []
    for r in range(n_rounds):
        W = week_matrix(first_week + r)
        nxt, losers = [], []
        for a, b in zip(level[::2], level[1::2]):
            if a is None or b is None:                      # bye
                nxt.append(a if a is not None else b)
                continue
            with np.errstate(divide="ignore"):
                excl_b = np.where(b < 1 - 1e-12, 1.0 / (1.0 - b), 0.0)   # opponent-of-a-team-i distribution
                excl_a = np.where(a < 1 - 1e-12, 1.0 / (1.0 - a), 0.0)   # excludes i itself
            win = a * (W @ b) * excl_b + b * (W @ a) * excl_a
            lose = a * (W.T @ b) * excl_b + b * (W.T @ a) * excl_a
            win_total, lose_total = win.sum(), lose.sum()            # ~1; renormalise the small residual
            nxt.append(win / win_total if win_total > 0 else win)
            losers.append(lose / lose_total if lose_total > 0 else lose)
        losers_by_round.append(losers)
        level = nxt

    champion = level[0]
    second = losers_by_round[-1][0]

    third = zeros
    if n_rounds >= 2:
        semi_losers = losers_by_round[-2]
        if len(semi_losers) == 1:
            third = semi_losers[0]
        elif len(semi_losers) == 2:                         # 3rd-place game in the final week
            u, v = semi_losers
            W = week_matrix(first_week + n_rounds - 1)
            third = u * (W @ v) + v * (W @ u)
            total = third.sum()
            third = third / total if total > 0 else third
    return champion, second, third


# -------------------------------------------------------------- top level

def league_distribution(schedule: list, weekly_means: dict, regular_season_weeks: int,
                        playoff_teams: int, playoff_weeks: int,
                        std_fraction: float = DEFAULT_STD_FRACTION) -> dict:
    """
    The whole season-long outcome distribution, from stored projections alone.

    weekly_means: {team_id: {week: projected optimal-lineup points}} (weeks
    already played are overridden by the actual scores stored in `schedule`).

    Returns {team_id: {"avg_wins", "avg_losses", "avg_seed", "playoff_prob",
    "first", "second", "third", "seed_probs"}} where every probability is in
    [0, 1] and seed_probs[s] = P(finishing with seed s + 1).
    """
    team_ids = list(weekly_means)
    n = len(team_ids)
    if n == 0:
        return {}

    pmf, games, pf_mean, pf_sd = _regular_season_summary(
        schedule, weekly_means, team_ids, regular_season_weeks, std_fraction)
    half_wins = np.arange(pmf.shape[1])
    avg_wins = pmf @ half_wins / 2.0                        # exact
    M = _seed_matrix(pmf, pf_mean, pf_sd, team_ids)

    k = min(playoff_teams, n)
    champion, second, third = _bracket_places(
        M, playoff_teams, regular_season_weeks + 1, playoff_weeks, weekly_means, team_ids, std_fraction)

    seeds = np.arange(1, n + 1)
    out = {}
    for i, tid in enumerate(team_ids):
        out[tid] = {
            "avg_wins": float(avg_wins[i]),
            "avg_losses": float(games[i] - avg_wins[i]),
            "avg_seed": float(M[i] @ seeds),
            "playoff_prob": float(M[i, :k].sum()),
            "first": float(champion[i]),
            "second": float(second[i]),
            "third": float(third[i]),
            "seed_probs": M[i].tolist(),
        }
    return out


def project_league_probabilities(conn, league_key: str, end_week: int, regular_season_weeks: int,
                                 playoff_teams: int, playoff_weeks: int, as_of_week: int = None,
                                 std_fraction: float = DEFAULT_STD_FRACTION) -> dict:
    """
    Season-long odds for every team. See the module docstring for what is
    exact and what is approximate; there is no sampling, so results are
    deterministic and instant.

    Returns {"teams": [{"team_id", "team_name", "manager_name",
    "playoff_pct", "champion_pct", "avg_wins", "avg_losses", "avg_seed"}]
    sorted by championship odds descending, "method", "std_fraction"}.

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
    dist = league_distribution(schedule, weekly_means, regular_season_weeks, playoff_teams,
                               playoff_weeks, std_fraction)

    rows = []
    for tid, d in dist.items():
        rows.append({
            "team_id": tid,
            "team_name": sim[tid]["team_name"],
            "manager_name": sim[tid]["manager_name"],
            "playoff_pct": round(100 * d["playoff_prob"], 1),
            "champion_pct": round(100 * d["first"], 1),
            "avg_wins": round(d["avg_wins"], 2),
            "avg_losses": round(d["avg_losses"], 2),
            "avg_seed": round(d["avg_seed"], 2),
        })
    rows.sort(key=lambda r: (-r["champion_pct"], -r["playoff_pct"], r["avg_seed"]))
    return {"teams": rows, "method": "analytic", "std_fraction": std_fraction}


# ---------------------------------------------------------- weekly high score

def week_high_probabilities(scores: dict, grid_points: int = 4001) -> dict:
    """
    Each team's share of a "highest score this week" prize.

    scores: {team_id: (mean, sd)}. sd <= 0 means the score is known exactly
    (an actual result, or a zero-variance projection); otherwise the score is
    Normal(mean, sd), independent across teams.

    For an uncertain team i, P(i is the top score) is the 1-D integral
        integral pdf_i(x) * prod_{j != i} cdf_j(x) dx
    restricted to x above the best known score. A known team holding the top
    known score wins whenever no uncertain team beats it, and exact ties split
    the prize equally. Shares sum to 1.
    """
    out = {t: 0.0 for t in scores}
    known = {t: m for t, (m, s) in scores.items() if s <= 0}
    unknown = {t: (m, s) for t, (m, s) in scores.items() if s > 0}

    if not unknown:
        if known:
            top = max(known.values())
            winners = [t for t, v in known.items() if v == top]
            for t in winners:
                out[t] = 1.0 / len(winners)
        return out

    ids = list(unknown)
    mu = np.array([unknown[t][0] for t in ids])
    sd = np.array([unknown[t][1] for t in ids])
    floor = max(known.values()) if known else -math.inf

    lo = max(float((mu - 8 * sd).min()), floor)
    hi = float((mu + 8 * sd).max())
    if hi <= lo:                                            # every uncertain score is (numerically) below the known best
        top = max(known.values())
        winners = [t for t, v in known.items() if v == top]
        for t in winners:
            out[t] = 1.0 / len(winners)
        return out

    x = np.linspace(lo, hi, grid_points)
    z = (x[None, :] - mu[:, None]) / sd[:, None]
    cdf, pdf = norm.cdf(z), norm.pdf(z) / sd[:, None]

    ones = np.ones((1, len(x)))
    prefix = np.cumprod(np.vstack([ones, cdf[:-1]]), axis=0)                     # prod_{j < i}
    suffix = np.cumprod(np.vstack([ones, cdf[::-1][:-1]]), axis=0)[::-1]         # prod_{j > i}
    integrand = pdf * prefix * suffix
    share = ((integrand[:, 1:] + integrand[:, :-1]) * np.diff(x) / 2.0).sum(axis=1)
    for t, p in zip(ids, share):
        out[t] = float(p)

    if known:
        top = max(known.values())
        winners = [t for t, v in known.items() if v == top]
        p_no_upset = float(np.prod(norm.cdf((top - mu) / sd)))                   # nobody uncertain beats `top`
        for t in winners:
            out[t] = p_no_upset / len(winners)
    return out


# -------------------------------------------------------------- calibration

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