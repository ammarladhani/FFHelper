# Fantasy football optimizer

Pulls fantasy football projections + rosters (ESPN, Sleeper, and/or Yahoo), then answers:

- What's every team projected to score, week by week and for the rest of the season?
- Which free agent should I (or my girlfriend) pick up right now to maximize that projection?
- What trades exist where BOTH sides' projected totals go up?
- If I propose a specific trade, what does it actually do to both teams?
- *Why* does a given pickup or trade help - week by week, before vs after?

Supports multiple leagues at once, across platforms - e.g. an ESPN IDP league, a
Sleeper league, and a Yahoo league, all tracked side by side in the same local
database. Usable either as a CLI (`cli.py`) or a local web UI (`app.py`) - both
call the same backend, so pick whichever fits the moment.

## Setup

```
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill in credentials for whichever platform(s)
you're using. `config.py` itself has no secrets in it and is safe to share/commit.

**ESPN** needs your session cookies (pull fresh from browser dev tools: Network
tab -> any fantasy.espn.com request -> Cookies). **These are live credentials
for your ESPN account - `.env` is gitignored and should never be committed.**

**Sleeper** leagues need only a `league_id` in `config.py` (from the URL when
viewing your league on sleeper.com, e.g. `sleeper.com/leagues/<LEAGUE_ID>`) -
Sleeper's read API is fully public, no cookies or login needed at all.

**Yahoo** needs OAuth2, not a cookie - it's the one platform here where "logged
into your browser" isn't enough. One-time setup:
1. Register an app at [developer.yahoo.com/apps](https://developer.yahoo.com/apps/) -
   any name, then under "API Permissions" check **Fantasy Sports** with Read
   access. The redirect URI you put doesn't matter (this tool uses Yahoo's
   out-of-band flow, not a callback server) - `https://localhost:8080` is fine.
2. Grab the **Client ID** and **Client Secret** Yahoo shows on the app's page.
3. Run `python get_yahoo_token.py`, paste those two values in when asked, and
   follow the browser prompt to authorize the app. It prints three lines to
   put in `.env`: `YAHOO_CLIENT_ID`, `YAHOO_CLIENT_SECRET`, `YAHOO_REFRESH_TOKEN`.
4. Fill in `league_id` in `config.py` (the numeric ID from your league's URL)
   and set `"scoring"` to match your league (see the note in `config.py` -
   Yahoo doesn't expose reception scoring in a form we're confident
   auto-parsing, so this is one place you tell it rather than it guessing).

One Yahoo account's OAuth credentials cover every Yahoo league you're in under
that account - you don't need to repeat setup per league, just add another
entry to `LEAGUES` with the same `client_id`/`client_secret`/`refresh_token`
and a different `league_id`.

> **Yahoo doesn't give you real weekly player projections.** Unlike ESPN and
> Sleeper, Yahoo's official API has no per-player, future-week "projected
> points" field - what you see on Yahoo's website comes from licensed
> projection partners that aren't exposed through the API. This tool works
> around that by borrowing Sleeper's public projections and matching players
> across platforms via Sleeper's `yahoo_id` cross-reference field (see
> `yahoo_projections.py`). Everything else for Yahoo - rosters, ownership,
> league settings, positions - comes straight from Yahoo. In practice this
> means Yahoo-league recommendations are only as good as the cross-platform
> player match, which is very good for standard rosters and can miss a rare
> deep-bench player Sleeper doesn't track under a matching `yahoo_id`.

Leave `my_team_id` as `None` for now - you'll fill it in after the first run.

## First run

```
python ingest.py
```

This pulls every week's projections + ownership + league settings + player slot
eligibility into a local SQLite file (`fantasy.db`), for every league listed in
`config.py`. It'll take a few minutes per league for a full season - Yahoo leagues
take a bit longer on the very first run specifically, since (unlike ESPN/Sleeper)
Yahoo's full player list has to be paginated 25-at-a-time across the league's
entire player pool once up front (see `yahoo_client.fetch_all_players`'s
docstring); after that first pull it's cached and each week's ingest is fast.

> If you're upgrading from a version of this tool from before Sleeper/Yahoo
> support: the database schema changed (`player_id` is now text instead of a
> number, since Sleeper/Yahoo use string IDs - even non-numeric ones like
> Sleeper's `"BUF"` for a defense). Run `python reset_db.py` and then
> `python ingest.py` fresh.

Then find your team ID for each league:

```
python cli.py list-teams --league my_league
python cli.py list-teams --league sleeper_league
python cli.py list-teams --league yahoo_league
```

Fill those into `config.py`'s `my_team_id` if you want, or just pass `--team` on the
command line each time.

## Web UI

```
streamlit run app.py
```

Opens a local dashboard in your browser (defaults to `http://localhost:8501`).
Pick a league and team from the sidebar, then:

- **Standings** - projected remaining-season totals for every team, with a chart.
- **Waiver Wire** - top pickups, with an expandable "why does this help?" panel per
  pickup showing a week-by-week before/after chart (same as `waiver-why`, but
  inline, no need to copy player IDs by hand).
- **Trade Finder** - win-win trades, with a dropdown to restrict to one partner
  team (same as `--partner-team`), plus the give/get frequency charts.
- **Evaluate Trade** - pick players from dropdowns instead of typing IDs; shows
  before/after totals and a chart for both teams.
- **Rosters** - browse any team's roster and projections for a given week.

This is a thin layer over the same `simulator` / `waiver` / `trades` / `repo`
modules the CLI uses - nothing about the underlying logic changes, it's just a
friendlier way to drive it. Cached results (waiver picks, trade proposals) are
keyed by league/team/week-range, so switching teams in the sidebar won't leave
stale results from the previous team on screen under the new team's label.

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
Add `--partner-team <team_id>` to restrict the search to one specific team instead
of scanning the whole league:
```
python cli.py trade-suggest --league my_league --team 11 --partner-team 5 \
  --start-week 1 --end-week 18
```
The output ends with two summary blocks - "Most frequently traded AWAY" and "Most
frequently RECEIVED" - counted across *every* win-win trade found, not just the
ones printed above the fold (`--top-n` only limits the printed list, not the
summary), so it's a quick read on which of your players keep coming up as trade
bait and which players across the league you'd keep landing on.

## Tests

```
python -m pytest tests/ -v
```

Builds a small in-memory league and exercises `simulator`, `waiver`, and `trades`
end to end. There were previously no tests at all in this repo, which is how a
signature mismatch between `waiver.py`'s calls to `simulate_roster()` and
`simulator.py`'s actual `simulate_roster()` definition shipped - every single
`python cli.py waiver` invocation raised `TypeError`. Run this before opening a PR
that touches `simulator.py`, `repo.py`, `waiver.py`, or `trades.py`.

## How it works

- `espn_client.py` / `sleeper_client.py` / `yahoo_client.py` - talk to each
  platform's API. ESPN needs auth cookies (from `.env`, see Setup); Sleeper is
  public read-only; Yahoo needs OAuth2 (see Setup, and `get_yahoo_token.py`).
  All three normalize into the same shape before hitting the database.
- `yahoo_projections.py` - Yahoo's API has no per-player future-week
  projections (see the callout in Setup above), so this borrows Sleeper's
  public projections and cross-references players by Sleeper's `yahoo_id`
  field. Only used for Yahoo leagues; ESPN and Sleeper get real projections
  straight from their own APIs.
- `get_yahoo_token.py` - one-time interactive OAuth setup for a Yahoo account;
  prints the three values (`YAHOO_CLIENT_ID`/`YAHOO_CLIENT_SECRET`/
  `YAHOO_REFRESH_TOKEN`) to put in `.env`. `debug_yahoo_raw.py` dumps a raw
  Yahoo API response for a given league/resource if `yahoo_client.py`'s
  parsing of Yahoo's JSON shape ever needs adjusting.
- `db.py` / `repo.py` - SQLite storage and read queries. Player IDs are stored
  prefixed by platform (`espn_4046692`, `sleeper_4984`, `yahoo_30123`) since
  each platform uses completely separate, non-comparable ID spaces - without
  the prefix, an ID could theoretically collide between two unrelated players
  on different platforms. `repo.py`'s player-info/roster lookups accept an
  optional shared cache dict so `waiver.py`/`trades.py` searches (which
  re-simulate the same handful of players over and over across candidate
  rosters) don't round-trip to SQLite for information that hasn't changed
  between calls.
- Player slot eligibility (`eligible_slots`) is stored as a list of **slot name
  strings** (e.g. `["DT", "DL", "DP", "BE"]`), not platform-specific numeric codes -
  this is what lets one `lineup_optimizer.py` work across all three platforms.
  ESPN's numeric `eligibleSlots` IDs are converted to names at ingestion time;
  Sleeper's `fantasy_positions` and Yahoo's `eligible_positions` are both
  already plain strings natively.
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
  Free agents are pre-filtered to the top `fa_prefilter` (default 40, matching this
  doc - a prior default of 2533 effectively disabled the prefilter) before the
  expensive per-candidate simulation runs.
- `trades.py` - either evaluates one specific trade you propose, or searches for
  trades where both sides' season totals improve (optionally restricted to one
  partner team via `partner_team_id`). `explain_trade` exposes the week-by-week
  before/after for both teams (used by `trade-why`). `suggest_trades` defaults to
  1-for-1 swaps only (`combo_sizes=(1,)`); pass `combo_sizes=(1, 2)` for
  2-for-1/2-for-2 too, with a smaller `candidate_prefilter` since it's much slower.
- `inspect_db.py` - read-only schema/data inspector:
  `python inspect_db.py fantasy.db --team 11 --player "some name"`.
- `reset_db.py` - safely deletes `fantasy.db` (with a confirmation prompt) so you
  can rebuild it from scratch after a schema change.
- `debug_sleeper_projections.py` - probes Sleeper's unofficial projections
  endpoint if `sleeper_client.py`'s parsing ever starts breaking (see below).
  `debug_yahoo_raw.py` is the Yahoo equivalent.

## Known rough edges / things to double check on first run

- ESPN's JSON field names (`onTeamId`, `appliedTotal`, `defaultPositionId`,
  `eligibleSlots`, etc.) are undocumented and based on observed behavior - if
  ingestion looks wrong (everyone shows as a free agent, projections look like
  season totals instead of weekly, or a position/slot is mysteriously always
  empty), that's the first place to look. A player's `position` and their
  `eligible_slots` are two different things pulled from two different ESPN
  fields - don't assume a slot name matching a player's nominal position is
  the only way they can be eligible for it. `espn_client.py` now raises instead
  of silently returning a partial/empty player list on an ESPN error response
  (most commonly an expired SWID/espn_s2 cookie) - check the exception message
  first if ingestion suddenly stops finding anyone.
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
- **Yahoo has no real per-player weekly projections via its official API** - see
  the callout in Setup. This tool bridges in Sleeper's projections by matching on
  Sleeper's `yahoo_id` field, which covers standard rostered players well but can
  miss an obscure deep-bench player. Worth spot-checking a couple of your Yahoo
  roster's projected points against what Sleeper itself shows for the same
  players, same spirit as the Sleeper K/DEF check above.
- **Yahoo's reception scoring isn't auto-detected** - unlike Sleeper, where a
  league's `rec` value is read straight from the API, Yahoo's scoring-settings
  response doesn't expose this in a form we're confident parsing automatically.
  Set `"scoring"` in `config.py`'s Yahoo league entry by hand to match your
  league (it feeds directly into the same `choose_points_field` logic Sleeper
  leagues use, since Yahoo projections are borrowed from Sleeper).
- **Yahoo occasionally rotates its refresh_token on a refresh call.**
  `yahoo_client.py` prints a warning with the new value if this happens, since
  continuing to refresh from the old (now possibly-invalidated) one could break
  ingestion. Rare, but if a Yahoo league starts failing auth, check the console
  output from your last `ingest.py` run for that warning.
- Yahoo's official REST API guide is stale/archived (the community docs it's
  based on date to 2013) - `yahoo_client.py`'s JSON parsing is built from that
  plus common usage patterns rather than an authoritative current spec. If
  something doesn't parse right, `debug_yahoo_raw.py` dumps the raw response
  for a given league/resource so you can see exactly what changed.
- Flex-style slots (`FLEX`, `RB/WR`, `OP`, Sleeper's `SUPER_FLEX`, Yahoo's
  `W/R/T`/`W/T`/`Q/W/R/T`, etc.) are matched by the player's own position rather
  than `eligible_slots`, since these are league lineup categories rather than a
  real eligibility slot on any platform. Fine for standard RB/WR/TE flex; worth
  double-checking for unusual flex types, especially on Yahoo where an
  uncommon flex code might not be in `config.FLEX_SLOT_ELIGIBILITY` yet - add
  it there if `lineup_optimizer.py` seems to be benching someone who should be
  flex-eligible.