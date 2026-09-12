"""
Thin client around ESPN's (undocumented) fantasy football API.

NOTE: ESPN's response shapes are not officially documented and can
shift. Field lookups here (e.g. `onTeamId`, `appliedTotal`) are based
on observed behavior. If ingestion looks wrong (e.g. everyone shows as
a free agent, or projections look like season totals instead of
weekly), run `python cli.py debug-raw` to dump a raw response and
we'll adjust the field paths.
"""

import requests

PAGE_SIZE = 50


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

        resp = requests.get(base_url, params=params, headers=headers, cookies=cookies)
        if resp.status_code != 200:
            print(f"  [warn] week {week} offset {offset}: status {resp.status_code}")
            break

        data = resp.json()
        if "messages" in data:
            print(f"  [warn] week {week}: ESPN error {data['messages']}")
            break

        page = data.get("players", [])
        if not page:
            break

        all_players.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE

    return all_players, stat_id


def fetch_league_settings(league_cfg, season: int) -> dict:
    """Fetch roster slot counts and team list for the league."""
    base_url = _base_url(league_cfg, season)
    cookies = _cookies(league_cfg)
    params = {"view": ["mSettings", "mTeam"]}

    resp = requests.get(base_url, params=params, cookies=cookies)
    resp.raise_for_status()
    return resp.json()
