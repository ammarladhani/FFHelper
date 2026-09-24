"""
Configuration for all leagues the system should track - across
platforms. Each entry needs a "platform" key: "espn" or "sleeper".

ESPN entries need SWID/espn_s2 session cookies (pull fresh from
browser dev tools: Network tab -> any fantasy.espn.com request ->
Cookies). These are live authentication credentials for your ESPN
account - NOT public. They are read from environment variables (see
.env.example) instead of being hardcoded here, and this file is safe
to commit / share.

Sleeper entries need only a league_id - Sleeper's read API is public,
no auth required. Find your league_id in the URL when viewing your
league on sleeper.com, e.g. sleeper.com/leagues/<LEAGUE_ID>.

Per-league season structure (all platforms):

    end_week        Last week of the fantasy season, INCLUDING playoffs
                    (ESPN league: 18, Sleeper league: 16). Ingestion, the
                    week slider, and the CLI defaults all stop here.
    playoff_teams   How many teams make the playoffs.
    playoff_weeks   How many weeks the playoffs last. The regular season is
                    therefore weeks 1 .. (end_week - playoff_weeks).
    schedule        OPTIONAL manual override of the regular-season schedule,
                    {week: [(team_id, team_id), ...]}. Normally leave this out
                    - ingest.py pulls the real schedule (and actual scores for
                    finished weeks) from ESPN/Sleeper automatically.

playoff_teams / playoff_weeks are what the projected-records / champion
view needs. They are left as None below on purpose: fill in YOUR leagues'
real values rather than trusting a guess.
"""

import os
from datetime import date

from dotenv import load_dotenv

load_dotenv()  # reads a local .env file if present; real env vars always win

SEASON = 2026

LEAGUES = [
    {
        "platform": "espn",
        "name": "my_league",           # short slug, used as a label everywhere
        "league_id": 2077647142,
        "swid": os.environ.get("ESPN_SWID_MY_LEAGUE", ""),
        "espn_s2": os.environ.get("ESPN_S2_MY_LEAGUE", ""),
        "my_team_id": 11,
        "end_week": 18,
        "playoff_teams": 8,          # TODO: fill in (e.g. 6)
        "playoff_weeks": 3,          # TODO: fill in (e.g. 3)
        "buy_in": 15,
        "payouts": {
            "placement": {1: 100, 2: 65, 3: 15},
        },
    },
    {
        "platform": "sleeper",
        "name": "sleeper_league",
        "league_id": "1322365155329216512",
        "my_team_id": 3,            # fill in with your roster_id after list-teams
        "end_week": 16,
        "playoff_teams": 6,          # TODO: fill in (e.g. 6)
        "playoff_weeks": 3,          # TODO: fill in (e.g. 3)
        "buy_in": 30,
        "payouts": {
            "placement": {1: 60, 2: 30, 3: 10},
            # $10 to the week's high scorer, weeks 1 through playoff round 1 (week 14)
            "weekly_high": {"amount": 10, "start_week": 1, "end_week": 14},
        },
    },
]

START_WEEK = 1
# Fallback last week for any league entry that doesn't set its own "end_week".
END_WEEK = 18

DB_PATH = "fantasy.db"

# The SUNDAY on which "week 1" begins. The current week rolls over at
# midnight every Sunday from here: 9/6 -> week 1, 9/13 -> week 2,
# 9/20 -> week 3, ... Used to default the week slider to "this week".
WEEK_1_START = date(2026, 9, 6)

# Recency weighting: a week `n` weeks after the first week in the
# selected range counts for DEFAULT_DECAY ** n of a week-0 point.
# 0.9 -> each week further out is worth 90% of the one before it.
DEFAULT_DECAY = 0.9

# Monte Carlo win-probability simulation (see win_probability.py). Ingestion
# only stores a point PROJECTION per team-week, not any measure of how much
# that projection has actually varied historically - there's no real
# week-to-week variance data to calibrate against. So a team's score in a
# not-yet-played week is modeled there as Normal(mean=projection,
# stdev=DEFAULT_SCORE_STD_FRACTION * mean), a simplifying, adjustable
# assumption rather than a fitted model. 0.20 is a reasonable fantasy
# football ballpark (raise it for more upsets/less confident favorites).
DEFAULT_SCORE_STD_FRACTION = 0.20
DEFAULT_N_SIMS = 2000


def require_espn_credentials():
    """Raise a clear error for any ESPN league missing its SWID/espn_s2
    cookies. Deliberately NOT run at import time - config.py also holds
    constants (SLOT_MAP, FLEX_SLOT_ELIGIBILITY, ...) that plenty of code
    needs without ever touching ESPN's API (lineup_optimizer.py, the
    test suite), and those shouldn't require secrets to even import.
    Called from ingest.py right before an ESPN league is actually hit.
    """
    for league in LEAGUES:
        if league["platform"] == "espn" and not (league.get("swid") and league.get("espn_s2")):
            raise RuntimeError(
                f"League '{league['name']}' is an ESPN league but its SWID/espn_s2 "
                "cookies are missing. Copy .env.example to .env and fill them in - "
                "see the README for how to pull them from your browser."
            )

# ESPN's default lineup slot ID -> readable name (standard mapping used
# across ESPN fantasy API tooling).
SLOT_MAP = {
    0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
    7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
    20: "BE", 21: "IR", 22: "", 23: "FLEX", 24: "EDR", 25: "Rookie",
}

# Slot names that lock a player onto a roster - the manager can leave
# them there, but the automated waiver/trade tooling shouldn't treat
# them as free to drop/trade the way a normal bench player is.
# Distinct from NON_STARTING_SLOTS: BE is also non-scoring but IS
# freely droppable.
RESERVED_SLOT_NAMES = {"IR", "TAXI"}

# Player's default position ID -> readable name (ESPN only - Sleeper
# reports position as a plain string natively, no ID lookup needed).
# ESPN uses two separate numbering schemes that happen not to collide:
# offensive positions (+ D/ST) use one set of codes, individual defensive
# player (IDP) positions use another. Both are needed for IDP leagues.
POSITION_MAP = {
    1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST",
    # IDP positions
    8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
    14: "DB", 15: "DP", 7: "P",
}

# Which slot names count as "eligible for FLEX"
FLEX_ELIGIBLE = {"RB", "WR", "TE"}

# Multi-position ("flex-like") slots and which player positions can fill them.
# Fixed single-position slots (QB, RB, WR, TE, K, D/ST, DEF, ...) aren't
# listed here - a player fills those only via their real eligible_slots.
FLEX_SLOT_ELIGIBILITY = {
    "FLEX": {"RB", "WR", "TE"},
    "RB/WR": {"RB", "WR"},
    "WR/TE": {"WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
    "SUPER_FLEX": {"QB", "RB", "WR", "TE"},  # Sleeper's superflex slot name
}

# Slot names that never count toward a team's scoring lineup.
NON_STARTING_SLOTS = {"BE", "BN", "IR", ""}