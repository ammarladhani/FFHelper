# Fantasy football optimizer

Pulls ESPN fantasy football projections + rosters, then answers:

- What's every team projected to score, week by week and for the rest of the season?
- Which free agent should I (or my girlfriend) pick up right now to maximize that projection?
- What trades exist where BOTH sides' projected totals go up?
- If I propose a specific trade, what does it actually do to both teams?
- *Why* does a given pickup or trade help - week by week, before vs after?

## Setup

```
pip install requests scipy
```

Edit `config.py`:
1. Fill in `SWID` and `espn_s2` for each league (pull fresh from browser dev tools:
   Network tab -> any fantasy.espn.com request -> Cookies). If you and your girlfriend
   are in different leagues, add a second entry to `LEAGUES`.
2. Leave `my_team_id` as `None` for now - you'll fill it in after the first run.

## First run

```
python ingest.py
```

This pulls every week's projections + ownership + league settings + player slot
eligibility into a local SQLite file (`fantasy.db`). It'll take a few minutes for a
full season (18 weeks x however many paginated player requests each week needs).

Then find your team ID:

```
python cli.py list-teams --league my_league
```

Fill that into `config.py`'s `my_team_id` if you want, or just pass `--team` on the
command line each time.

## Commands

**See projected standings for the rest of the season:**
```
python cli.py simulate --league my_league --start-week 1 --end-week 18
```

**Who should I pick up right now?**
```
python cli.py waiver --league my_league --team 11 --start-week 1 --end-week 18
```

**Why does that pickup help?** Week-by-week before/after comparison, using the
`player_id`s printed by the `waiver` command above:
```
python cli.py waiver-why --league my_league --team 11 \
  --add 4046692 --drop 4362628 \
  --start-week 1 --end-week 18
```

**Evaluate a specific trade** (player_ids, find them via the players table or add a
`--search-name` helper later if you want):
```
python cli.py trade-evaluate --league my_league \
  --team-a 3 --give 4046692 \
  --team-b 7 --get 4362628 \
  --start-week 3 --end-week 18
```

**Why does that trade help (for both sides)?** Same idea as `waiver-why`, but shows
a weekly before/after table for both teams:
```
python cli.py trade-why --league my_league \
  --team-a 3 --give 4046692 \
  --team-b 7 --get 4362628 \
  --start-week 3 --end-week 18
```

**Find win-win trades to propose:**
```
python cli.py trade-suggest --league my_league --team 11 --start-week 1 --end-week 18
```

## How it works

- `espn_client.py` - talks to ESPN's API (projections + ownership + league settings).
- `db.py` / `repo.py` - SQLite storage and read queries, including each player's
  ESPN slot eligibility (`eligible_slots`) alongside their nominal position.
- `lineup_optimizer.py` - given a roster, solves for the best legal starting lineup
  for one week as an optimal assignment problem (`scipy.optimize.linear_sum_assignment`)
  over player eligibility, rather than filling slots greedily. This matters most for
  IDP leagues, where a player is often eligible for several overlapping slots (e.g.
  DT + DL + DP) and a naive fill order can lock in a worse lineup than the optimal
  one. Every other module builds on this.
- `simulator.py` - runs the lineup optimizer across every remaining week to get a
  season-long projected total (and a week-by-week breakdown) per team.
- `waiver.py` - tries every free agent against every roster spot, keeps whichever
  add/drop combo raises the season total the most. `explain_pickup` exposes the
  week-by-week before/after behind a specific add/drop (used by `waiver-why`).
- `trades.py` - either evaluates one specific trade you propose, or searches for
  trades where both sides' season totals improve. `explain_trade` exposes the
  week-by-week before/after for both teams (used by `trade-why`).

## Known rough edges / things to double check on first run

- ESPN's JSON field names (`onTeamId`, `appliedTotal`, `defaultPositionId`,
  `eligibleSlots`, etc.) are undocumented and based on observed behavior - if
  ingestion looks wrong (everyone shows as a free agent, projections look like
  season totals instead of weekly, or a position/slot is mysteriously always
  empty), that's the first place to look. A player's `position` and their
  `eligible_slots` are two different things pulled from two different ESPN
  fields - don't assume a slot name matching a player's nominal position is
  the only way they can be eligible for it.
- Flex-style slots (`FLEX`, `RB/WR`, `OP`, etc.) are matched by the player's
  own position rather than `eligible_slots`, since these are league lineup
  categories rather than real ESPN eligibility slots. Fine for standard
  RB/WR/TE flex; worth double-checking if your league has an unusual flex type.
- Waiver search is capped to the top N free agents by naive point total
  (`fa_prefilter`, default 40) before running the expensive delta calculation, to
  keep runtime reasonable. Raise it if you want a wider search.
- Trade search defaults to 1-for-1 swaps only (`combo_sizes=(1,)` in `trades.py`).
  2-for-1 / 2-for-2 is supported by the code but will be much slower - use a small
  `candidate_prefilter` if you turn it on.