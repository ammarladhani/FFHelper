"""
Thin client around Sleeper's public fantasy football API. Unlike ESPN,
this is fully public and read-only - no auth needed.

Projections come from an unofficial (undocumented) endpoint discovered
by probing - see debug_sleeper_projections.py. If Sleeper ever changes
this, that script is the place to re-verify the URL shape.
"""

import requests

BASE = "https://api.sleeper.app/v1"


def fetch_league_settings(league_id: str) -> dict:
    resp = requests.get(f"{BASE}/league/{league_id}", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_rosters(league_id: str) -> list:
    resp = requests.get(f"{BASE}/league/{league_id}/rosters", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_users(league_id: str) -> list:
    resp = requests.get(f"{BASE}/league/{league_id}/users", timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_projections_week(season: int, week: int) -> list:
    url = f"https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=regular"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.json()


def choose_points_field(scoring_settings: dict) -> str:
    """Pick which of Sleeper's precomputed point fields matches this
    league's reception scoring (0 = standard, 0.5 = half-PPR, 1+ = full PPR).
    Note: this assumes the rest of the league's scoring (esp. K/DEF
    brackets) also matches Sleeper's own default template closely enough -
    worth spot-checking K/DEF projections against the app if your league's
    kicker/defense scoring is unusual.
    """
    rec = scoring_settings.get("rec", 0) or 0
    if rec >= 1:
        return "pts_ppr"
    elif rec > 0:
        return "pts_half_ppr"
    return "pts_std"
