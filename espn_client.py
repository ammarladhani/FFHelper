"""
Thin client around ESPN's (undocumented) fantasy football API.

NOTE: ESPN's response shapes are not officially documented and can
shift. Field lookups here (e.g. `onTeamId`, `appliedTotal`) are based
on observed behavior. If ingestion looks wrong (e.g. everyone shows as
a free agent, or projections look like season totals instead of
weekly), run `python cli.py debug-raw` to dump a raw response and
we'll adjust the field paths.
"""

import logging

import requests

logger = logging.getLogger(__name__)

PAGE_SIZE = 50
REQUEST_TIMEOUT = 30


def _base_url(league_cfg, season):
    return (
        f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/"
        f"seasons/{season}/segments/0/leagues/{league_cfg['league_id']}"
    )


def _cookies(league_cfg):
    return {"SWID": league_cfg["swid"], "espn_s2": league_cfg["espn_s2"]}


def _build_player_filter(stat_id: str, week: int, season: int, offset: int) -> str:
    return (
        '{"players":{'
        '"filterStatsForSplitTypeIds":{"value":[0,1]},'
        '"filterSlotIds":{"value":[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,23,24]},'
        '"filterStatsForSourceIds":{"value":[0,1]},'
        '"useFullProjectionTable":{"value":true},'
        f'"sortAppliedStatTotal":{{"sortAsc":false,"sortPriority":3,"value":"{stat_id}"}},'
        '"sortPercOwned":{"sortPriority":4,"sortAsc":false},'
        f'"limit":{PAGE_SIZE},'
        f'"offset":{offset},'
        '"filterRanksForSlotIds":{"value":[0,2,4,6,17,16,8,9,10,12,13,24,11,14,15]},'
        f'"filterStatsForTopScoringPeriodIds":{{"value":{week},"additionalValue":'
        f'["00{season}","10{season}","00{season-1}","{stat_id}","02{season}"]}}'
        '}}'
    )


def fetch_players_week(league_cfg, season: int, week: int) -> list:
    """
    Fetch every player for a given week: name, position, pro team,
    ownership (onTeamId; 0/absent = free agent), and projected points.
    Paginates until ESPN returns a short page.

    Raises requests.HTTPError on a non-2xx response and RuntimeError if
    ESPN returns a 200 with an embedded error payload (it does this for
    e.g. expired/invalid cookies) - previously both cases just logged a
    warning and silently returned a partial/empty player list, which
    made a bad session cookie look identical to "nobody's a free agent
    this week" during ingestion.
    """
    stat_id = f"11{season}{week}"
    base_url = _base_url(league_cfg, season)
    cookies = _cookies(league_cfg)

    all_players = []
    offset = 0

    while True:
        headers = {
            "accept": "application/json",
            "x-fantasy-filter": _build_player_filter(stat_id, week, season, offset),
            "x-fantasy-platform": "espn-fantasy-web",
            "x-fantasy-source": "kona",
        }
        params = {"view": "kona_player_info", "scoringPeriodId": week}

        resp = requests.get(
            base_url, params=params, headers=headers, cookies=cookies,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()

        data = resp.json()
        if "messages" in data:
            raise RuntimeError(
                f"ESPN returned an error for week {week} (often means the SWID/"
                f"espn_s2 cookies have expired - re-pull them from the browser): "
                f"{data['messages']}"
            )

        page = data.get("players", [])
        if not page:
            break

        all_players.extend(page)
        logger.debug("week %d offset %d: fetched %d players", week, offset, len(page))
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_players, stat_id


def fetch_league_settings(league_cfg, season: int) -> dict:
    """Fetch roster slot counts and team list for the league."""
    base_url = _base_url(league_cfg, season)
    cookies = _cookies(league_cfg)
    params = {"view": ["mSettings", "mTeam"]}

    resp = requests.get(base_url, params=params, cookies=cookies, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return resp.json()

def fetch_roster_slots(league_cfg, season: int, week: int) -> dict:
    """
    Raw ESPN player id -> lineupSlotId, for every rostered player, as of
    this scoring period. Used to detect IR: occupying lineup slot 21
    ("IR" in config.SLOT_MAP) is how ESPN marks a player roster-locked,
    as opposed to sitting on the bench (slot 20, "BE"), which stays
    freely droppable.
    """
    base_url = _base_url(league_cfg, season)
    cookies = _cookies(league_cfg)
    params = {"view": "mRoster", "scoringPeriodId": week}

    resp = requests.get(base_url, params=params, cookies=cookies, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()

    slots = {}
    for team in data.get("teams", []):
        for entry in (team.get("roster") or {}).get("entries", []):
            player_id = entry.get("playerId")
            if player_id is not None:
                slots[player_id] = entry.get("lineupSlotId")
    return slots


def fetch_schedule(league_cfg, season: int) -> list:
    """
    Raw `schedule` entries (every head-to-head matchup, regular season
    and playoffs) from ESPN's matchup views. Use parse_schedule() to turn
    them into (week, team_a, team_b, score_a, score_b) tuples.
    """
    base_url = _base_url(league_cfg, season)
    cookies = _cookies(league_cfg)
    params = {"view": ["mMatchup", "mMatchupScore"]}

    resp = requests.get(base_url, params=params, cookies=cookies, timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if "messages" in data:
        raise RuntimeError(
            f"ESPN returned an error fetching the schedule (often means the SWID/"
            f"espn_s2 cookies have expired): {data['messages']}"
        )
    return data.get("schedule", [])


def parse_schedule(entries: list, regular_season_weeks: int) -> list:
    """
    ESPN schedule entries -> [(week, home_team_id, away_team_id,
    home_score, away_score)] for regular-season head-to-heads only.

    Scores are only kept once ESPN has decided the matchup (`winner` is
    HOME/AWAY/TIE - it's UNDECIDED until then), so unplayed and
    in-progress weeks come back with None scores and get projected
    instead. Byes (no away side) and playoff-bracket entries
    (`playoffTierType` other than NONE) are skipped.
    """
    matchups = []
    for entry in entries:
        if entry.get("playoffTierType", "NONE") != "NONE":
            continue
        week = entry.get("matchupPeriodId")
        if week is None or week > regular_season_weeks:
            continue
        home, away = entry.get("home"), entry.get("away")
        if not home or not away:
            continue  # bye week
        home_id, away_id = home.get("teamId"), away.get("teamId")
        if home_id is None or away_id is None:
            continue
        decided = entry.get("winner") in ("HOME", "AWAY", "TIE")
        matchups.append((
            week, home_id, away_id,
            home.get("totalPoints") if decided else None,
            away.get("totalPoints") if decided else None,
        ))
    return matchups