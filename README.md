# Fantasy football optimizer

Pulls fantasy football projections + rosters (ESPN and/or Sleeper), then answers:

- What's every team projected to score, week by week and for the rest of the season?
- Which free agent should I (or my girlfriend) pick up right now to maximize that projection?
- What trades exist where BOTH sides' projected totals go up?
- If I propose a specific trade, what does it actually do to both teams?
- *Why* does a given pickup or trade help - week by week, before vs after?

Supports multiple leagues at once, across platforms - e.g. an ESPN IDP league and a
separate Sleeper league, tracked side by side in the same local database.

## Setup

```
pip install requests scipy
```

Edit `config.py`. Each entry in `LEAGUES` needs a `"platform"` key:

- **ESPN**: fill in `swid` and `espn_s2` (pull fresh from browser dev tools:
  Network tab -> any fantasy.espn.com request -> Cookies).
- **Sleeper**: fill in `league_id` only (from the URL when viewing your league on
  sleeper.com, e.g. `sleeper.com/leagues/<LEAGUE_ID>`). Sleeper's read API is fully
  public - no cookies or login needed at all.

Leave `my_team_id` as `None` for now - you'll fill it in after the first run.

## First run

```
python ingest.py
```

This pulls every week's projections + ownership + league settings + player slot
eligibility into a local SQLite file (`fantasy.db`), for every league listed in
`config.py`. It'll take a few minutes per league for a full season.

> If you're upgrading from a version of this tool from before Sleeper support: the
> database schema changed (`player_id` is now text instead of a number, since
> Sleeper uses string IDs - even non-numeric ones like `"BUF"` for a defense).
> Delete your existing `fantasy.db` and re-run `ingest.py` fresh.

Then find your team ID for each league:

```
python cli.py list-teams --league my_league
python cli.py list-teams --league sleeper_league
```

Fill those into `config.py`'s `my_team_id` if you want, or just pass `--team` on the
command line each time.

## Commands

Every command takes `--league <key>` where `<key>` is whatever `"name"` you gave
that league in `config.py` - the rest works identically regardless of platform.

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
  --add espn_4046692 --drop espn_4362628 \
  --start-week 1 --end-week 18
```

**Evaluate a specific trade** (player_ids, find them via the `waiver` /
`trade-suggest` output, which prints each player's id alongside their name):
```
python cli.py trade-evaluate --league my_league \
  --team-a 3 --give espn_4046692 \
  --team-b 7 --get espn_4362628 \
  --start-week 3 --end-week 18
```

**Why does that trade help (for both sides)?** Same idea as `waiver-why`, but shows
a weekly before/after table for both teams:
```
python cli.py trade-why --league my_league \
  --team-a 3 --give espn_4046692 \
  --team-b 7 --get espn_4362628 \
  --start-week 3 --end-week 18
```

**Find win-win trades to propose:**
```
python cli.py trade-suggest --league my_league --team 11 --start-week 1 --end-week 18
```

## How it works

- `espn_client.py` / `sleeper_client.py` - talk to each platform's API. ESPN needs
  auth cookies; Sleeper is public read-only. Both normalize into the same shape
  before hitting the database.
- `db.py` / `repo.py` - SQLite storage and read queries. Player IDs are stored
  prefixed by platform (`espn_4046692`, `sleeper_4984`) since the two platforms use
  completely separate, non-comparable ID spaces - without the prefix, an ID could
  theoretically collide between an ESPN player and an unrelated Sleeper player.
- Player slot eligibility (`eligible_slots`) is stored as a list of **slot name
  strings** (e.g. `["DT", "DL", "DP", "BE"]`), not platform-specific numeric codes -
  this is what lets one `lineup_optimizer.py` work for both platforms. ESPN's
  numeric `eligibleSlots` IDs are converted to names at ingestion time; Sleeper's
  `fantasy_positions` are already plain strings natively.
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
- Sleeper's projections come from an **unofficial, undocumented endpoint**
  (discovered by probing - see `sleeper_client.py`'s docstring). If Sleeper ever
  changes this, `debug_sleeper_projections.py` is the script to re-run to find the
  new shape.
- Sleeper's precomputed points (`pts_half_ppr` / `pts_ppr` / `pts_std`) are chosen
  automatically based on your league's `rec` scoring value, and are accurate for
  QB/RB/WR/TE as long as the rest of your skill-position scoring is standard.
  **Kicker and defense scoring is not verified** - Sleeper computes those using its
  own default bracket assumptions (FG distance tiers, points-allowed tiers), which
  may not exactly match your league's custom settings. Worth spot-checking one K
  and one DEF projection against what the Sleeper app itself shows.
- Flex-style slots (`FLEX`, `RB/WR`, `OP`, Sleeper's `SUPER_FLEX`, etc.) are matched
  by the player's own position rather than `eligible_slots`, since these are league
  lineup categories rather than a real eligibility slot on either platform. Fine
  for standard RB/WR/TE flex; worth double-checking for unusual flex types.
- Waiver search is capped to the top N free agents by naive point total
  (`fa_prefilter`, default 40) before running the expensive delta calculation, to
  keep runtime reasonable. Raise it if you want a wider search.
- Trade search defaults to 1-for-1 swaps only (`combo_sizes=(1,)` in `trades.py`).
  2-for-1 / 2-for-2 is supported by the code but will be much slower - use a small
  `candidate_prefilter` if you turn it on.