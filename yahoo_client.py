"""
Thin client around Yahoo's official Fantasy Sports API.

AUTH: Yahoo uses three-legged OAuth2, unlike ESPN's cookie auth or
Sleeper's fully public API. Run `python get_yahoo_token.py` once (see
README) to register a Yahoo app and get a refresh_token; after that
this module mints its own short-lived access tokens automatically on
every call, no further browser interaction needed.

PROJECTIONS: Yahoo's official API does not expose a reliable
per-player, future-week "projected points" field the way ESPN and
Sleeper do. What you see on the Yahoo Fantasy website comes from
licensed projection partners (FTN, THE BLITZ, BAKER Prediction Engine,
etc.) that aren't surfaced through this API - people who need Yahoo
projections programmatically generally end up scraping the website
instead. This client pulls everything Yahoo CAN reliably give us -
teams, rosters, ownership, roster slot settings, and each player's
`eligible_positions` (used directly as `eligible_slots` - Yahoo
reports these as plain strings natively, no numeric-ID translation
needed, unlike ESPN) - and leaves the actual point projections to
yahoo_projections.py, which borrows Sleeper's public projections and
cross-references players by Sleeper's `yahoo_id` field.

YAHOO'S JSON SHAPE: requesting format=json gives back Yahoo's XML tree
translated almost literally into JSON - repeated elements land in a
dict keyed "0"/"1"/.../"count", and a single object is often a list of
single-key dicts that need merging. `_collection()` and `_flatten()`
below are the two helpers every parsing function here leans on. Yahoo's
official docs for this are stale/archived (the guide the community
documentation mirrors dates to 2013), so if a response ever doesn't
parse the way a function here expects, run `python debug_yahoo_raw.py`
to dump the raw JSON and adjust the field paths - the same "go look at
the raw response" approach espn_client.py's docstring recommends for
ESPN's undocumented fields.
"""

import base64
import time

import requests

TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
FANTASY_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
REQUEST_TIMEOUT = 30
PLAYER_PAGE_SIZE = 25  # Yahoo caps the players collection at 25 per page

# Access tokens are short-lived (~1hr) and shared by every league under
# the same Yahoo account - cached here by (client_id, refresh_token) so
# a multi-league ingest run refreshes once, not once per league.
_token_cache = {}


def _account_key(league_cfg):
    return (league_cfg["client_id"], league_cfg["refresh_token"])


def _refresh_access_token(league_cfg) -> str:
    creds = f"{league_cfg['client_id']}:{league_cfg['client_secret']}"
    basic = base64.b64encode(creds.encode()).decode()

    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "grant_type": "refresh_token",
            "redirect_uri": "oob",
            "refresh_token": league_cfg["refresh_token"],
        },
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != league_cfg["refresh_token"]:
        print(
            "[yahoo_client] WARNING: Yahoo issued a new refresh_token on this "
            "refresh. Update YAHOO_REFRESH_TOKEN in .env to the value below, or "
            "future runs keep refreshing from the now-stale one:\n"
            f"  {new_refresh}"
        )

    _token_cache[_account_key(league_cfg)] = {
        "access_token": data["access_token"],
        # refresh a little early so a request doesn't land right at expiry
        "expires_at": time.time() + data.get("expires_in", 3600) - 60,
    }
    return data["access_token"]


def get_access_token(league_cfg) -> str:
    cached = _token_cache.get(_account_key(league_cfg))
    if cached and cached["expires_at"] > time.time():
        return cached["access_token"]
    return _refresh_access_token(league_cfg)


def _get(league_cfg, resource: str) -> dict:
    """`resource` is a full Yahoo fantasy resource path with any filters
    already appended Yahoo-style (semicolon-separated key=value pairs on
    the path segment they modify), e.g.
    'league/449.l.123456/players;status=ALL;start=0;count=25'.
    `format=json` is the one real query parameter Yahoo's API expects."""
    url = f"{FANTASY_BASE}/{resource}"
    headers = {"Authorization": f"Bearer {get_access_token(league_cfg)}"}
    resp = requests.get(url, headers=headers, params={"format": "json"}, timeout=REQUEST_TIMEOUT)

    if resp.status_code == 401:
        # access token expired early or got revoked mid-run - refresh once and retry
        _token_cache.pop(_account_key(league_cfg), None)
        headers = {"Authorization": f"Bearer {get_access_token(league_cfg)}"}
        resp = requests.get(url, headers=headers, params={"format": "json"}, timeout=REQUEST_TIMEOUT)

    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------- JSON wrangling

def _collection(node) -> list:
    """A Yahoo '0'/'1'/.../'count' dict -> a plain list, in order."""
    if not isinstance(node, dict):
        return []
    count = node.get("count", 0)
    return [node[str(i)] for i in range(count) if str(i) in node]


def _flatten(item) -> dict:
    """Yahoo often represents one object as a list of single-key dicts
    (and sometimes nested lists of those) rather than one flat dict -
    merge everything into one dict. A dict passed in is returned as-is."""
    if isinstance(item, dict):
        return item
    flat = {}
    if isinstance(item, list):
        for part in item:
            if isinstance(part, dict):
                flat.update(part)
            elif isinstance(part, list):
                flat.update(_flatten(part))
    return flat


# ------------------------------------------------------------- resolving keys

def resolve_game_key(league_cfg, season: int) -> str:
    """NFL's Yahoo game_key changes every season (e.g. 449 was the 2024
    season) - look it up per season rather than hardcoding one that goes
    stale the moment a season rolls over."""
    data = _get(league_cfg, f"games;game_codes=nfl;seasons={season}")
    games = _collection(data["fantasy_content"]["games"])
    if not games:
        raise RuntimeError(f"Yahoo returned no NFL game_key for season {season}")
    game = _flatten(games[0]["game"])
    return game["game_key"]


def _league_key(league_cfg, season: int) -> str:
    return f"{resolve_game_key(league_cfg, season)}.l.{league_cfg['league_id']}"


# --------------------------------------------------------------- league/teams

def fetch_league_settings(league_cfg, season: int) -> dict:
    """Teams + roster slot counts for the league."""
    league_key = _league_key(league_cfg, season)
    data = _get(league_cfg, f"league/{league_key};out=settings,teams")
    league_node = _flatten(data["fantasy_content"]["league"])

    settings = _flatten(league_node.get("settings", {}))
    slot_counts = []  # [(position_name, count), ...]
    for rp in _collection(settings.get("roster_positions", {})):
        pos = _flatten(rp.get("roster_position", rp))
        slot_counts.append((pos["position"], int(pos["count"])))

    teams = []
    for t in _collection(league_node.get("teams", {})):
        team = _flatten(t["team"])
        teams.append({
            "team_id": int(team["team_id"]),
            "team_name": team.get("name"),
            "manager_name": _team_manager_name(team),
        })

    return {"league_key": league_key, "slot_counts": slot_counts, "teams": teams}


def _team_manager_name(team: dict):
    for m in _collection(team.get("managers", {})):
        manager = _flatten(m.get("manager", m))
        if manager.get("nickname"):
            return manager["nickname"]
    return None


# ---------------------------------------------------------------- rosters

def fetch_rosters_week(league_cfg, season: int, week: int) -> dict:
    """{team_id: [player_key, ...]} - who owns whom, as of this week.
    One call for the whole league (Yahoo supports fetching a sub-resource
    across an entire teams collection), not one call per team."""
    league_key = _league_key(league_cfg, season)
    data = _get(league_cfg, f"league/{league_key}/teams/roster;week={week}")
    league_node = _flatten(data["fantasy_content"]["league"])

    rosters = {}
    for t in _collection(league_node.get("teams", {})):
        team = _flatten(t["team"])
        team_id = int(team["team_id"])
        roster = _flatten(team.get("roster", {}))
        player_keys = [
            _flatten(p["player"])["player_key"]
            for p in _collection(roster.get("players", {}))
        ]
        rosters[team_id] = player_keys

    return rosters


# ----------------------------------------------------------- player universe

def _parse_player(player: dict) -> dict:
    eligible_slots = [
        _flatten(e).get("position")
        for e in _collection(player.get("eligible_positions", {}))
    ]
    eligible_slots = [p for p in eligible_slots if p]

    name_node = _flatten(player.get("name", {}))
    return {
        "player_key": player["player_key"],
        "name": name_node.get("full") or player.get("name"),
        "position": player.get("display_position") or player.get("primary_position"),
        "eligible_slots": eligible_slots,
    }


def fetch_all_players(league_cfg, season: int) -> list:
    """Every player Yahoo tracks for this league - rostered AND free
    agent alike (status=ALL) - with name/position/eligible_slots only,
    no per-week data. Meant to be called ONCE per ingest run (from
    ingest_yahoo_league_settings), not once per week: a player's name
    and position essentially never change mid-season, so unlike rosters
    and projections there's no reason to keep re-fetching this. This is
    also the slow part of a Yahoo ingest (thousands of players, 25 per
    page) - expect it to take a few minutes, same ballpark as ESPN's
    per-week pagination.
    """
    league_key = _league_key(league_cfg, season)
    players = []
    start = 0

    while True:
        resource = f"league/{league_key}/players;status=ALL;start={start};count={PLAYER_PAGE_SIZE}"
        data = _get(league_cfg, resource)
        league_node = _flatten(data["fantasy_content"]["league"])
        page = _collection(league_node.get("players", {}))
        if not page:
            break

        for p in page:
            players.append(_parse_player(_flatten(p["player"])))

        if len(page) < PLAYER_PAGE_SIZE:
            break
        start += PLAYER_PAGE_SIZE

    return players