"""
Command-line interface.

Examples:
    python cli.py list-teams --league my_league
    python cli.py simulate --league my_league
    python cli.py simulate --league my_league --weighted --decay 0.85
    python cli.py waiver --league my_league --team 3 --weighted
    python cli.py trade-evaluate --league my_league --team-a 3 --give espn_4046692 --team-b 7 --get espn_4362628
    python cli.py trade-suggest --league my_league --team 3
    python cli.py records --league my_league
"""

import argparse
import payouts
import config
import db
import league_info
import season_records as records
import repo
import simulator
import waiver
import trades
import win_probability


def _decay(args):
    """The recency-weighting decay for this invocation: --decay's value if
    --weighted was passed, else None (weighting off)."""
    return args.decay if getattr(args, "weighted", False) else None


def _weighting_note(decay):
    if decay is None:
        return ""
    return (f"  [recency-weighted, decay={decay:g}/week: totals and gains are weighted "
            f"points; 'raw' is plain projected points]")


def cmd_list_teams(args, conn):
    teams = repo.get_teams(conn, args.league)
    for t in teams:
        print(f"team_id={t['team_id']:<4} name={t['team_name']:<25} manager={t['manager_name']}")


def cmd_simulate(args, conn):
    decay = _decay(args)
    results = simulator.simulate_all_teams(
        conn, args.league, args.start_week, args.end_week, args.as_of_week, decay=decay,
    )
    ranked = sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True)
    print(f"\nProjected remaining-season totals (weeks {args.start_week}-{args.end_week}):"
          f"{_weighting_note(decay)}\n")
    for team_id, r in ranked:
        if decay is None:
            print(f"  {r['total']:>8.1f}  {r['team_name']:<25} ({r['manager_name']})")
        else:
            print(f"  {r['total']:>8.1f} wtd  {r['raw_total']:>8.1f} raw  "
                  f"{r['team_name']:<25} ({r['manager_name']})")


def cmd_waiver(args, conn):
    decay = _decay(args)
    results = waiver.best_pickups(
        conn, args.league, args.team, args.start_week, args.end_week,
        as_of_week=args.as_of_week, top_n=1000,
        by_position=args.by_position, fa_per_position=args.fa_per_position,
        fa_prefilter=args.prefilter, decay=decay,
    )
    print(f"\nTop pickups for team_id={args.team} (weeks {args.start_week}-{args.end_week}):"
          f"{_weighting_note(decay)}\n")
    for r in results:
        add = r["add"]
        drop = r["drop"]
        add_str = f"{add['name']} ({add['player_id']})" if add else "?"
        drop_str = f"{drop['name']} ({drop['player_id']})" if drop else "?"
        raw_str = f" ({r['raw_gain']:+.1f} raw)" if decay is not None else ""
        print(f"  +{r['projected_gain']:>6.1f}{raw_str}  add {add_str:<32} drop {drop_str}")


def cmd_waiver_plan(args, conn):
    decay = _decay(args)
    plan = waiver.plan_waiver_moves(
        conn, args.league, args.team, args.start_week, args.end_week,
        as_of_week=args.as_of_week, fa_prefilter=args.prefilter,
        by_position=args.by_position, fa_per_position=args.fa_per_position,
        max_moves=args.max_moves, decay=decay,
    )
    print(f"\nWaiver plan for team_id={args.team} (weeks {args.start_week}-{args.end_week}):"
          f"{_weighting_note(decay)}")
    print(f"  Starting total: {plan['starting_total']:.1f}")
    if not plan["moves"]:
        print("  No move found that improves your projected total.")
    for m in plan["moves"]:
        add, drop = m["add"], m["drop"]
        raw_str = f" ({m['raw_gain']:+.1f} raw)" if decay is not None else ""
        print(f"  Step {m['step']}: +{m['gain']:>6.1f}{raw_str}  add {add['name']:<28} "
              f"drop {drop['name']:<28} -> running total {m['running_total']:.1f}")
    raw_total = f", {plan['total_raw_gain']:+.1f} raw" if decay is not None else ""
    print(f"  Final total: {plan['final_total']:.1f}  (total gain {plan['total_gain']:+.1f}{raw_total})")


def cmd_trade_evaluate(args, conn):
    decay = _decay(args)
    result = trades.evaluate_trade(
        conn, args.league, args.team_a, args.give, args.team_b, args.get,
        args.start_week, args.end_week, args.as_of_week, decay=decay,
    )
    a, b = result["team_a"], result["team_b"]
    print(_weighting_note(decay))
    print(f"\nTeam A (id={a['team_id']}): {a['before']:.1f} -> {a['after']:.1f}  (delta {a['delta']:+.1f})")
    print(f"Team B (id={b['team_id']}): {b['before']:.1f} -> {b['after']:.1f}  (delta {b['delta']:+.1f})")
    if decay is not None:
        print(f"  raw: Team A {a['raw_delta']:+.1f}, Team B {b['raw_delta']:+.1f}")


def cmd_trade_suggest(args, conn):
    decay = _decay(args)
    all_proposals = trades.suggest_trades(
        conn, args.league, args.team, args.start_week, args.end_week,
        as_of_week=args.as_of_week, candidate_prefilter=args.prefilter,
        partner_team_id=args.partner_team, decay=decay,
    )
    proposals = all_proposals[:500]

    header = f"\nWin-win trade suggestions for team_id={args.team}"
    if args.partner_team is not None:
        header += f" (vs team_id={args.partner_team})"
    print(header + f" (showing {len(proposals)} of {len(all_proposals)} found):"
          f"{_weighting_note(decay)}\n")
    for p in proposals:
        give_str = ", ".join(f"{x['name']} ({x['player_id']})" for x in p["give"])
        get_str = ", ".join(f"{x['name']} ({x['player_id']})" for x in p["get"])
        raw_str = ""
        if decay is not None:
            raw_str = f" [raw: you {p['my_raw_delta']:+.1f}, them {p['partner_raw_delta']:+.1f}]"
        print(f"  Give [{give_str}]  ->  Get [{get_str}]  "
              f"(you: {p['my_delta']:+.1f}, {p['partner_team_name']}: {p['partner_delta']:+.1f}){raw_str}")

    if all_proposals:
        give_counts = {}
        get_counts = {}
        for p in all_proposals:
            for x in p["give"]:
                give_counts[x["name"]] = give_counts.get(x["name"], 0) + 1
            for x in p["get"]:
                get_counts[x["name"]] = get_counts.get(x["name"], 0) + 1

        print(f"\nMost frequently traded AWAY (across all {len(all_proposals)} win-win trades found):")
        for name, count in sorted(give_counts.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {count:>3}x  {name}")

        print(f"\nMost frequently RECEIVED (across all {len(all_proposals)} win-win trades found):")
        for name, count in sorted(get_counts.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {count:>3}x  {name}")


def print_weekly_table(title, weekly_before, weekly_after, weights=None):
    """Weekly before/after projected points. If `weights` (a {week: weight}
    dict) contains anything other than 1.0, also shows each week's weight
    and the weighted totals."""
    print(f"\n{title}")
    weeks = sorted(weekly_before.keys())
    weighted = weights is not None and any(w != 1.0 for w in weights.values())

    header = f"  {'Week':<6}{'Before':>10}{'After':>10}{'Diff':>10}"
    if weighted:
        header += f"{'Weight':>10}"
    print(header)
    for w in weeks:
        b = weekly_before[w]
        a = weekly_after.get(w, 0.0)
        line = f"  {w:<6}{b:>10.1f}{a:>10.1f}{(a - b):>+10.1f}"
        if weighted:
            line += f"{weights[w]:>10.2f}"
        print(line)
    total_b = sum(weekly_before.values())
    total_a = sum(weekly_after.values())
    print(f"  {'-' * (46 if weighted else 36)}")
    print(f"  {'Total':<6}{total_b:>10.1f}{total_a:>10.1f}{(total_a - total_b):>+10.1f}")
    if weighted:
        wb = sum(weights[w] * weekly_before[w] for w in weeks)
        wa = sum(weights[w] * weekly_after.get(w, 0.0) for w in weeks)
        print(f"  {'Wtd':<6}{wb:>10.1f}{wa:>10.1f}{(wa - wb):>+10.1f}")


def cmd_waiver_why(args, conn):
    decay = _decay(args)
    result = waiver.explain_pickup(
        conn, args.league, args.team, args.add, args.drop,
        args.start_week, args.end_week, args.as_of_week, decay=decay,
    )
    add_name = result["add"]["name"] if result["add"] else "?"
    drop_name = result["drop"]["name"] if result["drop"] else "?"
    print_weekly_table(f"Add {add_name} / Drop {drop_name} (team_id={args.team})",
                        result["weekly_before"], result["weekly_after"], result["weights"])


def cmd_trade_why(args, conn):
    decay = _decay(args)
    result = trades.explain_trade(
        conn, args.league, args.team_a, args.give, args.team_b, args.get,
        args.start_week, args.end_week, args.as_of_week, decay=decay,
    )
    a, b = result["team_a"], result["team_b"]
    print_weekly_table(f"Team A (id={a['team_id']})", a["weekly_before"], a["weekly_after"], a["weights"])
    print_weekly_table(f"Team B (id={b['team_id']})", b["weekly_before"], b["weekly_after"], b["weights"])


def cmd_records(args, conn):
    try:
        ps = league_info.playoff_settings(args.league)
        as_of = args.as_of_week or league_info.current_week(last_week=ps["end_week"])
        result = records.project_league(
            conn, args.league,
            end_week=ps["end_week"], regular_season_weeks=ps["regular_season_weeks"],
            playoff_teams=ps["playoff_teams"], playoff_weeks=ps["playoff_weeks"],
            as_of_week=as_of,
        )
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    reg = ps["regular_season_weeks"]
    print(f"\nProjected records for {args.league} - regular season weeks 1-{reg}, "
          f"rosters as of week {as_of}.")
    print("Weeks already played use actual scores; the rest use projected lineups "
          "(higher projected score wins).\n")
    print(f"  {'Seed':<5}{'Record':<9}{'PF':>9}{'PA':>9}  Team")
    cut_printed = False
    for row in result["teams"]:
        if not row["made_playoffs"] and not cut_printed:
            print(f"  {'-' * 12} playoff cut ({ps['playoff_teams']} teams) {'-' * 12}")
            cut_printed = True
        champ = "  <- projected champion" if row["is_champion"] else ""
        print(f"  {row['seed']:<5}{records.record_str(row):<9}{row['points_for']:>9.1f}"
              f"{row['points_against']:>9.1f}  {row['team_name']} ({row['manager_name']}){champ}")

    names = {r["team_id"]: r["team_name"] for r in result["teams"]}
    seed_of = {r["team_id"]: r["seed"] for r in result["teams"]}
    print("\nProjected playoffs:")
    for n, rnd in enumerate(result["playoffs"]["rounds"], start=1):
        print(f"  Round {n} (week {rnd['week']})")
        for tid in rnd["byes"]:
            print(f"    #{seed_of[tid]} {names[tid]}: bye")
        for m in rnd["matchups"]:
            print(f"    #{m['seed_a']} {names[m['team_a']]} {m['score_a']:.1f}  vs  "
                  f"#{m['seed_b']} {names[m['team_b']]} {m['score_b']:.1f}  ->  {names[m['winner']]}")
    if result["champion"]:
        print(f"\n  Projected champion: {result['champion']['team_name']} "
              f"({result['champion']['manager_name']})")


def cmd_odds(args, conn):
    try:
        ps = league_info.playoff_settings(args.league)
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    as_of = args.as_of_week or league_info.current_week(last_week=ps["end_week"])
    week = args.week or as_of

    schedule = repo.get_schedule(conn, args.league)
    sim = simulator.simulate_all_teams(conn, args.league, 1, ps["end_week"], as_of_week=as_of)
    weekly_means = {tid: r["weekly"] for tid, r in sim.items()}
    names = {tid: r["team_name"] for tid, r in sim.items()}

    matchups = win_probability.week_matchup_probabilities(schedule, weekly_means, week, args.std_fraction)
    print(f"\nWin probabilities for week {week} (rosters as of week {as_of}, "
          f"score stdev={args.std_fraction:.0%} of projection):\n")
    if not matchups:
        print("  No unplayed matchups found for this week (already played, past the regular "
              "season, or no schedule stored).")
    for m in matchups:
        print(f"  {names[m['team_a']]:<25} {m['win_prob_a'] * 100:5.1f}%   vs   "
              f"{m['win_prob_b'] * 100:5.1f}%  {names[m['team_b']]:<25}")

    try:
        result = win_probability.project_league_probabilities( conn, args.league, ps["end_week"], ps["regular_season_weeks"], ps["playoff_teams"], ps["playoff_weeks"], as_of_week=as_of, std_fraction=args.std_fraction, )
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    print(f"\nSeason-long odds:\n")
    print(f"  {'Team':<25}{'Playoffs%':>11}{'Champ%':>9}{'Avg record':>13}{'Avg seed':>10}")
    for r in result["teams"]:
        avg_record = f"{r['avg_wins']:.1f}-{r['avg_losses']:.1f}"
        print(f"  {r['team_name']:<25}{r['playoff_pct']:>10.1f}%{r['champion_pct']:>8.1f}%"
              f"{avg_record:>13}{r['avg_seed']:>10.2f}")

def cmd_payouts(args, conn):
    try:
        ps = league_info.playoff_settings(args.league)
        as_of = args.as_of_week or league_info.current_week(last_week=ps["end_week"])
        result = payouts.expected_payouts(conn, args.league, as_of_week=as_of, std_fraction=args.std_fraction)
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    s = result["summary"]
    print(f"\nExpected payouts for {args.league} (buy-in ${s['buy_in']:g}, " f"weekly-high weeks settled: {s['weekly_high_weeks_paid']}/{s['weekly_high_weeks_total']}):\n")
    print(f"  {'Team':<25}{'Earned':>9}{'ROS EV':>9}{'Total EV':>10}{'Net EV':>9}{'1st%':>7}{'2nd%':>7}{'3rd%':>7}")
    for r in result["teams"]:
        print(f"  {r['team_name']:<25}{r['earned']:>9.2f}{r['expected_remaining']:>9.2f}"
              f"{r['expected_total']:>10.2f}{r['expected_net']:>+9.2f}"
              f"{r['first_pct']:>7.1f}{r['second_pct']:>7.1f}{r['third_pct']:>7.1f}")
    if abs(s["total_prizes"] - s["total_buy_ins"]) > 0.005:
        print(f"\n  Note: prizes total ${s['total_prizes']:g} but buy-ins total "
              f"${s['total_buy_ins']:g} ({s['n_teams']} teams x ${s['buy_in']:g}) - check your payout config.")


def cmd_calibrate(args, conn):
    try:
        result = win_probability.calibrate_std_fraction(conn, args.league, min_games=args.min_games)
    except ValueError as e:
        raise SystemExit(f"error: {e}")

    if result["bias"] > 0.02:
        bias_note = "actual scores have been running ABOVE projections"
    elif result["bias"] < -0.02:
        bias_note = "actual scores have been running BELOW projections"
    else:
        bias_note = "no meaningful systematic bias so far"

    print(f"\nCalibrating uncertainty for '{args.league}' from {result['n_games']} played team-weeks:\n")
    print(f"  Suggested std_fraction: {result['std_fraction']:.3f}  ({result['std_fraction'] * 100:.1f}%)")
    print(f"  Bias: {result['bias'] * 100:+.1f}%  ({bias_note})")
    print(f"\n  Use it with: python cli.py odds --league {args.league} --std-fraction {result['std_fraction']:.2f}")
    print("\n  Note: this compares actual scores to what the OPTIMAL lineup would have projected, "
          "not necessarily what was actually started, so it can run a bit high if managers don't "
          "always start their best lineup. It's also only as reliable as the number of played "
          "weeks behind it - treat it skeptically early in the season.")


def build_parser():
    parser = argparse.ArgumentParser(description="Fantasy football optimizer")
    parser.add_argument("--db", default=config.DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--league", required=True, help="league key from config.py")
        p.add_argument("--start-week", type=int, dest="start_week", default=config.START_WEEK)
        p.add_argument("--end-week", type=int, dest="end_week", default=None,
                       help="last week to project through (default: this league's end_week from config.py)")
        p.add_argument("--as-of-week", type=int, dest="as_of_week", default=None)
        p.add_argument("--weighted", action="store_true",
                       help="rank/sort by recency-weighted projected points (nearer weeks count "
                            "more) instead of a plain sum")
        p.add_argument("--decay", type=float, default=config.DEFAULT_DECAY,
                       help=f"per-week decay for --weighted, in (0, 1] (default {config.DEFAULT_DECAY}: "
                            f"each week is worth that fraction of the week before it)")

    p = sub.add_parser("list-teams")
    p.add_argument("--league", required=True)
    p.set_defaults(func=cmd_list_teams)

    p = sub.add_parser("simulate")
    add_common(p)
    p.set_defaults(func=cmd_simulate)

    p = sub.add_parser("waiver")
    add_common(p)
    p.add_argument("--team", type=int, required=True)
    p.add_argument("--top-n", type=int, dest="top_n", default=10)
    p.add_argument("--prefilter", type=int, default=waiver.DEFAULT_FA_PREFILTER,
                   help="free agents considered overall (ignored if --by-position)")
    p.add_argument("--by-position", action="store_true",
                   help="prefilter free agents per-position instead of one global ranked list")
    p.add_argument("--fa-per-position", type=int, dest="fa_per_position",
                   default=waiver.DEFAULT_FA_PER_POSITION,
                   help="free agents considered per position, only used with --by-position")
    p.set_defaults(func=cmd_waiver)

    p = sub.add_parser("waiver-plan")
    add_common(p)
    p.add_argument("--team", type=int, required=True)
    p.add_argument("--prefilter", type=int, default=waiver.DEFAULT_FA_PREFILTER)
    p.add_argument("--by-position", action="store_true")
    p.add_argument("--fa-per-position", type=int, dest="fa_per_position",
                   default=waiver.DEFAULT_FA_PER_POSITION)
    p.add_argument("--max-moves", type=int, dest="max_moves", default=50,
                   help="safety cap on chained moves - the search stops earlier on its own once no move helps")
    p.set_defaults(func=cmd_waiver_plan)

    p = sub.add_parser("waiver-why")
    add_common(p)
    p.add_argument("--team", type=int, required=True)
    p.add_argument("--add", type=str, required=True, help="player_id to add")
    p.add_argument("--drop", type=str, required=True, help="player_id to drop")
    p.set_defaults(func=cmd_waiver_why)

    p = sub.add_parser("trade-evaluate")
    add_common(p)
    p.add_argument("--team-a", type=int, required=True, dest="team_a")
    p.add_argument("--give", type=str, nargs="+", required=True, help="player_id(s) team A gives up")
    p.add_argument("--team-b", type=int, required=True, dest="team_b")
    p.add_argument("--get", type=str, nargs="+", required=True, help="player_id(s) team A receives")
    p.set_defaults(func=cmd_trade_evaluate)

    p = sub.add_parser("trade-why")
    add_common(p)
    p.add_argument("--team-a", type=int, required=True, dest="team_a")
    p.add_argument("--give", type=str, nargs="+", required=True, help="player_id(s) team A gives up")
    p.add_argument("--team-b", type=int, required=True, dest="team_b")
    p.add_argument("--get", type=str, nargs="+", required=True, help="player_id(s) team A receives")
    p.set_defaults(func=cmd_trade_why)

    p = sub.add_parser("trade-suggest")
    add_common(p)
    p.add_argument("--team", type=int, required=True)
    p.add_argument("--partner-team", type=int, dest="partner_team", default=None,
                   help="restrict search to this one other team_id instead of the whole league")
    p.add_argument("--top-n", type=int, dest="top_n", default=10)
    p.add_argument("--prefilter", type=int, default=12, help="candidates per roster side before combinatorics")
    p.set_defaults(func=cmd_trade_suggest)

    p = sub.add_parser("records", help="projected W-L records, playoff seeds and champion")
    p.add_argument("--league", required=True, help="league key from config.py")
    p.add_argument("--as-of-week", type=int, dest="as_of_week", default=None,
                   help="project forward from this week's rosters (default: the current week)")
    p.set_defaults(func=cmd_records)

    p = sub.add_parser("odds", help="matchup win probabilities and season-long championship odds")
    p.add_argument("--league", required=True, help="league key from config.py")
    p.add_argument("--as-of-week", type=int, dest="as_of_week", default=None,
                   help="project forward from this week's rosters (default: the current week)")
    p.add_argument("--week", type=int, default=None,
                   help="which week's matchup odds to show (default: the current/as-of week)")
    p.add_argument("--std-fraction", type=float, dest="std_fraction",
                   default=config.DEFAULT_SCORE_STD_FRACTION,
                   help=f"weekly score stdev as a fraction of the projection "
                        f"(default {config.DEFAULT_SCORE_STD_FRACTION:g} - see win_probability.py "
                        f"for what this assumes)")
    p.set_defaults(func=cmd_odds)

    p = sub.add_parser("calibrate", help="estimate a data-driven --std-fraction from this league's own "
                                          "actual-vs-projected history")
    p.add_argument("--league", required=True, help="league key from config.py")
    p.add_argument("--min-games", type=int, dest="min_games", default=8,
                   help="minimum played team-weeks required before trusting the estimate (default 8)")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("payouts", help="expected prize money: already earned + expected rest of season")
    p.add_argument("--league", required=True, help="league key from config.py")
    p.add_argument("--as-of-week", type=int, dest="as_of_week", default=None)
    p.add_argument("--std-fraction", type=float, dest="std_fraction",
                   default=config.DEFAULT_SCORE_STD_FRACTION)
    p.set_defaults(func=cmd_payouts)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    # Each league has its own last week (config.py "end_week"), so the default
    # can only be filled in once we know which league was asked about.
    if getattr(args, "end_week", "unset") is None:
        args.end_week = league_info.league_end_week(args.league)
    conn = db.get_conn(args.db)
    args.func(args, conn)
    conn.close()


if __name__ == "__main__":
    main()