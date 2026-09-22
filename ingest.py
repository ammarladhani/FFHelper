"""
Pull every league's teams, roster settings, and weekly player
projections + ownership into the local SQLite database. Supports
multiple platforms (ESPN, Sleeper, Yahoo) writing into the same schema -
each league_cfg's "platform" key decides which ingestion path runs.

Player IDs are prefixed by platform ("espn_<id>", "sleeper_<id>",
"yahoo_<id>") before being stored, since each platform uses completely
separate, non-comparable ID namespaces (Sleeper even uses non-numeric
IDs like "BUF" for team defenses) - without the prefix, a raw ID
collision between platforms could silently merge two unrelated players.

Usage:
    python ingest.py
"""

from collections import Counter

import config
import db
import espn_client
import sleeper_client
import yahoo_client
import yahoo_projections


# ---------------------------------------------------------------- ESPN

def ingest_espn_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] (espn) fetching league settings & teams...")
    data = espn_client.fetch_league_settings(league_cfg, season)

    db.upsert_league(conn, league_key, league_cfg["league_id"], season)

    for team in data.get("teams", []):
        team_id = team.get("id")
        team_name = (team.get("location", "") + " " + team.get("nickname", "")).strip() or team.get("name")
        owners = team.get("owners", [])
        manager_name = owners[0] if owners else None
        db.upsert_team(conn, league_key, team_id, team_name, manager_name)

    settings = data.get("settings", {})
    roster_settings = settings.get("rosterSettings", {})
    lineup_slot_counts = roster_settings.get("lineupSlotCounts", {})
    for slot_id_str, count in lineup_slot_counts.items():
        slot_id = int(slot_id_str)
        if count and count > 0:
            slot_name = config.SLOT_MAP.get(slot_id, f"slot_{slot_id}")
            db.upsert_setting(conn, league_key, slot_id, slot_name, count)

    conn.commit()


def ingest_espn_week(conn, league_cfg, league_key, season, week):
    players, stat_id = espn_client.fetch_players_week(league_cfg, season, week)
    print(f"[{league_key}] (espn) week {week}: {len(players)} players")

    for p in players:
        raw_id = p.get("id")
        player_id = f"espn_{raw_id}"

        onteam_id = p.get("onTeamId", 0)  # 0 = free agent in ESPN's convention
        team_id = onteam_id if onteam_id and onteam_id > 0 else None

        player_obj = p.get("player", {})
        name = player_obj.get("fullName", "Unknown")
        pos_id = player_obj.get("defaultPositionId")
        position = config.POSITION_MAP.get(pos_id, f"pos_{pos_id}")
        pro_team_id = player_obj.get("proTeamId")

        proj_stats = player_obj.get("stats", [])
        projected = next(
            (s.get("appliedTotal") for s in proj_stats if s.get("id") == stat_id),
            None,
        )

        # Convert ESPN's numeric eligibleSlots to slot NAME strings, so
        # eligibility is stored in the same platform-agnostic shape
        # Sleeper/Yahoo use natively (see lineup_optimizer.py).
        raw_eligible = player_obj.get("eligibleSlots", [])
        eligible_slots = [config.SLOT_MAP.get(sid, f"slot_{sid}") for sid in raw_eligible]

        db.upsert_player(conn, player_id, name, position, pro_team_id, eligible_slots)
        db.upsert_projection(conn, league_key, player_id, week, projected)
        db.upsert_ownership(conn, league_key, player_id, week, team_id)

    conn.commit()


# ------------------------------------------------------------- Sleeper

def ingest_sleeper_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] (sleeper) fetching league settings & teams...")
    league_id = league_cfg["league_id"]
    league = sleeper_client.fetch_league_settings(league_id)

    db.upsert_league(conn, league_key, league_id, season)

    users = sleeper_client.fetch_users(league_id)
    user_map = {u["user_id"]: u for u in users}

    rosters = sleeper_client.fetch_rosters(league_id)
    for r in rosters:
        team_id = r["roster_id"]
        owner = user_map.get(r.get("owner_id"), {})
        manager_name = owner.get("display_name")
        team_name = (owner.get("metadata") or {}).get("team_name") or manager_name
        db.upsert_team(conn, league_key, team_id, team_name, manager_name)

    # Sleeper has no numeric slot IDs - roster_positions is just a flat
    # list like ["QB","RB","RB","WR","WR","TE","FLEX","K","DEF","BN",...].
    # We only need a distinct integer per slot NAME for the DB's primary
    # key; the number itself is never used for eligibility matching.
    roster_positions = league.get("roster_positions", [])
    counts = Counter(roster_positions)
    for idx, (slot_name, count) in enumerate(counts.items()):
        db.upsert_setting(conn, league_key, idx, slot_name, count)

    conn.commit()


def ingest_sleeper_week(conn, league_cfg, league_key, season, week):
    league_id = league_cfg["league_id"]
    league = sleeper_client.fetch_league_settings(league_id)
    points_field = sleeper_client.choose_points_field(league.get("scoring_settings", {}))
    has_ir_slot = "IR" in league.get("roster_positions", [])

    rosters = sleeper_client.fetch_rosters(league_id)
    ownership_map = {}  # raw sleeper player_id (str) -> roster_id
    for r in rosters:
        for pid in (r.get("players") or []):
            ownership_map[pid] = r["roster_id"]

    projections = sleeper_client.fetch_projections_week(season, week)
    print(f"[{league_key}] (sleeper) week {week}: {len(projections)} projection entries")

    seen_raw_ids = set()
    for entry in projections:
        raw_id = entry.get("player_id")
        if raw_id is None:
            continue
        seen_raw_ids.add(raw_id)
        player_id = f"sleeper_{raw_id}"

        player = entry.get("player") or {}
        stats = entry.get("stats") or {}

        first = player.get("first_name") or ""
        last = player.get("last_name") or ""
        name = f"{first} {last}".strip() or player.get("team") or str(raw_id)
        fantasy_positions = player.get("fantasy_positions") or []
        position = player.get("position") or (fantasy_positions[0] if fantasy_positions else None)

        eligible_slots = list(fantasy_positions) + ["BN"]
        if has_ir_slot:
            eligible_slots.append("IR")

        projected = stats.get(points_field)
        team_id = ownership_map.get(raw_id)  # None -> free agent

        db.upsert_player(conn, player_id, name, position, None, eligible_slots)
        db.upsert_projection(conn, league_key, player_id, week, projected)
        db.upsert_ownership(conn, league_key, player_id, week, team_id)

    # Rostered players absent from this week's projections (bye week, or
    # just not covered by Sleeper's projection provider) still need an
    # ownership row - None/0 projection is correct for a bye week anyway.
    for raw_id, team_id in ownership_map.items():
        if raw_id not in seen_raw_ids:
            player_id = f"sleeper_{raw_id}"
            db.upsert_ownership(conn, league_key, player_id, week, team_id)
            db.upsert_projection(conn, league_key, player_id, week, None)

    conn.commit()


# --------------------------------------------------------------- Yahoo

def ingest_yahoo_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] (yahoo) fetching league settings & teams...")
    settings = yahoo_client.fetch_league_settings(league_cfg, season)

    db.upsert_league(conn, league_key, league_cfg["league_id"], season)

    for team in settings["teams"]:
        db.upsert_team(conn, league_key, team["team_id"], team["team_name"], team["manager_name"])

    for idx, (slot_name, count) in enumerate(settings["slot_counts"]):
        if count and count > 0:
            db.upsert_setting(conn, league_key, idx, slot_name, count)

    conn.commit()

    # Full player universe is fetched ONCE here (not per week, unlike
    # ESPN/Sleeper's weekly player pulls) - see fetch_all_players()'s
    # docstring for why. This is the slow part of a Yahoo ingest.
    print(f"[{league_key}] (yahoo) fetching full player list (thousands of players, "
          f"25/page - this is the slow part, expect a few minutes)...")
    players = yahoo_client.fetch_all_players(league_cfg, season)
    for p in players:
        raw_id = p["player_key"].split(".p.")[-1]
        db.upsert_player(conn, f"yahoo_{raw_id}", p["name"], p["position"], None, p["eligible_slots"])
    conn.commit()
    print(f"[{league_key}] (yahoo) cached {len(players)} players")


def ingest_yahoo_week(conn, league_cfg, league_key, season, week):
    rosters = yahoo_client.fetch_rosters_week(league_cfg, season, week)
    ownership_map = {}  # raw yahoo player id (str) -> team_id
    for team_id, player_keys in rosters.items():
        for pk in player_keys:
            ownership_map[pk.split(".p.")[-1]] = team_id

    rostered_count = sum(len(v) for v in rosters.values())
    print(f"[{league_key}] (yahoo) week {week}: {rostered_count} rostered players across {len(rosters)} teams")

    # Projections are borrowed from Sleeper (see yahoo_projections.py's
    # module docstring for why) - keyed by the config's explicit
    # "scoring" setting rather than anything auto-detected from Yahoo.
    projections = yahoo_projections.get_projections_week(season, week, league_cfg["scoring"])

    # Every player we've ever cached for this platform (from the
    # one-time full-list fetch in ingest_yahoo_league_settings) gets an
    # ownership + projection row for this week, same "every free agent
    # still needs a row" approach ingest_sleeper_week uses - a player
    # not in `ownership_map` this week is a free agent (team_id=None).
    all_player_ids = db.get_player_ids_by_prefix(conn, "yahoo_")
    for player_id in all_player_ids:
        raw_id = player_id[len("yahoo_"):]
        team_id = ownership_map.get(raw_id)
        projected = projections.get(raw_id)
        db.upsert_projection(conn, league_key, player_id, week, projected)
        db.upsert_ownership(conn, league_key, player_id, week, team_id)

    conn.commit()


# --------------------------------------------------------------- main

def main():
    conn = db.get_conn(config.DB_PATH)
    db.init_schema(conn)

    for league_cfg in config.LEAGUES:
        league_key = league_cfg["name"]
        platform = league_cfg.get("platform", "espn")

        if platform == "espn":
            config.require_espn_credentials()
            ingest_espn_league_settings(conn, league_cfg, league_key, config.SEASON)
            for week in range(config.START_WEEK, config.END_WEEK + 1):
                ingest_espn_week(conn, league_cfg, league_key, config.SEASON, week)
        elif platform == "sleeper":
            ingest_sleeper_league_settings(conn, league_cfg, league_key, config.SEASON)
            for week in range(config.START_WEEK, config.END_WEEK + 1):
                ingest_sleeper_week(conn, league_cfg, league_key, config.SEASON, week)
        elif platform == "yahoo":
            config.require_yahoo_credentials()
            ingest_yahoo_league_settings(conn, league_cfg, league_key, config.SEASON)
            for week in range(config.START_WEEK, config.END_WEEK + 1):
                ingest_yahoo_week(conn, league_cfg, league_key, config.SEASON, week)
        else:
            print(f"[{league_key}] unknown platform '{platform}', skipping")

    conn.close()
    print("\nDone. Data saved to", config.DB_PATH)


if __name__ == "__main__":
    main()