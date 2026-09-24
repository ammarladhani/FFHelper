"""
Tests for the analytic win-probability / payout math (win_probability.py).

Three kinds of check:
  * EXACT things are checked against brute force: game-outcome enumeration
    for win totals, and the zero-variance limit (where the analytic answer
    must collapse onto season_records' deterministic bracket exactly).
  * APPROXIMATE things (seed distribution, bracket) are checked against a
    seeded Monte Carlo oracle (tests/oracle.py - the old sampling logic,
    kept only for this). Tolerances are set from measured error (see the
    win_probability module docstring), with headroom; the oracle is seeded
    so these tests are deterministic, not flaky.
  * Structural invariants (probabilities sum where they must).

Run with:
    python -m pytest tests/test_analytic_odds.py -v
"""

import itertools
import random
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import season_records as records
import win_probability as wp
from oracle import monte_carlo


def make_league(n_teams, reg_weeks, playoff_weeks, seed, played_weeks=0, spread=12.0):
    """Random league: (schedule, weekly_means). Weeks <= played_weeks carry actual scores."""
    rng = random.Random(seed)
    ids = list(range(1, n_teams + 1))
    base = {t: rng.gauss(100, spread) for t in ids}
    weekly = {t: {w: max(20.0, base[t] + rng.gauss(0, 4)) for w in range(1, reg_weeks + playoff_weeks + 1)}
              for t in ids}
    schedule = []
    for w in range(1, reg_weeks + 1):
        order = ids[:]
        rng.shuffle(order)
        for a, b in zip(order[::2], order[1::2]):
            if w <= played_weeks:
                schedule.append((w, a, b, round(weekly[a][w] * (1 + rng.gauss(0, .2)), 2),
                                 round(weekly[b][w] * (1 + rng.gauss(0, .2)), 2)))
            else:
                schedule.append((w, a, b, None, None))
    return schedule, weekly


# ------------------------------------------------------------ exact pieces

def test_win_matrix_matches_scalar_formula():
    means = [120.0, 100.0, 100.0, 0.0]
    W = wp._win_matrix(means, 0.2)
    for i, j in itertools.permutations(range(4), 2):
        expected = wp.matchup_win_probability(means[i], means[j], means[i] * .2, means[j] * .2)
        assert W[i, j] == pytest.approx(expected)
    assert np.all(np.diag(W) == 0)


def test_win_distribution_matches_outcome_enumeration():
    """4 teams x 3 weeks = 6 games: enumerate all 2^6 outcomes and compare the
    Poisson-binomial DP (and expected wins) exactly."""
    schedule, weekly = make_league(4, 3, 1, seed=5)
    ids = list(weekly)
    pmf, games, _pf_mean, _pf_sd = wp._regular_season_summary(schedule, weekly, ids, 3, 0.25)

    game_p = [wp.matchup_win_probability(weekly[a][w], weekly[b][w], weekly[a][w] * .25, weekly[b][w] * .25)
              for w, a, b, _sa, _sb in schedule]
    brute = np.zeros_like(pmf)
    for outcome in itertools.product((0, 1), repeat=len(schedule)):      # 1 = first-listed team wins
        prob = np.prod([p if o else 1 - p for p, o in zip(game_p, outcome)])
        wins = {t: 0 for t in ids}
        for (w, a, b, _sa, _sb), o in zip(schedule, outcome):
            wins[a if o else b] += 1
        for i, t in enumerate(ids):
            brute[i, 2 * wins[t]] += prob                                # half-win units
    assert pmf == pytest.approx(brute, abs=1e-12)


def test_played_games_are_fixed_terms():
    schedule = [(1, 1, 2, 90.0, 80.0), (2, 1, 2, None, None)]
    weekly = {1: {1: 0.0, 2: 100.0}, 2: {1: 0.0, 2: 100.0}}
    d = wp.league_distribution(schedule, weekly, 2, 2, 1, 0.2)
    assert d[1]["avg_wins"] == pytest.approx(1.5)      # one certain win + a coin flip
    assert d[2]["avg_wins"] == pytest.approx(0.5)


@pytest.mark.parametrize("scores, expected", [
    ({1: (10.0, 0.0), 2: (12.0, 0.0)}, {1: 0.0, 2: 1.0}),                       # all known
    ({1: (110.0, 0.0), 2: (110.0, 0.0), 3: (0.0, 0.0)}, {1: .5, 2: .5, 3: 0.0}),  # known tie splits
    ({1: (100.0, 20.0), 2: (100.0, 20.0)}, {1: .5, 2: .5}),                      # symmetric uncertain
])
def test_week_high_special_cases(scores, expected):
    got = wp.week_high_probabilities(scores)
    for t, p in expected.items():
        assert got[t] == pytest.approx(p, abs=1e-6)


def test_week_high_known_leader_and_sum_to_one():
    scores = {1: (300.0, 0.0), 2: (100.0, 20.0), 3: (90.0, 18.0)}
    assert wp.week_high_probabilities(scores)[1] == pytest.approx(1.0)
    mixed = {1: (120.0, 24.0), 2: (110.0, 22.0), 3: (118.0, 0.0), 4: (60.0, 0.0), 5: (95.0, 19.0)}
    got = wp.week_high_probabilities(mixed)
    assert sum(got.values()) == pytest.approx(1.0, abs=1e-6)
    assert got[4] == 0.0
    # sanity vs sampling
    rng = np.random.default_rng(0)
    draws = {t: (rng.normal(m, s, 200_000) if s else np.full(200_000, m)) for t, (m, s) in mixed.items()}
    stack = np.array([draws[t] for t in mixed])
    winners = stack.argmax(axis=0)
    for r, t in enumerate(mixed):
        assert got[t] == pytest.approx(np.mean(winners == r), abs=0.005)


# -------------------------------------------------- zero-variance collapse

@pytest.mark.parametrize("n_teams, playoff_teams, playoff_weeks", [
    (8, 1, 1), (8, 2, 1), (8, 3, 2), (8, 4, 2), (8, 5, 3), (8, 6, 3), (8, 8, 3), (10, 6, 3),
])
def test_zero_variance_equals_deterministic_bracket(n_teams, playoff_teams, playoff_weeks):
    """With std_fraction=0 there is nothing left to be uncertain about, so the
    analytic answer must be exactly season_records' deterministic result -
    every bracket shape (byes, 3rd-place game, one-team playoff, ...)."""
    reg = 10
    schedule, weekly = make_league(n_teams, reg, playoff_weeks, seed=n_teams * 10 + playoff_teams)
    dist = wp.league_distribution(schedule, weekly, reg, playoff_teams, playoff_weeks, std_fraction=0.0)

    recs = records.project_records(schedule, weekly, reg)
    seeds = records.seed_teams(recs)
    playoffs = records.project_playoffs(seeds[:playoff_teams], weekly, reg + 1, playoff_weeks)
    podium = records.podium(playoffs, weekly)

    for tid, d in dist.items():
        assert d["playoff_prob"] == pytest.approx(1.0 if tid in seeds[:playoff_teams] else 0.0, abs=1e-6)
        assert d["avg_seed"] == pytest.approx(seeds.index(tid) + 1, abs=1e-6)
        assert d["avg_wins"] == pytest.approx(recs[tid]["wins"] + 0.5 * recs[tid]["ties"])
        for key in ("first", "second", "third"):
            assert d[key] == pytest.approx(1.0 if podium[key] == tid else 0.0, abs=1e-6), (tid, key)


# ------------------------------------------------------------- invariants

@pytest.mark.parametrize("n_teams, playoff_teams, played", [(12, 6, 0), (12, 6, 6), (10, 8, 3), (8, 4, 0), (6, 3, 2)])
def test_structural_invariants(n_teams, playoff_teams, played):
    reg, po_w = 13, 3
    schedule, weekly = make_league(n_teams, reg, po_w, seed=3, played_weeks=played)
    dist = wp.league_distribution(schedule, weekly, reg, playoff_teams, po_w, 0.2)

    M = np.array([d["seed_probs"] for d in dist.values()])
    assert M.sum(axis=1) == pytest.approx(np.ones(n_teams))          # every team gets exactly one seed
    assert M.sum(axis=0) == pytest.approx(np.ones(n_teams), abs=1e-6)  # every seed goes to exactly one team
    assert sum(d["playoff_prob"] for d in dist.values()) == pytest.approx(playoff_teams)
    assert sum(d["first"] for d in dist.values()) == pytest.approx(1.0)
    assert sum(d["second"] for d in dist.values()) == pytest.approx(1.0)
    if playoff_teams >= 3:
        assert sum(d["third"] for d in dist.values()) == pytest.approx(1.0)
    for d in dist.values():
        assert 0.0 <= d["first"] <= d["playoff_prob"] + 1e-9        # can't win it without qualifying
        assert d["avg_wins"] + d["avg_losses"] == pytest.approx(13.0)


def test_stronger_team_is_favoured():
    schedule, weekly = make_league(8, 13, 2, seed=9)
    dist = wp.league_distribution(schedule, weekly, 13, 4, 2, 0.2)
    strength = {t: np.mean(list(w.values())) for t, w in weekly.items()}
    best, worst = max(strength, key=strength.get), min(strength, key=strength.get)
    assert dist[best]["first"] > dist[worst]["first"]
    assert dist[best]["playoff_prob"] > dist[worst]["playoff_prob"]


def test_too_few_playoff_weeks_raises():
    schedule, weekly = make_league(8, 10, 2, seed=1)
    with pytest.raises(ValueError, match="needs 3 rounds"):
        wp.league_distribution(schedule, weekly, 10, 6, 2, 0.2)


# ------------------------------------------------- against Monte Carlo oracle

@pytest.mark.parametrize("n_teams, reg, po_w, k, played", [
    (12, 13, 3, 6, 0),
    (10, 14, 3, 8, 5),
    (8, 13, 2, 4, 2),
])
def test_matches_monte_carlo_oracle(n_teams, reg, po_w, k, played):
    """The approximate parts, measured. At 8000 sims the oracle's own sampling
    error is ~1 point; the analytic model's known bias is up to ~2.5 (playoff
    odds) and ~1.5 (title / podium) - see win_probability's docstring."""
    schedule, weekly = make_league(n_teams, reg, po_w, seed=1, played_weeks=played)
    analytic = wp.league_distribution(schedule, weekly, reg, k, po_w, 0.2)
    oracle = monte_carlo(schedule, weekly, reg, k, po_w, 0.2, n_sims=8000, seed=11)

    for key, tol in (("playoff_prob", 0.045), ("first", 0.035), ("second", 0.045), ("third", 0.045)):
        worst = max(abs(analytic[t][key] - oracle[t][key]) for t in analytic)
        assert worst < tol, f"{key}: worst abs error {worst:.3f} >= {tol}"
    assert max(abs(analytic[t]["avg_wins"] - oracle[t]["avg_wins"]) for t in analytic) < 0.06   # exact vs MC noise
    assert max(abs(analytic[t]["avg_seed"] - oracle[t]["avg_seed"]) for t in analytic) < 0.25