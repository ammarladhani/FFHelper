"""
Configuration for all leagues the system should track - across
platforms. Each entry needs a "platform" key: "espn" or "sleeper".

ESPN entries need SWID/espn_s2 cookies (pull fresh from browser dev
tools: Network tab -> any fantasy.espn.com request -> Cookies).

Sleeper entries need only a league_id - Sleeper's read API is public,
no auth required. Find your league_id in the URL when viewing your
league on sleeper.com, e.g. sleeper.com/leagues/<LEAGUE_ID>.
"""

SEASON = 2026

LEAGUES = [
    {
        "platform": "espn",
        "name": "my_league",           # short slug, used as a label everywhere
        "league_id": 2077647142,
        "swid": "{43F9DA5A-2D52-43C1-B9C7-DE593AD0A84D}",
        "espn_s2": "AECeQlAUWUXSLPLFvcTWSK3dNeVePYmFPRiSVZNHo6eQCN7TGmoGGUPSL3rl7ncIyV2gtvv4zs23vU%2BgGaQ2yLeZGbNVfW8KWwNHBxIWRaP%2BRJrI8N0qZCrZL4Cq7FLSLrRMASripTOOyUFM%2B6%2BooTb8vOCstns77WComij0Q4ak%2Bp9wO%2F8SYzZatg13kw3xDhSt%2BHvIzuo9ChW2yK%2BO6G7tumnnpCaDTEjLTSSA9JNTKnoHgrtgajh5d7Ok%2BY0d0s8Pn0g1OqZckx%2F0x%2BMYiROhpcYoW84hCxZoGVTuq78oCQ%3D%3D",
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
    9: "DT", 10: "DE", 11: "LB", 12: "CB", 13: "S",
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

# Preferred display ordering for lineup slots (QB, RB, WR, TE, FLEX, K, DEF, IDPs, ...)
PREFERRED_SLOT_ORDER = [
    "QB", "TQB", "RB", "RB/WR", "WR", "WR/TE", "TE",
    "FLEX", "OP", "SUPER_FLEX", "K", "D/ST", "DEF", "P", "HC",
    "DT", "DE", "DL", "LB", "CB", "S", "DB", "DP", "EDR", "Rookie",
]


def slot_sort_key(slot_name: str) -> int:
    try:
        return PREFERRED_SLOT_ORDER.index(slot_name)
    except ValueError:
        return 999