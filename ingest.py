"""
Pull every league's teams, roster settings, and weekly player
projections + ownership into the local SQLite database.

Usage:
    python ingest.py
"""

import config
import db
import espn_client


def ingest_league_settings(conn, league_cfg, league_key, season):
    print(f"[{league_key}] fetching league settings & teams...")
    data = espn_client.fetch_league_settings(league_cfg, season)

    db.upsert_league(conn, league_key, league_cfg["league_id"], season)

    # Teams
    for team in data.get("teams", []):
        team_id = team.get("id")
        team_name = (team.get("location", "") + " " + team.get("nickname", "")).strip() or team.get("name")
        owners = team.get("owners", [])
        manager_name = owners[0] if owners else None
        db.upsert_team(conn, league_key, team_id, team_name, manager_name)

    # Roster slot counts
    settings = data.get("settings", {})
    roster_settings = settings.get("rosterSettings", {})
    lineup_slot_counts = roster_settings.get("lineupSlotCounts", {})
    for slot_id_str, count in lineup_slot_counts.items():
        slot_id = int(slot_id_str)
        if count and count > 0:
            slot_name = config.SLOT_MAP.get(slot_id, f"slot_{slot_id}")
            db.upsert_setting(conn, league_key, slot_id, slot_name, count)

    conn.commit()


def ingest_week(conn, league_cfg, league_key, season, week):
    players, stat_id = espn_client.fetch_players_week(league_cfg, season, week)
    print(f"[{league_key}] week {week}: {len(players)} players")

    for p in players:
        player_id = p.get("id")
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

        db.upsert_player(conn, player_id, name, position, pro_team_id)
        if name == "Derrick Brown":
            print("\n=== RAW DERRICK BROWN ===")
            print(p)
            print("=== PLAYER OBJECT ===")
            print(player_obj)
            print("=== defaultPositionId ===")
            print(pos_id)
            print("=== mapped position ===")
            print(position)
        db.upsert_projection(conn, league_key, player_id, week, projected)
        db.upsert_ownership(conn, league_key, player_id, week, team_id)

    conn.commit()


def main():
    conn = db.get_conn(config.DB_PATH)
    db.init_schema(conn)

    for league_cfg in config.LEAGUES:
        league_key = league_cfg["name"]
        ingest_league_settings(conn, league_cfg, league_key, config.SEASON)

        for week in range(config.START_WEEK, config.END_WEEK + 1):
            ingest_week(conn, league_cfg, league_key, config.SEASON, week)

    conn.close()
    print("\nDone. Data saved to", config.DB_PATH)


if __name__ == "__main__":
    main()
