"""
Web frontend for the fantasy football optimizer, built on Streamlit.
Wraps every existing module (simulator, waiver, trades, repo) - no
backend logic lives here, just UI.

Run with:
    streamlit run app.py
"""

import pandas as pd
import streamlit as st

import config
import db
import ingest
import league_info
import season_records as records
import repo
import simulator
import waiver
import trades
import weighting

st.set_page_config(page_title="Fantasy Optimizer", page_icon="🏈", layout="wide")

FREE_AGENCY_LABEL = "🆓 Free Agency"

# Session/result caches that go stale the moment ownership changes -
# either from the manual "move a player" control or from re-running
# ingest.py - so both of those clear this list before rerunning.
STALE_ON_ROSTER_CHANGE = ("waiver_picks", "waiver_plan", "trade_proposals", "records_result")


@st.cache_resource
def get_connection():
    conn = db.get_conn(config.DB_PATH)
    db.init_schema(conn)   # <-- add this: safe/idempotent, adds `reserved` column if missing
    return conn


conn = get_connection()

st.title("🏈 Fantasy Football Optimizer")

# ---------------------------------------------------------------- sidebar

league_keys = [l["name"] for l in config.LEAGUES]
if not league_keys:
    st.error("No leagues configured in config.py.")
    st.stop()

league = st.sidebar.selectbox("League", league_keys)

teams = repo.get_teams(conn, league)
if not teams:
    st.sidebar.warning("No teams found for this league - has ingest.py been run?")
    st.stop()

team_labels = {f"{t['team_name']} ({t['manager_name']})": t["team_id"] for t in teams}
default_team_id = next((l.get("my_team_id") for l in config.LEAGUES if l["name"] == league), None)
default_label = next((lbl for lbl, tid in team_labels.items() if tid == default_team_id), None)
label_list = list(team_labels.keys())
default_index = label_list.index(default_label) if default_label in label_list else 0

my_team_label = st.sidebar.selectbox("Your team", label_list, index=default_index)
my_team_id = team_labels[my_team_label]

# The slider runs 1 .. THIS league's last week (ESPN and Sleeper can differ),
# and starts at the current week (rolls over every Sunday, see config.WEEK_1_START).
# Keyed per league so switching leagues gets its own range instead of carrying
# over a value that might be out of bounds for the other league's season length.
league_end = league_info.league_end_week(league)
current_week = league_info.current_week(last_week=league_end)
start_week, end_week = st.sidebar.slider(
    "Week range", 1, league_end, (current_week, league_end),
    key=f"week_range_{league}_{league_end}",
    help=f"Defaults to the current week ({current_week}) through the end of this league's "
         f"season (week {league_end}).",
)
st.sidebar.caption(f"League key: `{league}` · Team ID: `{my_team_id}` · Current week: {current_week}")

st.sidebar.divider()
use_weighting = st.sidebar.toggle(
    "Weight nearer weeks more heavily", value=False, key="use_weighting",
    help="Rank and sort by recency-weighted projected points instead of a plain sum: each "
         "week further out counts for less, since far-off projections are less reliable and "
         "near-term points are the ones you can act on.",
)
decay = None
if use_weighting:
    decay = st.sidebar.slider(
        "Weekly decay", 0.50, 0.99, config.DEFAULT_DECAY, 0.01, key="decay",
        help="Each week counts this fraction of the week before it. 0.90 = a point next week "
             "is worth 0.9 of a point this week; lower = more short-sighted.",
    )
    _w = weighting.week_weights(start_week, end_week, decay)
    st.sidebar.caption(
        f"Week {start_week} counts 1.00 → week {min(start_week + 4, end_week)} counts "
        f"{_w[min(start_week + 4, end_week)]:.2f} → week {end_week} counts {_w[end_week]:.2f}"
    )
GAIN_LABEL = "Weighted gain" if decay is not None else "Gain"

st.sidebar.divider()
if st.sidebar.button("🔄 Refresh data (run ingest.py)",
                      help="Re-pulls every league's teams, settings, rosters, schedule, and weekly "
                           "projections from ESPN/Sleeper. Takes a few minutes per league. "
                           "This overwrites any manual moves made in the Rosters tab, since "
                           "it re-derives real ownership from the platform."):
    with st.spinner("Running ingest.py - this can take a few minutes per league..."):
        try:
            ingest.main()
        except Exception as e:
            st.sidebar.error(f"Ingest failed: {e}")
        else:
            for key in STALE_ON_ROSTER_CHANGE:
                st.session_state.pop(key, None)
            st.sidebar.success("Data refreshed.")
            st.rerun()

# Results are cached in session_state so they survive re-runs from other
# widgets, but they need to be keyed by (league, team, week range, weighting) -
# otherwise switching teams in the sidebar kept showing the PREVIOUS
# team's waiver picks / trade proposals under the new team's label until
# you clicked the button again, which is a good way to make someone drop
# the wrong player. Weighting is part of the key for the same reason: a
# list ranked by plain points must not linger under the "weighted" toggle.
scope_key = f"{league}::{my_team_id}::{start_week}-{end_week}::decay={decay}"

# ------------------------------------------------------------- standings

tab_standings, tab_records, tab_waiver, tab_trade_finder, tab_trade_eval, tab_rosters = st.tabs(
    ["📊 Standings", "🏆 Projected Records", "🔄 Waiver Wire", "🤝 Trade Finder", "⚖️ Evaluate Trade", "📋 Rosters"]
)

with tab_standings:
    st.subheader(f"Projected totals, weeks {start_week}-{end_week}")
    if st.button("Run simulation", key="sim_btn"):
        with st.spinner("Simulating every team's optimal lineup, week by week..."):
            results = simulator.simulate_all_teams(conn, league, start_week, end_week, decay=decay)
        # `total` is the weighted total when weighting is on, the plain sum otherwise,
        # so this sort follows the sidebar toggle.
        rows = sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True)
        table = []
        for _, r in rows:
            entry = {"Team": r["team_name"], "Manager": r["manager_name"],
                     "Projected Total": round(r["raw_total"], 1)}
            if decay is not None:
                entry["Weighted Total"] = round(r["weighted_total"], 1)
            table.append(entry)
        df = pd.DataFrame(table)
        if decay is not None:
            st.caption(f"Sorted by weighted total (decay {decay:g}/week).")
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.bar_chart(df.set_index("Team")["Weighted Total" if decay is not None else "Projected Total"])

# ------------------------------------------------------- projected records

with tab_records:
    st.subheader("Projected records & league champion")
    try:
        playoff_cfg = league_info.playoff_settings(league)
    except ValueError as e:
        st.info(str(e))
    else:
        reg_weeks = playoff_cfg["regular_season_weeks"]
        st.caption(
            f"{playoff_cfg['playoff_teams']} of {len(teams)} teams make the playoffs · regular season = "
            f"weeks 1-{reg_weeks} · playoffs = weeks {reg_weeks + 1}-{playoff_cfg['end_week']}. Weeks "
            f"already played use actual scores; every other week goes to whichever team's projected "
            f"optimal lineup scores more (rosters as of week {start_week}). This view always covers the "
            f"whole season, regardless of the week-range slider's end or the weighting toggle."
        )

        records_scope_key = f"{league}::{start_week}"
        if st.button("Project records & champion", key="records_btn"):
            with st.spinner("Simulating every team's lineup for the whole season..."):
                try:
                    result = records.project_league(
                        conn, league,
                        end_week=playoff_cfg["end_week"], regular_season_weeks=reg_weeks,
                        playoff_teams=playoff_cfg["playoff_teams"],
                        playoff_weeks=playoff_cfg["playoff_weeks"], as_of_week=start_week,
                    )
                except ValueError as e:
                    st.error(str(e))
                else:
                    st.session_state["records_result"] = {"scope": records_scope_key, "data": result}

        cached_records = st.session_state.get("records_result")
        proj = cached_records["data"] if cached_records and cached_records["scope"] == records_scope_key else None
        if proj is None:
            st.info("Click 'Project records & champion' to run.")
        else:
            if proj["champion"]:
                st.success(f"🏆 Projected champion: **{proj['champion']['team_name']}** "
                           f"({proj['champion']['manager_name']})")

            st.dataframe(
                pd.DataFrame([
                    {"Seed": r["seed"], "Team": r["team_name"], "Manager": r["manager_name"],
                     "Record": records.record_str(r),
                     "Points For": round(r["points_for"], 1), "Points Against": round(r["points_against"], 1),
                     "Playoffs": "✅" if r["made_playoffs"] else "",
                     "Champion": "🏆" if r["is_champion"] else ""}
                    for r in proj["teams"]
                ]),
                use_container_width=True, hide_index=True,
            )

            names = {r["team_id"]: r["team_name"] for r in proj["teams"]}
            seed_of = {r["team_id"]: r["seed"] for r in proj["teams"]}

            st.write("**Projected playoffs**")
            for n, rnd in enumerate(proj["playoffs"]["rounds"], start=1):
                st.write(f"Round {n} · week {rnd['week']}")
                for tid in rnd["byes"]:
                    st.write(f"- #{seed_of[tid]} {names[tid]} — bye")
                for m in rnd["matchups"]:
                    st.write(
                        f"- #{m['seed_a']} {names[m['team_a']]} ({m['score_a']:.1f}) vs "
                        f"#{m['seed_b']} {names[m['team_b']]} ({m['score_b']:.1f}) → "
                        f"**{names[m['winner']]}**"
                    )

            with st.expander("Game-by-game: why does a team have this record?"):
                game_team = st.selectbox("Team", [r["team_name"] for r in proj["teams"]], key="records_game_team")
                team_row = next(r for r in proj["teams"] if r["team_name"] == game_team)
                if team_row["games"]:
                    st.dataframe(
                        pd.DataFrame([
                            {"Week": g["week"], "Opponent": names[g["opponent"]],
                             "Points For": round(g["points_for"], 1),
                             "Points Against": round(g["points_against"], 1),
                             "Result": g["result"],
                             "Source": "Actual" if g["actual"] else "Projected"}
                            for g in sorted(team_row["games"], key=lambda g: g["week"])
                        ]),
                        use_container_width=True, hide_index=True,
                    )
                else:
                    st.write("No games found for this team in the stored schedule.")

# ---------------------------------------------------------------- waiver

with tab_waiver:
    st.subheader(f"Best pickups for {my_team_label}")
    if decay is not None:
        st.caption(f"Ranked by weighted gain (decay {decay:g}/week); raw projected points shown underneath.")
    col1, col2, col3 = st.columns(3)
    top_n = col1.slider("How many to show", 5, 50, 15, key="waiver_topn")
    by_position = col2.checkbox("Search per-position", key="waiver_by_position",
                                 help="Take the top N free agents at EACH position, instead of one "
                                      "global top-N list that a deep position (WR) can flood out a "
                                      "shallow one (QB/TE/K) from entirely.")
    if by_position:
        fa_limit = col3.slider("Free agents per position", 5, 30, 10, key="waiver_fa_per_position")
    else:
        fa_limit = col3.slider("Free agents considered", 10, 100, 40, key="waiver_prefilter")

    if st.button("Find pickups", key="waiver_btn"):
        with st.spinner("Testing every free agent against every roster spot..."):
            picks = waiver.best_pickups(
                conn, league, my_team_id, start_week, end_week,
                top_n=top_n, by_position=by_position,
                fa_per_position=fa_limit if by_position else waiver.DEFAULT_FA_PER_POSITION,
                fa_prefilter=fa_limit if not by_position else waiver.DEFAULT_FA_PREFILTER,
                decay=decay,
            )
        st.session_state["waiver_picks"] = {"scope": scope_key, "data": picks}

    cached = st.session_state.get("waiver_picks")
    picks = cached["data"] if cached and cached["scope"] == scope_key else []
    if not picks:
        st.info("Click 'Find pickups' to search.")
    for p in picks:
        add, drop = p["add"], p["drop"]
        add_name = add["name"] if add else "?"
        drop_name = drop["name"] if drop else "?"
        c1, c2, c3 = st.columns([3, 3, 1])
        c1.write(f"**Add:** {add_name}")
        c2.write(f"**Drop:** {drop_name}")
        c3.metric(GAIN_LABEL, f"+{p['projected_gain']:.1f}")
        if decay is not None:
            c3.caption(f"{p['raw_gain']:+.1f} raw pts")
        with st.expander("Why does this help? (week by week)"):
            if add and drop:
                explanation = waiver.explain_pickup(
                    conn, league, my_team_id, add["player_id"], drop["player_id"],
                    start_week, end_week, decay=decay,
                )
                weeks = sorted(explanation["weekly_before"].keys())
                chart_df = pd.DataFrame({
                    "Week": weeks,
                    "Before": [explanation["weekly_before"][w] for w in weeks],
                    "After": [explanation["weekly_after"][w] for w in weeks],
                }).set_index("Week")
                st.line_chart(chart_df)
                st.dataframe(chart_df.reset_index(), hide_index=True, use_container_width=True)
                if decay is not None:
                    st.caption(f"Raw: {explanation['raw_delta']:+.1f} pts · "
                               f"Weighted: {explanation['delta']:+.1f} (decay {decay:g}/week)")
        st.divider()

    st.divider()
    st.subheader("Plan sequential moves")
    st.caption(
        "Finds the single best add/drop, applies it, then searches again against "
        "the resulting roster - repeating until no further move improves your total. "
        "Answers 'after that first drop, what should I do next?' instead of just the "
        "first move."
    )

    plan_scope_key = f"{scope_key}::{by_position}::{fa_limit}"

    if st.button("Plan moves", key="waiver_plan_btn"):
        with st.spinner("Chaining moves until nothing else helps... this can take a bit"):
            plan = waiver.plan_waiver_moves(
                conn, league, my_team_id, start_week, end_week,
                by_position=by_position,
                fa_per_position=fa_limit if by_position else waiver.DEFAULT_FA_PER_POSITION,
                fa_prefilter=fa_limit if not by_position else waiver.DEFAULT_FA_PREFILTER,
                decay=decay,
            )
        st.session_state["waiver_plan"] = {"scope": plan_scope_key, "data": plan}

    cached_plan = st.session_state.get("waiver_plan")
    plan = cached_plan["data"] if cached_plan and cached_plan["scope"] == plan_scope_key else None
    total_word = "weighted total" if decay is not None else "total"
    if not plan:
        st.info("Click 'Plan moves' to search.")
    elif not plan["moves"]:
        st.write(f"Starting {total_word}: **{plan['starting_total']:.1f}** - no move found that improves it.")
    else:
        st.write(f"Starting {total_word}: **{plan['starting_total']:.1f}**  →  "
                 f"Final {total_word}: **{plan['final_total']:.1f}**  "
                 f"(total gain **+{plan['total_gain']:.1f}**)")
        if decay is not None:
            st.caption(f"In raw projected points: {plan['starting_raw_total']:.1f} → "
                       f"{plan['final_raw_total']:.1f} ({plan['total_raw_gain']:+.1f})")
        for m in plan["moves"]:
            c1, c2, c3, c4 = st.columns([1, 3, 3, 2])
            c1.write(f"**Step {m['step']}**")
            c2.write(f"Add: {m['add']['name']}")
            c3.write(f"Drop: {m['drop']['name']}")
            c4.metric(GAIN_LABEL, f"+{m['gain']:.1f}")
            if decay is not None:
                c4.caption(f"{m['raw_gain']:+.1f} raw pts")

        totals_df = pd.DataFrame({
            "Step": [0] + [m["step"] for m in plan["moves"]],
            "Running total": [plan["starting_total"]] + [m["running_total"] for m in plan["moves"]],
        }).set_index("Step")
        st.line_chart(totals_df)

# ---------------------------------------------------------- trade finder

with tab_trade_finder:
    st.subheader(f"Win-win trades for {my_team_label}")
    if decay is not None:
        st.caption(f"'Win-win' and the sort order use weighted totals (decay {decay:g}/week); "
                   "raw projected-point changes shown in brackets.")

    partner_labels = {"Any team": None}
    partner_labels.update({t["team_name"]: t["team_id"] for t in teams if t["team_id"] != my_team_id})
    partner_choice = st.selectbox("Trade partner", list(partner_labels.keys()), key="partner_select")
    partner_team_id = partner_labels[partner_choice]

    c1, c2, c3 = st.columns(3)
    prefilter = c1.slider("Search depth (prefilter)", 4, 25, 12, key="trade_prefilter")
    top_n = c2.slider("Show top N", 5, 500, 10, key="trade_topn")
    c3.caption("Higher search depth = slower but more thorough")

    trade_scope_key = f"{scope_key}::{partner_team_id}::{prefilter}"

    if st.button("Find trades", key="trade_btn"):
        progress_bar = st.progress(0)
        status_text = st.empty()

        def _update_progress(considered, total):
            pct = min(considered / total, 1.0) if total else 1.0
            progress_bar.progress(pct)
            status_text.caption(f"Considered {considered:,} of {total:,} trade combinations...")

        all_proposals = trades.suggest_trades(
            conn, league, my_team_id, start_week, end_week,
            candidate_prefilter=prefilter, partner_team_id=partner_team_id,
            progress_callback=_update_progress, decay=decay,
        )

        progress_bar.empty()
        status_text.empty()
        st.session_state["trade_proposals"] = {"scope": trade_scope_key, "data": all_proposals}

    cached = st.session_state.get("trade_proposals")
    all_proposals = cached["data"] if cached and cached["scope"] == trade_scope_key else []
    if not all_proposals:
        st.info("Click 'Find trades' to search.")
    else:
        proposals = all_proposals[:top_n]
        st.caption(f"Showing {len(proposals)} of {len(all_proposals)} win-win trades found")
        for p in proposals:
            give_str = ", ".join(x["name"] for x in p["give"])
            get_str = ", ".join(x["name"] for x in p["get"])
            raw_note = ""
            if decay is not None:
                raw_note = f" [raw: you {p['my_raw_delta']:+.1f}, {p['partner_raw_delta']:+.1f}]"
            st.write(
                f"**Give:** {give_str} &nbsp;→&nbsp; **Get:** {get_str}  "
                f"&nbsp;&nbsp;(you: +{p['my_delta']:.1f}, {p['partner_team_name']}: +{p['partner_delta']:.1f}){raw_note}"
            )

        give_counts, get_counts = {}, {}
        for p in all_proposals:
            for x in p["give"]:
                give_counts[x["name"]] = give_counts.get(x["name"], 0) + 1
            for x in p["get"]:
                get_counts[x["name"]] = get_counts.get(x["name"], 0) + 1

        st.divider()
        colA, colB = st.columns(2)
        with colA:
            st.write("**Most frequently traded AWAY**")
            st.bar_chart(pd.Series(give_counts).sort_values(ascending=False))
        with colB:
            st.write("**Most frequently RECEIVED**")
            st.bar_chart(pd.Series(get_counts).sort_values(ascending=False))

# --------------------------------------------------------- trade evaluate

with tab_trade_eval:
    st.subheader("Evaluate a specific trade")

    other_labels = {t["team_name"]: t["team_id"] for t in teams if t["team_id"] != my_team_id}
    if not other_labels:
        st.info("No other teams in this league.")
    else:
        other_team_label = st.selectbox("Trade with", list(other_labels.keys()), key="eval_partner")
        other_team_id = other_labels[other_team_label]

        my_ids = repo.get_roster_player_ids(conn, league, my_team_id, start_week)
        my_reserved = repo.get_reserved_player_ids(conn, league, my_team_id, start_week)
        my_names = {
            repo.get_player_info(conn, pid)["name"]: pid
            for pid in my_ids if pid not in my_reserved
        }

        other_ids = repo.get_roster_player_ids(conn, league, other_team_id, start_week)
        other_reserved = repo.get_reserved_player_ids(conn, league, other_team_id, start_week)
        other_names = {
            repo.get_player_info(conn, pid)["name"]: pid
            for pid in other_ids if pid not in other_reserved
        }

        c1, c2 = st.columns(2)
        give_selection = c1.multiselect(f"You give ({my_team_label})", list(my_names.keys()))
        get_selection = c2.multiselect(f"You get ({other_team_label})", list(other_names.keys()))

        if st.button("Evaluate trade", key="eval_btn"):
            if not give_selection or not get_selection:
                st.warning("Pick at least one player on each side.")
            else:
                give_ids = [my_names[n] for n in give_selection]
                get_ids = [other_names[n] for n in get_selection]
                result = trades.explain_trade(
                    conn, league, my_team_id, give_ids, other_team_id, get_ids,
                    start_week, end_week, decay=decay,
                )
                a, b = result["team_a"], result["team_b"]

                # Headline numbers are always plain projected points; the
                # weighted view (when on) is added underneath.
                m1, m2 = st.columns(2)
                m1.metric(my_team_label, f"{a['raw_total_after']:.1f}", f"{a['raw_delta']:+.1f}")
                m2.metric(other_team_label, f"{b['raw_total_after']:.1f}", f"{b['raw_delta']:+.1f}")
                if decay is not None:
                    st.caption(f"Weighted (decay {decay:g}/week): {my_team_label} {a['delta']:+.1f}, "
                               f"{other_team_label} {b['delta']:+.1f}")

                for label, side in [(my_team_label, a), (other_team_label, b)]:
                    st.write(f"**{label}**")
                    weeks = sorted(side["weekly_before"].keys())
                    chart_df = pd.DataFrame({
                        "Week": weeks,
                        "Before": [side["weekly_before"][w] for w in weeks],
                        "After": [side["weekly_after"][w] for w in weeks],
                    }).set_index("Week")
                    st.line_chart(chart_df)

# -------------------------------------------------------------- rosters

with tab_rosters:
    st.subheader("Browse a roster")
    roster_team_label = st.selectbox("Team", label_list, key="roster_team_select")
    roster_team_id = team_labels[roster_team_label]
    roster_week = st.number_input("As of week", min_value=1, max_value=league_end,
                                   value=start_week, key="roster_week")

    player_ids = repo.get_roster_player_ids(conn, league, roster_team_id, roster_week)
    players = repo.get_roster_with_projection(conn, league, player_ids, roster_week)
    if not players:
        st.info("No players found for this team/week.")
    else:
        ros_totals = repo.get_projection_totals(
            conn, league, [p["player_id"] for p in players], roster_week, end_week, decay,
        )
        rows = []
        for p in players:
            row = {"Name": p["name"], "Position": p["position"], "Projected": p["projected"],
                   "Rest of season": round(ros_totals[p["player_id"]]["raw"], 1)}
            if decay is not None:
                row["Rest of season (weighted)"] = round(ros_totals[p["player_id"]]["weighted"], 1)
            rows.append(row)
        sort_col = "Rest of season (weighted)" if decay is not None else "Projected"
        df = pd.DataFrame(rows).sort_values(sort_col, ascending=False, na_position="last")
        st.caption(f"'Projected' is week {roster_week} only; 'Rest of season' covers weeks "
                   f"{roster_week}-{end_week}. Sorted by {sort_col.lower()}.")
        st.dataframe(df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Move a player")
    st.caption(
        "Reassigns a player straight in the local database - your team to another team's, "
        "another team's to yours, or anyone to/from free agency. This is a manual override "
        "for testing 'what if' scenarios (or fixing a roster ingest.py hasn't caught up on "
        "yet, e.g. a same-day waiver claim) - it does NOT touch ESPN/Sleeper, and the next "
        "'Refresh data' run will overwrite it with whatever the platform actually shows."
    )

    move_team_labels = {FREE_AGENCY_LABEL: None, **team_labels}
    move_label_list = list(move_team_labels.keys())

    move_effective_week = st.number_input(
        "Effective from week", min_value=1, max_value=league_end, value=int(start_week), key="move_effective_week",
        help="Applies to this week and every later week already in the database - i.e. it "
             "changes the roster for the rest of the season's simulations, not just one "
             "week's snapshot.",
    )

    mc1, mc2 = st.columns(2)
    from_label = mc1.selectbox("From", move_label_list, key="move_from_team")
    from_team_id = move_team_labels[from_label]

    if from_team_id is None:
        from_player_ids = repo.get_free_agent_ids(conn, league, move_effective_week)
    else:
        from_player_ids = repo.get_roster_player_ids(conn, league, from_team_id, move_effective_week)

    from_player_names = {}
    for pid in from_player_ids:
        info = repo.get_player_info(conn, pid)
        if info:
            from_player_names[f"{info['name']} ({info['position'] or '?'})"] = pid

    if not from_player_names:
        mc1.info("No players found there for that week.")
    else:
        player_label = mc1.selectbox("Player", sorted(from_player_names.keys()), key="move_player_select")
        player_id = from_player_names[player_label]

        to_options = [lbl for lbl in move_label_list if lbl != from_label]
        to_label = mc2.selectbox("To", to_options, key="move_to_team")
        to_team_id = move_team_labels[to_label]

        if st.button("Move player", key="move_player_btn"):
            rows_updated = db.move_player(conn, league, player_id, to_team_id, int(move_effective_week))
            if rows_updated == 0:
                st.warning(
                    f"No ownership rows updated - is week {move_effective_week} within the "
                    "range covered by ingest.py for this league?"
                )
            else:
                for key in STALE_ON_ROSTER_CHANGE:
                    st.session_state.pop(key, None)
                st.success(
                    f"Moved {player_label.split(' (')[0]} from {from_label} to {to_label}, "
                    f"effective week {move_effective_week}."
                )
                st.rerun()