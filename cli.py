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
        as_of_week=args.as_of_week, top_n=args.top_n,
    )
    print(f"\nTop pickups for team_id={args.team} (weeks {args.start_week}-{args.end_week}):\n")
    for r in results:
        add_name = r["add"]["name"] if r["add"] else "?"
        drop_name = r["drop"]["name"] if r["drop"] else "?"
        print(f"  +{r['projected_gain']:>6.1f}  add {add_name:<22} drop {drop_name}")


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
        give_names = ", ".join(x["name"] for x in p["give"])
        get_names = ", ".join(x["name"] for x in p["get"])
        print(f"  Give [{give_names}]  ->  Get [{get_names}]  "
              f"(you: {p['my_delta']:+.1f}, {p['partner_team_name']}: {p['partner_delta']:+.1f})")


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

    p = sub.add_parser("trade-evaluate")
    add_common(p)
    p.add_argument("--team-a", type=int, required=True, dest="team_a")
    p.add_argument("--give", type=int, nargs="+", required=True, help="player_id(s) team A gives up")
    p.add_argument("--team-b", type=int, required=True, dest="team_b")
    p.add_argument("--get", type=int, nargs="+", required=True, help="player_id(s) team A receives")
    p.set_defaults(func=cmd_trade_evaluate)

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
