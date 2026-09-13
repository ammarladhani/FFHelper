"""
Command-line interface.

Examples:
    python cli.py list-teams --league my_league
    python cli.py simulate --league my_league
    python cli.py waiver --league my_league --team 3
    python cli.py trade-evaluate --league my_league --team-a 3 --give 4046692 --team-b 7 --get 4362628
    python cli.py trade-suggest --league my_league --team 3
"""

import argparse

import config
import db
import repo
import simulator
import waiver
import trades


def cmd_list_teams(args, conn):
    teams = repo.get_teams(conn, args.league)
    for t in teams:
        print(f"team_id={t['team_id']:<4} name={t['team_name']:<25} manager={t['manager_name']}")


def cmd_simulate(args, conn):
    results = simulator.simulate_all_teams(conn, args.league, args.start_week, args.end_week, args.as_of_week)
    ranked = sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True)
    print(f"\nProjected remaining-season totals (weeks {args.start_week}-{args.end_week}):\n")
    for team_id, r in ranked:
        print(f"  {r['total']:>8.1f}  {r['team_name']:<25} ({r['manager_name']})")


def cmd_waiver(args, conn):
    results = waiver.best_pickups(
        conn, args.league, args.team, args.start_week, args.end_week,
        as_of_week=args.as_of_week, top_n=1000,
    )
    print(f"\nTop pickups for team_id={args.team} (weeks {args.start_week}-{args.end_week}):\n")
    for r in results:
        add = r["add"]
        drop = r["drop"]
        add_str = f"{add['name']} ({add['player_id']})" if add else "?"
        drop_str = f"{drop['name']} ({drop['player_id']})" if drop else "?"
        print(f"  +{r['projected_gain']:>6.1f}  add {add_str:<32} drop {drop_str}")


def cmd_trade_evaluate(args, conn):
    result = trades.evaluate_trade(
        conn, args.league, args.team_a, args.give, args.team_b, args.get,
        args.start_week, args.end_week, args.as_of_week,
    )
    a, b = result["team_a"], result["team_b"]
    print(f"\nTeam A (id={a['team_id']}): {a['before']:.1f} -> {a['after']:.1f}  (delta {a['delta']:+.1f})")
    print(f"Team B (id={b['team_id']}): {b['before']:.1f} -> {b['after']:.1f}  (delta {b['delta']:+.1f})")


def cmd_trade_suggest(args, conn):
    proposals = trades.suggest_trades(
        conn, args.league, args.team, args.start_week, args.end_week,
        as_of_week=args.as_of_week, top_n=args.top_n, candidate_prefilter=args.prefilter,
    )
    print(f"\nWin-win trade suggestions for team_id={args.team}:\n")
    for p in proposals:
        give_str = ", ".join(f"{x['name']} ({x['player_id']})" for x in p["give"])
        get_str = ", ".join(f"{x['name']} ({x['player_id']})" for x in p["get"])
        print(f"  Give [{give_str}]  ->  Get [{get_str}]  "
              f"(you: {p['my_delta']:+.1f}, {p['partner_team_name']}: {p['partner_delta']:+.1f})")


def print_weekly_table(title, weekly_before, weekly_after):
    print(f"\n{title}")
    weeks = sorted(weekly_before.keys())
    print(f"  {'Week':<6}{'Before':>10}{'After':>10}{'Diff':>10}")
    for w in weeks:
        b = weekly_before[w]
        a = weekly_after.get(w, 0.0)
        print(f"  {w:<6}{b:>10.1f}{a:>10.1f}{(a - b):>+10.1f}")
    total_b = sum(weekly_before.values())
    total_a = sum(weekly_after.values())
    print(f"  {'-' * 36}")
    print(f"  {'Total':<6}{total_b:>10.1f}{total_a:>10.1f}{(total_a - total_b):>+10.1f}")


def cmd_waiver_why(args, conn):
    result = waiver.explain_pickup(
        conn, args.league, args.team, args.add, args.drop,
        args.start_week, args.end_week, args.as_of_week,
    )
    add_name = result["add"]["name"] if result["add"] else "?"
    drop_name = result["drop"]["name"] if result["drop"] else "?"
    print_weekly_table(f"Add {add_name} / Drop {drop_name} (team_id={args.team})",
                        result["weekly_before"], result["weekly_after"])


def cmd_trade_why(args, conn):
    result = trades.explain_trade(
        conn, args.league, args.team_a, args.give, args.team_b, args.get,
        args.start_week, args.end_week, args.as_of_week,
    )
    a, b = result["team_a"], result["team_b"]
    print_weekly_table(f"Team A (id={a['team_id']})", a["weekly_before"], a["weekly_after"])
    print_weekly_table(f"Team B (id={b['team_id']})", b["weekly_before"], b["weekly_after"])


def build_parser():
    parser = argparse.ArgumentParser(description="Fantasy football optimizer")
    parser.add_argument("--db", default=config.DB_PATH)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p):
        p.add_argument("--league", required=True, help="league key from config.py")
        p.add_argument("--start-week", type=int, dest="start_week", default=config.START_WEEK)
        p.add_argument("--end-week", type=int, dest="end_week", default=config.END_WEEK)
        p.add_argument("--as-of-week", type=int, dest="as_of_week", default=None)

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
    p.set_defaults(func=cmd_waiver)

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
    p.add_argument("--top-n", type=int, dest="top_n", default=10)
    p.add_argument("--prefilter", type=int, default=12, help="candidates per roster side before combinatorics")
    p.set_defaults(func=cmd_trade_suggest)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    conn = db.get_conn(args.db)
    args.func(args, conn)
    conn.close()


if __name__ == "__main__":
    main()