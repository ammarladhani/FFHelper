"""Brute-force Monte Carlo oracle: the OLD sampling logic, kept only to check the analytic code."""
import random
import numpy as np
import season_records as records

def monte_carlo(schedule, weekly_means, reg_weeks, k, playoff_weeks, std_fraction, n_sims, seed=0):
    rng = random.Random(seed)
    played = {(w, t) for w, a, b, sa, sb in schedule if sa is not None for t in (a, b)}
    ids = list(weekly_means)
    cnt = {t: dict(playoff=0, first=0, second=0, third=0, wins=0.0, seed=0.0) for t in ids}
    for _ in range(n_sims):
        sampled = {t: {w: (m if (w, t) in played or m <= 0 or std_fraction == 0
                            else max(0.0, rng.gauss(m, m * std_fraction)))
                       for w, m in weeks.items()} for t, weeks in weekly_means.items()}
        recs = records.project_records(schedule, sampled, reg_weeks)
        seeds = records.seed_teams(recs)
        po = records.project_playoffs(seeds[:k], sampled, reg_weeks + 1, playoff_weeks)
        pod = records.podium(po, sampled)
        for s, t in enumerate(seeds, 1):
            cnt[t]["seed"] += s
            if s <= k: cnt[t]["playoff"] += 1
        for key in ("first", "second", "third"):
            if pod[key] is not None: cnt[pod[key]][key] += 1
        for t, r in recs.items(): cnt[t]["wins"] += r["wins"] + 0.5 * r["ties"]
    return {t: {"playoff_prob": c["playoff"]/n_sims, "first": c["first"]/n_sims, "second": c["second"]/n_sims,
                "third": c["third"]/n_sims, "avg_wins": c["wins"]/n_sims, "avg_seed": c["seed"]/n_sims}
            for t, c in cnt.items()}