"""
Configuration for all leagues the system should track.

Add one entry per league you want to pull data for. If you and your
girlfriend are in different leagues (even under different ESPN
accounts), just add a second entry with her own SWID/espn_s2 cookies.
If you're in the SAME league, list it once.

Pull fresh SWID / espn_s2 values from browser dev tools:
Network tab -> any fantasy.espn.com request -> Cookies.
"""

SEASON = 2026

LEAGUES = [
    {
        "name": "my_league",           # short slug, used as a label everywhere
        "league_id": 2077647142,
        "swid": "{43F9DA5A-2D52-43C1-B9C7-DE593AD0A84D}",
        "espn_s2": "AECeQlAUWUXSLPLFvcTWSK3dNeVePYmFPRiSVZNHo6eQCN7TGmoGGUPSL3rl7ncIyV2gtvv4zs23vU%2BgGaQ2yLeZGbNVfW8KWwNHBxIWRaP%2BRJrI8N0qZCrZL4Cq7FLSLrRMASripTOOyUFM%2B6%2BooTb8vOCstns77WComij0Q4ak%2Bp9wO%2F8SYzZatg13kw3xDhSt%2BHvIzuo9ChW2yK%2BO6G7tumnnpCaDTEjLTSSA9JNTKnoHgrtgajh5d7Ok%2BY0d0s8Pn0g1OqZckx%2F0x%2BMYiROhpcYoW84hCxZoGVTuq78oCQ%3D%3D",
        "my_team_id": 11,            # fill in once you know your team_id (see `python cli.py list-teams`)
    },
    # Uncomment and fill in if your girlfriend is in a separate league:
    # {
    #     "name": "gf_league",
    #     "league_id": 0000000000,
    #     "swid": "{XXXXXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX}",
    #     "espn_s2": "PASTE_HER_ESPN_S2_VALUE_HERE",
    #     "my_team_id": None,
    # },
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
 
# Player's default position ID -> readable name.
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
# Fixed single-position slots (QB, RB, WR, TE, K, D/ST) aren't listed here -
# a player fills those only if their own position matches exactly.
FLEX_SLOT_ELIGIBILITY = {
    "FLEX": {"RB", "WR", "TE"},
    "RB/WR": {"RB", "WR"},
    "WR/TE": {"WR", "TE"},
    "OP": {"QB", "RB", "WR", "TE"},
}
 
# Slot names that never count toward a team's scoring lineup.
NON_STARTING_SLOTS = {"BE", "IR", ""}
 