# Fantasy football optimizer

Pulls ESPN fantasy football projections + rosters, then answers:

- What's every team projected to score, week by week and for the rest of the season?
- Which free agent should I (or my girlfriend) pick up right now to maximize that projection?
- What trades exist where BOTH sides' projected totals go up?
- If I propose a specific trade, what does it actually do to both teams?

## Setup

```
pip install requests
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

This pulls every week's projections + ownership + league settings into a local
SQLite file (`fantasy.db`). It'll take a few minutes for a full season (18 weeks x
however many paginated player requests each week needs).

Then find your team ID:

```
python cli.py list-teams --league my_league
```

Fill that into `config.py`'s `my_team_id` if you want, or just pass `--team` on the
command line each time.

## Commands

**See projected standings for the rest of the season:**
```
python cli.py simulate --league my_league --start-week 3 --end-week 18
```

**Who should I pick up right now?**
```
python cli.py waiver --league my_league --team 3 --start-week 3 --end-week 18
```

**Evaluate a specific trade** (player_ids, find them via the players table or add a
`--search-name` helper later if you want):
```
python cli.py trade-evaluate --league my_league \
  --team-a 3 --give 4046692 \
  --team-b 7 --get 4362628 \
  --start-week 3 --end-week 18
```

**Find win-win trades to propose:**
```
python cli.py trade-suggest --league my_league --team 3 --start-week 3 --end-week 18
```

## How it works

- `espn_client.py` - talks to ESPN's API (projections + ownership + league settings).
- `db.py` / `repo.py` - SQLite storage and read queries.
- `lineup_optimizer.py` - given a roster, picks the best legal starting lineup for
  one week. Every other module builds on this.
- `simulator.py` - runs the lineup optimizer across every remaining week to get a
  season-long projected total per team.
- `waiver.py` - tries every free agent against every roster spot, keeps whichever
  add/drop combo raises the season total the most.
- `trades.py` - either evaluates one specific trade you propose, or searches for
  trades where both sides' season totals improve.

## Known rough edges / things to double check on first run

- ESPN's JSON field names (`onTeamId`, `appliedTotal`, `defaultPositionId`, etc.)
  are undocumented and based on observed behavior - if ingestion looks wrong
  (everyone shows as a free agent, or projections look like season totals instead
  of weekly), that's the first place to look.
- The lineup optimizer's greedy approach (fill exact-position slots first, then
  flex slots with the best leftovers) is optimal for standard league structures,
  but hasn't been checked against unusual/deep bench configurations.
- Waiver search is capped to the top N free agents by naive point total
  (`fa_prefilter`, default 40) before running the expensive delta calculation, to
  keep runtime reasonable. Raise it if you want a wider search.
- Trade search defaults to 1-for-1 swaps only (`combo_sizes=(1,)` in `trades.py`).
  2-for-1 / 2-for-2 is supported by the code but will be much slower - use a small
  `candidate_prefilter` if you turn it on.
