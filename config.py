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
"""

import os

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
    },
    {
        "platform": "sleeper",
        "name": "sleeper_league",
        "league_id": "1322365155329216512",
        "my_team_id": 3,            # fill in with your roster_id after list-teams
    },
]

START_WEEK = 1
END_WEEK = 18

DB_PATH = "fantasy.db"


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
