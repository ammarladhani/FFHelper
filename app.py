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
import repo
import simulator
import waiver
import trades

st.set_page_config(page_title="Fantasy Optimizer", page_icon="🏈", layout="wide")


@st.cache_resource
def get_connection():
    return db.get_conn(config.DB_PATH)


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

start_week, end_week = st.sidebar.slider("Week range", 1, 18, (1, 18))
st.sidebar.caption(f"League key: `{league}` · Team ID: `{my_team_id}`")
st.sidebar.caption(
    "Switching league, team, or week range doesn't automatically re-run a search - "
    "hit the button again in whichever tab you're using."
)


def _labeled_player_map(conn, player_ids):
    """name -> player_id, but with the player_id appended when two players
    on the same roster happen to share a display name (e.g. two IDP guys
    with common names) so the dropdown never silently collides them."""
    infos = [repo.get_player_info(conn, pid) for pid in player_ids]
    name_counts = {}
    for info in infos:
        if info:
            name_counts[info["name"]] = name_counts.get(info["name"], 0) + 1

    labeled = {}
    for info in infos:
        if not info:
            continue
        if name_counts[info["name"]] > 1:
            label = f"{info['name']} ({info['player_id']})"
        else:
            label = info["name"]
        labeled[label] = info["player_id"]
    return labeled


tab_standings, tab_waiver, tab_trade_finder, tab_trade_eval, tab_rosters, tab_best_lineup = st.tabs(
    ["📊 Standings", "🔄 Waiver Wire", "🤝 Trade Finder", "⚖️ Evaluate Trade", "📋 Rosters", "🏆 Best Lineup"]
)

# ------------------------------------------------------------- standings

with tab_standings:
    st.subheader(f"Projected totals, weeks {start_week}-{end_week}")
    st.caption("Every team's optimal starting lineup, week by week, summed to a season total.")
    if st.button("Run simulation", key="sim_btn"):
        with st.spinner("Simulating every team's optimal lineup, week by week..."):
            results = simulator.simulate_all_teams(conn, league, start_week, end_week)
        rows = sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True)
        df = pd.DataFrame([
            {"Team": r["team_name"], "Manager": r["manager_name"], "Projected Total": round(r["total"], 1)}
            for _, r in rows
        ])
        st.dataframe(df, width="stretch", hide_index=True)
        st.bar_chart(df.set_index("Team")["Projected Total"])

# ---------------------------------------------------------------- waiver

with tab_waiver:
    st.subheader(f"Best waiver moves for {my_team_label}")
    st.caption(
        "Searches for waiver moves that increase your team's projected "
        "optimized season total."
    )

    # ---------------------------------------------------------
    # Search settings
    # ---------------------------------------------------------
    c1, c2 = st.columns(2)

    top_n = c1.slider(
        "Show top N",
        5,
        100,
        10,
        key="waiver_topn",
    )

    fa_prefilter = c2.slider(
        "Top free agents per position",
        2,
        30,
        8,
        key="waiver_prefilter",
        help=(
            "Takes the top N free agents within each position based "
            "on projected points. This prevents positions such as "
            "K/DEF/IDP from being crowded out by higher-scoring QB/RB/WR."
        ),
    )

    search_2_for_2 = st.checkbox(
        "Also search 2-for-2 waivers (much slower)",
        value=False,
        key="waiver_2_for_2",
        help=(
            "Off = 1-for-1 waivers only. On = also searches combinations "
            "where you add two free agents and drop two players."
        ),
    )

    if search_2_for_2:
        c1, c2 = st.columns(2)

        waiver_2drop_prefilter = c1.slider(
            "2-for-2 drop candidates",
            4,
            20,
            10,
            key="waiver_2drop_prefilter",
            help=(
                "Lowest-projected players on your roster considered "
                "as possible drops."
            ),
        )

        # Show an estimate of the 2-for-2 search size.
        #
        # get_free_agents_ranked_by_position() returns up to N players
        # per position, so this is an estimate based on five position
        # groups. The actual number depends on your league.
        estimated_fa_count = fa_prefilter * 5

        estimated_fa_pairs = (
            estimated_fa_count
            * (estimated_fa_count - 1)
            // 2
        )

        estimated_drop_pairs = (
            waiver_2drop_prefilter
            * (waiver_2drop_prefilter - 1)
            // 2
        )

        estimated_combinations = (
            estimated_fa_pairs
            * estimated_drop_pairs
        )

        c2.metric(
            "Estimated 2-for-2 searches",
            f"{estimated_combinations:,}",
        )

    # ---------------------------------------------------------
    # Search
    # ---------------------------------------------------------
    if st.button("Find pickups", key="waiver_btn"):

        # -----------------------------------------------------
        # 1-for-1
        # -----------------------------------------------------
        with st.spinner(
            "Searching for best 1-for-1 waiver moves..."
        ):
            waiver_picks = waiver.best_pickups(
                conn,
                league,
                my_team_id,
                start_week,
                end_week,
                top_n=top_n,
                fa_prefilter=fa_prefilter,
            )

        st.session_state["waiver_picks"] = waiver_picks

        # -----------------------------------------------------
        # 2-for-2
        # -----------------------------------------------------
        if search_2_for_2:
            with st.spinner(
                "Searching for best 2-for-2 waiver moves..."
            ):
                waiver_2_for_2_picks = waiver.best_pickups_2_for_2(
                    conn,
                    league,
                    my_team_id,
                    start_week,
                    end_week,
                    top_n=top_n,
                    fa_prefilter=fa_prefilter,
                    drop_prefilter=waiver_2drop_prefilter,
                )

            st.session_state["waiver_2_for_2_picks"] = (
                waiver_2_for_2_picks
            )
        else:
            st.session_state["waiver_2_for_2_picks"] = []

    # ---------------------------------------------------------
    # Results
    # ---------------------------------------------------------
    waiver_picks = st.session_state.get(
        "waiver_picks",
        [],
    )

    waiver_2_for_2_picks = st.session_state.get(
        "waiver_2_for_2_picks",
        [],
    )

    if not waiver_picks and not waiver_2_for_2_picks:
        st.info("Click 'Find pickups' to search.")

    # ---------------------------------------------------------
    # 1-for-1 results
    # ---------------------------------------------------------
    if waiver_picks:
        st.write("### 1-for-1 Waivers")

        st.caption(
            f"Showing {len(waiver_picks)} best 1-for-1 waiver moves"
        )

        for i, p in enumerate(waiver_picks):
            add = p["add"]
            drop = p["drop"]

            add_name = add["name"] if add else "?"
            drop_name = drop["name"] if drop else "?"

            c1, c2, c3 = st.columns([4, 4, 1])

            c1.write(f"**Add:** {add_name}")
            c2.write(f"**Drop:** {drop_name}")
            c3.metric(
                "Gain",
                f"+{p['projected_gain']:.1f}",
            )

            with st.expander(
                "Why does this help? (week by week)",
                key=f"waiver_explain_{i}",
            ):
                if add and drop:
                    explanation = waiver.explain_pickup(
                        conn,
                        league,
                        my_team_id,
                        add["player_id"],
                        drop["player_id"],
                        start_week,
                        end_week,
                    )

                    weeks = sorted(
                        explanation["weekly_before"].keys()
                    )

                    chart_df = pd.DataFrame({
                        "Week": weeks,
                        "Before": [
                            explanation["weekly_before"][w]
                            for w in weeks
                        ],
                        "After": [
                            explanation["weekly_after"][w]
                            for w in weeks
                        ],
                    }).set_index("Week")

                    st.line_chart(chart_df)

                    st.dataframe(
                        chart_df.reset_index(),
                        hide_index=True,
                        width="stretch",
                    )

    # ---------------------------------------------------------
    # 2-for-2 results
    # ---------------------------------------------------------
    if waiver_2_for_2_picks:
        st.write("### 2-for-2 Waivers")

        st.caption(
            f"Showing {len(waiver_2_for_2_picks)} best 2-for-2 "
            "waiver moves"
        )

        for i, p in enumerate(waiver_2_for_2_picks):
            add_players = p["add"]
            drop_players = p["drop"]

            add_str = ", ".join(
                x["name"]
                for x in add_players
                if x
            )

            drop_str = ", ".join(
                x["name"]
                for x in drop_players
                if x
            )

            c1, c2, c3 = st.columns([4, 4, 1])

            c1.write(f"**Add:** {add_str}")
            c2.write(f"**Drop:** {drop_str}")
            c3.metric(
                "Gain",
                f"+{p['projected_gain']:.1f}",
            )

            with st.expander(
                "Why does this help? (week by week)",
                key=f"waiver_2for2_explain_{i}",
            ):
                explanation = waiver.explain_pickup_2_for_2(
                    conn,
                    league,
                    my_team_id,
                    [
                        x["player_id"]
                        for x in add_players
                        if x
                    ],
                    [
                        x["player_id"]
                        for x in drop_players
                        if x
                    ],
                    start_week,
                    end_week,
                )

                weeks = sorted(
                    explanation["weekly_before"].keys()
                )

                chart_df = pd.DataFrame({
                    "Week": weeks,
                    "Before": [
                        explanation["weekly_before"][w]
                        for w in weeks
                    ],
                    "After": [
                        explanation["weekly_after"][w]
                        for w in weeks
                    ],
                }).set_index("Week")

                st.line_chart(chart_df)

                st.dataframe(
                    chart_df.reset_index(),
                    hide_index=True,
                    width="stretch",
                )

    # ---------------------------------------------------------
    # Frequency charts
    # ---------------------------------------------------------
    if waiver_picks:
        st.divider()

        st.write("### 1-for-1 Waiver Trends")

        give_counts = {}
        drop_counts = {}

        for p in waiver_picks:
            add = p["add"]
            drop = p["drop"]

            if add:
                give_counts[add["name"]] = (
                    give_counts.get(add["name"], 0) + 1
                )

            if drop:
                drop_counts[drop["name"]] = (
                    drop_counts.get(drop["name"], 0) + 1
                )

        colA, colB = st.columns(2)

        with colA:
            st.write("**Most frequently ADDED**")

            if give_counts:
                st.bar_chart(
                    pd.Series(give_counts)
                    .sort_values(ascending=False)
                )

        with colB:
            st.write("**Most frequently DROPPED**")

            if drop_counts:
                st.bar_chart(
                    pd.Series(drop_counts)
                    .sort_values(ascending=False)
                )

    if waiver_2_for_2_picks:
        st.divider()

        st.write("### 2-for-2 Waiver Trends")

        add_counts = {}
        drop_counts = {}

        for p in waiver_2_for_2_picks:
            for player in p["add"]:
                if player:
                    name = player["name"]
                    add_counts[name] = (
                        add_counts.get(name, 0) + 1
                    )

            for player in p["drop"]:
                if player:
                    name = player["name"]
                    drop_counts[name] = (
                        drop_counts.get(name, 0) + 1
                    )

        colA, colB = st.columns(2)

        with colA:
            st.write("**Most frequently ADDED**")

            if add_counts:
                st.bar_chart(
                    pd.Series(add_counts)
                    .sort_values(ascending=False)
                )

        with colB:
            st.write("**Most frequently DROPPED**")

            if drop_counts:
                st.bar_chart(
                    pd.Series(drop_counts)
                    .sort_values(ascending=False)
                )


# ---------------------------------------------------------- trade finder

with tab_trade_finder:
    st.subheader(f"Win-win trades for {my_team_label}")
    st.caption("Searches for trades where BOTH sides' projected season totals go up.")

    partner_labels = {"Any team": None}
    partner_labels.update({t["team_name"]: t["team_id"] for t in teams if t["team_id"] != my_team_id})
    partner_choice = st.selectbox("Trade partner", list(partner_labels.keys()), key="partner_select")
    partner_team_id = partner_labels[partner_choice]

    c1, c2 = st.columns(2)
    prefilter = c1.slider(
        "Search depth (prefilter)", 4, 23, 12, key="trade_prefilter",
        help="Only the top N players per roster (by raw projected points) are considered as trade pieces.",
    )
    top_n = c2.slider("Show top N", 5, 1000, 10, key="trade_topn")

    deep_search = st.checkbox(
        "Also search 2-for-2 trades (much slower)",
        value=False,
        key="trade_deep_search",
        help="Off = 1-for-1 trades only (fast). On = also tries 2-for-2 swaps, "
             "which grows combinatorially and can take a lot longer, especially "
             "with a high search depth or 'Any team' as the partner.",
    )
    combo_sizes = (1, 2) if deep_search else (1,)

    if st.button("Find trades", key="trade_btn"):
        spinner_msg = "Searching for win-win trades..."
        if deep_search:
            spinner_msg += " (2-for-2 included, this can take a while)"
        with st.spinner(spinner_msg):
            all_proposals = trades.suggest_trades(
                conn, league, my_team_id, start_week, end_week,
                candidate_prefilter=prefilter, partner_team_id=partner_team_id,
                combo_sizes=combo_sizes,
            )
        st.session_state["trade_proposals"] = all_proposals

    all_proposals = st.session_state.get("trade_proposals", [])
    if not all_proposals:
        st.info("Click 'Find trades' to search.")
    else:
        proposals = all_proposals[:top_n]
        st.caption(f"Showing {len(proposals)} of {len(all_proposals)} win-win trades found")
        for p in proposals:
            give_str = ", ".join(x["name"] for x in p["give"])
            get_str = ", ".join(x["name"] for x in p["get"])
            st.write(
                f"**Give:** {give_str} &nbsp;→&nbsp; **Get:** {get_str}  "
                f"&nbsp;&nbsp;(you: +{p['my_delta']:.1f}, {p['partner_team_name']}: +{p['partner_delta']:.1f})"
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
        other_ids = repo.get_roster_player_ids(conn, league, other_team_id, start_week)
        my_names = _labeled_player_map(conn, my_ids)
        other_names = _labeled_player_map(conn, other_ids)

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
                    start_week, end_week,
                )
                a, b = result["team_a"], result["team_b"]

                m1, m2 = st.columns(2)
                m1.metric(my_team_label, f"{a['total_after']:.1f}", f"{a['delta']:+.1f}")
                m2.metric(other_team_label, f"{b['total_after']:.1f}", f"{b['delta']:+.1f}")

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
    roster_week = st.number_input("As of week", min_value=1, max_value=18, value=start_week, key="roster_week")

    player_ids = repo.get_roster_player_ids(conn, league, roster_team_id, roster_week)
    players = repo.get_roster_with_projection(conn, league, player_ids, roster_week)
    if not players:
        st.info("No players found for this team/week.")
    else:
        df = pd.DataFrame([
            {"Name": p["name"], "Position": p["position"], "Projected": p["projected"]}
            for p in players
        ]).sort_values("Projected", ascending=False, na_position="last")
        st.dataframe(df, width="stretch", hide_index=True)

# ---------------------------------------------------------- best lineup / roster

with tab_best_lineup:
    st.subheader("Optimal Lineup by Week")
    st.caption("View any team's highest-projected starting lineup and bench for a given week.")

    col_t, col_w = st.columns(2)
    best_team_label = col_t.selectbox("Team", label_list, index=default_index, key="best_team_select")
    best_team_id = team_labels[best_team_label]
    best_week = col_w.number_input("Week", min_value=1, max_value=18, value=start_week, key="best_week_select")

    best_result = simulator.get_best_lineup_for_week(conn, league, best_team_id, int(best_week))

    if not best_result["lineup"] and not best_result["bench"]:
        st.info("No roster data available for this team and week.")
    else:
        st.metric("Optimal Lineup Projected Points", f"{best_result['total_points']:.2f}")

        st.write("### 🏈 Starting Lineup")
        starter_rows = []
        for slot_name, players_assigned in best_result["lineup"].items():
            for p in players_assigned:
                starter_rows.append({
                    "Slot": slot_name,
                    "Name": p["name"],
                    "Position": p["position"],
                    "Projected Points": round(p["projected"], 2) if p.get("projected") is not None else 0.0,
                })

        if starter_rows:
            df_starters = pd.DataFrame(starter_rows)
            st.dataframe(df_starters, width="stretch", hide_index=True)
        else:
            st.write("No starters assigned.")

        st.write("### 🪑 Bench")
        bench_rows = [
            {
                "Name": p["name"],
                "Position": p["position"],
                "Projected Points": round(p.get("projected") or 0.0, 2),
            }
            for p in best_result["bench"]
        ]
        if bench_rows:
            df_bench = pd.DataFrame(bench_rows).sort_values("Projected Points", ascending=False)
            st.dataframe(df_bench, width="stretch", hide_index=True)
        else:
            st.write("No bench players.")