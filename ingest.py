"""
Pull every league's teams, roster settings, regular-season schedule, and
weekly player projections + ownership into the local SQLite database.
Supports multiple platforms (ESPN, Sleeper) writing into the same schema -
each league_cfg's "platform" key decides which ingestion path runs.

Optimized with parallel week/league execution and high-speed SQLite pragma settings.

Usage:
    python ingest.py
"""

from collections import Counter
import concurrent.futures
import time
import config
import db
import espn_client
import league_info
import sleeper_client


# ---------------------------------------------------------------- ESPN

def ingest_espn_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] (espn) fetching league settings & teams...")
    data = espn_client.fetch_league_settings(league_cfg, season)

    with conn:
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


def fetch_espn_week_data(league_cfg, season, week):
    players, stat_id = espn_client.fetch_players_week(league_cfg, season, week)
    roster_slots = espn_client.fetch_roster_slots(league_cfg, season, week)
    return week, players, roster_slots


def ingest_espn_weeks_parallel(conn, league_cfg, league_key, season, start_week, end_week):
    weeks = list(range(start_week, end_week + 1))
    print(f"[{league_key}] (espn) fetching weeks {start_week}-{end_week} in parallel...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_espn_week_data, league_cfg, season, w): w for w in weeks}

        for future in concurrent.futures.as_completed(futures):
            week, players, roster_slots = future.result()
            print(f"[{league_key}] (espn) week {week}: {len(players)} players")

            with conn:
                for p in players:
                    raw_id = p.get("id")
                    if raw_id is None:
                        continue

                    player_id = f"espn_{raw_id}"
                    player = p.get("player") or {}

                    name = (
                        player.get("fullName")
                        or f"{player.get('firstName', '')} {player.get('lastName', '')}".strip()
                        or str(raw_id)
                    )

                    position_id = player.get("defaultPositionId")
                    position = config.POSITION_MAP.get(
                        position_id,
                        f"pos_{position_id}" if position_id is not None else None,
                    )

                    pro_team_id = player.get("proTeamId")
                    raw_eligible_slots = player.get("eligibleSlots") or []
                    eligible_slots = [
                        config.SLOT_MAP[slot_id]
                        for slot_id in raw_eligible_slots
                        if slot_id in config.SLOT_MAP
                    ]

                    onteam_id = p.get("onTeamId", 0)
                    team_id = onteam_id if onteam_id is not None and onteam_id > 0 else None

                    projected = None
                    stats = player.get("stats") or []
                    for stat in stats:
                        if not isinstance(stat, dict):
                            continue
                        if stat.get("scoringPeriodId") == week and stat.get("statSourceId") == 1:
                            projected = stat.get("appliedTotal")
                            break

                    lineup_slot_id = roster_slots.get(raw_id)
                    slot_name = config.SLOT_MAP.get(lineup_slot_id) if lineup_slot_id is not None else None
                    reserved = slot_name if slot_name in config.RESERVED_SLOT_NAMES else None

                    db.upsert_player(conn, player_id, name, position, pro_team_id, eligible_slots)
                    db.upsert_projection(conn, league_key, player_id, week, projected)
                    db.upsert_ownership(conn, league_key, player_id, week, team_id, reserved)


# ------------------------------------------------------------- Sleeper

def ingest_sleeper_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] (sleeper) fetching league settings & teams...")
    league_id = league_cfg["league_id"]
    league = sleeper_client.fetch_league_settings(league_id)

    users = sleeper_client.fetch_users(league_id)
    user_map = {u["user_id"]: u for u in users}
    rosters = sleeper_client.fetch_rosters(league_id)

    with conn:
        db.upsert_league(conn, league_key, league_id, season)

        for r in rosters:
            team_id = r["roster_id"]
            owner = user_map.get(r.get("owner_id"), {})
            manager_name = owner.get("display_name")
            team_name = (owner.get("metadata") or {}).get("team_name") or manager_name
            db.upsert_team(conn, league_key, team_id, team_name, manager_name)

        roster_positions = league.get("roster_positions", [])
        counts = Counter(roster_positions)
        for idx, (slot_name, count) in enumerate(counts.items()):
            db.upsert_setting(conn, league_key, idx, slot_name, count)


def fetch_sleeper_week_projections(season, week):
    projections = sleeper_client.fetch_projections_week(season, week)
    return week, projections


def ingest_sleeper_weeks_parallel(conn, league_cfg, league_key, season, start_week, end_week):
    league_id = league_cfg["league_id"]
    league = sleeper_client.fetch_league_settings(league_id)
    points_field = sleeper_client.choose_points_field(league.get("scoring_settings", {}))
    has_ir_slot = "IR" in league.get("roster_positions", [])

    rosters = sleeper_client.fetch_rosters(league_id)
    ownership_map = {}
    reserved_map = {}
    for r in rosters:
        for pid in (r.get("players") or []):
            ownership_map[pid] = r["roster_id"]
        for pid in (r.get("reserve") or []):
            reserved_map[pid] = "IR"
        for pid in (r.get("taxi") or []):
            reserved_map[pid] = "TAXI"

    weeks = list(range(start_week, end_week + 1))
    print(f"[{league_key}] (sleeper) fetching weeks {start_week}-{end_week} projections in parallel...")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(fetch_sleeper_week_projections, season, w): w for w in weeks}

        for future in concurrent.futures.as_completed(futures):
            week, projections = future.result()
            print(f"[{league_key}] (sleeper) week {week}: {len(projections)} projection entries")

            seen_raw_ids = set()
            with conn:
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
                    team_id = ownership_map.get(raw_id)

                    db.upsert_player(conn, player_id, name, position, None, eligible_slots)
                    db.upsert_projection(conn, league_key, player_id, week, projected)
                    db.upsert_ownership(conn, league_key, player_id, week, team_id, reserved_map.get(raw_id))

                for raw_id, team_id in ownership_map.items():
                    if raw_id not in seen_raw_ids:
                        player_id = f"sleeper_{raw_id}"
                        db.upsert_ownership(conn, league_key, player_id, week, team_id, reserved_map.get(raw_id))
                        db.upsert_projection(conn, league_key, player_id, week, None)


# ------------------------------------------------------------ Schedule

def matchups_from_config(schedule_cfg: dict, regular_season_weeks: int) -> list:
    return [
        (week, a, b, None, None)
        for week, pairs in sorted(schedule_cfg.items())
        if week <= regular_season_weeks
        for a, b in pairs
    ]


def sleeper_matchups(league_id, regular_season_weeks: int, played_through: int) -> list:
    matchups = []
    for week in range(1, regular_season_weeks + 1):
        by_matchup = {}
        for entry in sleeper_client.fetch_matchups(league_id, week):
            matchup_id = entry.get("matchup_id")
            if matchup_id is None:
                continue
            by_matchup.setdefault(matchup_id, []).append(entry)

        completed = week <= played_through
        for pair in by_matchup.values():
            if len(pair) != 2:
                continue
            a, b = pair
            matchups.append((
                week, a["roster_id"], b["roster_id"],
                a.get("points") if completed else None,
                b.get("points") if completed else None,
            ))
    return matchups


def ingest_schedule(conn, league_cfg, league_key, platform, season):
    try:
        settings = league_info.playoff_settings(league_key)
    except ValueError as e:
        print(f"[{league_key}] skipping schedule ingest: {e}")
        return

    regular_season_weeks = settings["regular_season_weeks"]
    try:
        if league_cfg.get("schedule"):
            source = "config"
            matchups = matchups_from_config(league_cfg["schedule"], regular_season_weeks)
        elif platform == "espn":
            source = "espn"
            entries = espn_client.fetch_schedule(league_cfg, season)
            matchups = espn_client.parse_schedule(entries, regular_season_weeks)
        else:
            source = "sleeper"
            played_through = league_info.current_week(last_week=settings["end_week"]) - 1
            matchups = sleeper_matchups(league_cfg["league_id"], regular_season_weeks, played_through)
    except Exception as e:
        print(f"[{league_key}] WARNING: schedule ingest failed: {e!r}")
        return

    if not matchups:
        print(f"[{league_key}] WARNING: no regular-season matchups found ({source})")
        return

    with conn:
        db.replace_schedule(conn, league_key, matchups)
    played = sum(1 for m in matchups if m[3] is not None)
    print(f"[{league_key}] schedule ({source}): {len(matchups)} matchups over "
          f"{regular_season_weeks} regular-season weeks, {played} already played")


# --------------------------------------------------------------- worker & main

def process_single_league(league_cfg, db_path):
    # Each thread/task gets its own connection for safe concurrent writing in WAL mode
    conn = db.get_conn(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=OFF;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA busy_timeout=30000;")

    league_start = time.perf_counter()
    league_key = league_cfg["name"]
    platform = league_cfg.get("platform", "espn")
    end_week = league_info.league_end_week(league_key)

    print(f"=== Processing League: {league_key} ({platform}) ===")

    if platform == "espn":
        config.require_espn_credentials()
        ingest_espn_league_settings(conn, league_cfg, league_key, config.SEASON)
        ingest_schedule(conn, league_cfg, league_key, platform, config.SEASON)
        ingest_espn_weeks_parallel(conn, league_cfg, league_key, config.SEASON, config.START_WEEK, end_week)
    elif platform == "sleeper":
        ingest_sleeper_league_settings(conn, league_cfg, league_key, config.SEASON)
        ingest_schedule(conn, league_cfg, league_key, platform, config.SEASON)
        ingest_sleeper_weeks_parallel(conn, league_cfg, league_key, config.SEASON, config.START_WEEK, end_week)
    else:
        print(f"[{league_key}] unknown platform '{platform}', skipping")

    conn.close()
    league_duration = time.perf_counter() - league_start
    print(f"-> Finished '{league_key}' in {league_duration:.2f}s ({league_duration / 60:.2f}m)\n")


def main():
    start_total = time.perf_counter()

    print("Initializing database...")
    t_db = time.perf_counter()
    conn = db.get_conn(config.DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    db.init_schema(conn)
    conn.close()
    print(f"-> Database initialized in {time.perf_counter() - t_db:.2f}s\n")

    # Process multiple leagues concurrently using ThreadPoolExecutor
    print(f"Processing {len(config.LEAGUES)} league(s) in parallel...")
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(config.LEAGUES) or 1) as executor:
        futures = [executor.submit(process_single_league, league_cfg, config.DB_PATH) for league_cfg in config.LEAGUES]
        concurrent.futures.wait(futures)

    total_duration = time.perf_counter() - start_total
    print("=" * 45)
    print("Benchmark Complete (Max Performance)!")
    print(f"Data saved to: {config.DB_PATH}")
    print(
        f"Total Execution Time: {total_duration:.2f}s"
        f" ({total_duration / 60:.2f} minutes)"
    )
    print("=" * 45)


if __name__ == "__main__":
    main()