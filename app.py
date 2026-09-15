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

start_week, end_week = st.sidebar.slider("Week range", 1, 18, (1, 18))
st.sidebar.caption(f"League key: `{league}` · Team ID: `{my_team_id}`")

# Results are cached in session_state so they survive re-runs from other
# widgets, but they need to be keyed by (league, team, week range) -
# otherwise switching teams in the sidebar kept showing the PREVIOUS
# team's waiver picks / trade proposals under the new team's label until
# you clicked the button again, which is a good way to make someone drop
# the wrong player.
scope_key = f"{league}::{my_team_id}::{start_week}-{end_week}"

# ------------------------------------------------------------- standings

tab_standings, tab_waiver, tab_trade_finder, tab_trade_eval, tab_rosters = st.tabs(
    ["📊 Standings", "🔄 Waiver Wire", "🤝 Trade Finder", "⚖️ Evaluate Trade", "📋 Rosters"]
)

with tab_standings:
    st.subheader(f"Projected totals, weeks {start_week}-{end_week}")
    if st.button("Run simulation", key="sim_btn"):
        with st.spinner("Simulating every team's optimal lineup, week by week..."):
            results = simulator.simulate_all_teams(conn, league, start_week, end_week)
        rows = sorted(results.items(), key=lambda kv: kv[1]["total"], reverse=True)
        df = pd.DataFrame([
            {"Team": r["team_name"], "Manager": r["manager_name"], "Projected Total": round(r["total"], 1)}
            for _, r in rows
        ])
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.bar_chart(df.set_index("Team")["Projected Total"])

# ---------------------------------------------------------------- waiver

with tab_waiver:
    st.subheader(f"Best pickups for {my_team_label}")
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
        c3.metric("Gain", f"+{p['projected_gain']:.1f}")
        with st.expander("Why does this help? (week by week)"):
            if add and drop:
                explanation = waiver.explain_pickup(
                    conn, league, my_team_id, add["player_id"], drop["player_id"],
                    start_week, end_week,
                )
                weeks = sorted(explanation["weekly_before"].keys())
                chart_df = pd.DataFrame({
                    "Week": weeks,
                    "Before": [explanation["weekly_before"][w] for w in weeks],
                    "After": [explanation["weekly_after"][w] for w in weeks],
                }).set_index("Week")
                st.line_chart(chart_df)
                st.dataframe(chart_df.reset_index(), hide_index=True, use_container_width=True)
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
            )
        st.session_state["waiver_plan"] = {"scope": plan_scope_key, "data": plan}

    cached_plan = st.session_state.get("waiver_plan")
    plan = cached_plan["data"] if cached_plan and cached_plan["scope"] == plan_scope_key else None
    if not plan:
        st.info("Click 'Plan moves' to search.")
    elif not plan["moves"]:
        st.write(f"Starting total: **{plan['starting_total']:.1f}** - no move found that improves it.")
    else:
        st.write(f"Starting total: **{plan['starting_total']:.1f}**  →  "
                 f"Final total: **{plan['final_total']:.1f}**  "
                 f"(total gain **+{plan['total_gain']:.1f}**)")
        for m in plan["moves"]:
            c1, c2, c3, c4 = st.columns([1, 3, 3, 2])
            c1.write(f"**Step {m['step']}**")
            c2.write(f"Add: {m['add']['name']}")
            c3.write(f"Drop: {m['drop']['name']}")
            c4.metric("Gain", f"+{m['gain']:.1f}")

        totals_df = pd.DataFrame({
            "Step": [0] + [m["step"] for m in plan["moves"]],
            "Running total": [plan["starting_total"]] + [m["running_total"] for m in plan["moves"]],
        }).set_index("Step")
        st.line_chart(totals_df)

# ---------------------------------------------------------- trade finder

with tab_trade_finder:
    st.subheader(f"Win-win trades for {my_team_label}")

    partner_labels = {"Any team": None}
    partner_labels.update({t["team_name"]: t["team_id"] for t in teams if t["team_id"] != my_team_id})
    partner_choice = st.selectbox("Trade partner", list(partner_labels.keys()), key="partner_select")
    partner_team_id = partner_labels[partner_choice]

    c1, c2, c3 = st.columns(3)
    prefilter = c1.slider("Search depth (prefilter)", 4, 16, 12, key="trade_prefilter")
    top_n = c2.slider("Show top N", 5, 50, 10, key="trade_topn")
    c3.caption("Higher search depth = slower but more thorough")

    trade_scope_key = f"{scope_key}::{partner_team_id}::{prefilter}"

    if st.button("Find trades", key="trade_btn"):
        with st.spinner("Searching for win-win trades... this can take a bit"):
            all_proposals = trades.suggest_trades(
                conn, league, my_team_id, start_week, end_week,
                candidate_prefilter=prefilter, partner_team_id=partner_team_id,
            )
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
        st.dataframe(df, use_container_width=True, hide_index=True)
