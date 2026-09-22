"""
Yahoo's official API doesn't expose per-player, future-week projected
points (see yahoo_client.py's module docstring for why) - so for Yahoo
leagues, projections are borrowed from Sleeper's public, unofficial
projections endpoint (the same one sleeper_client.py uses) and matched
to Yahoo players via the `yahoo_id` cross-reference field present in
Sleeper's player dictionary.

This is a best-effort bridge, not a guarantee: if Sleeper's dictionary
is missing a `yahoo_id` for some player (rare - mostly happens for
D/ST entries and very deep bench/practice-squad players), that player
just gets no projection for Yahoo leagues, the same "no projection
data" outcome any other unprojected player already gets elsewhere in
this app. Worth spot-checking a few of your own roster's projections
against Sleeper's own numbers on first run, especially for K/DEF -
Sleeper's own docstring already flags those as using Sleeper's default
scoring-bracket assumptions rather than your exact league settings.
"""

import requests

import sleeper_client

SLEEPER_PLAYERS_URL = "https://api.sleeper.app/v1/players/nfl"
REQUEST_TIMEOUT = 30

# Sleeper's full player dictionary is ~5,000 players and Sleeper's own
# docs ask callers not to hit this more than once a day - so it's
# fetched once per process (per ingest run) and cached here, not once
# per week the way projections/rosters are.
_yahoo_to_sleeper_cache = None


def _load_yahoo_to_sleeper_map() -> dict:
    global _yahoo_to_sleeper_cache
    if _yahoo_to_sleeper_cache is not None:
        return _yahoo_to_sleeper_cache

    resp = requests.get(SLEEPER_PLAYERS_URL, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    all_players = resp.json()

    mapping = {}
    for sleeper_id, info in all_players.items():
        yahoo_id = info.get("yahoo_id")
        if yahoo_id:
            mapping[str(yahoo_id)] = sleeper_id

    _yahoo_to_sleeper_cache = mapping
    return mapping


def get_projections_week(season: int, week: int, scoring_settings: dict) -> dict:
    """{yahoo_raw_player_id: projected_points} for one week - raw ids
    only (no 'yahoo_' prefix), matching the shape callers already work
    with elsewhere before db.py's prefixing is applied at write time."""
    yahoo_to_sleeper = _load_yahoo_to_sleeper_map()
    points_field = sleeper_client.choose_points_field(scoring_settings)

    sleeper_projections = sleeper_client.fetch_projections_week(season, week)
    sleeper_points = {}
    for entry in sleeper_projections:
        raw_id = entry.get("player_id")
        if raw_id is None:
            continue
        stats = entry.get("stats") or {}
        sleeper_points[raw_id] = stats.get(points_field)

    projections = {}
    for yahoo_id, sleeper_id in yahoo_to_sleeper.items():
        if sleeper_id in sleeper_points:
            projections[yahoo_id] = sleeper_points[sleeper_id]

    return projections