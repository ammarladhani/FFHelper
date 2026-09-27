"""
Web backend for the fantasy football optimizer - replaces app.py
(Streamlit) with a FastAPI JSON API served to a static dark-dashboard
frontend (index.html + static/). No backend logic lives here, same as
the old app.py: every endpoint is a thin wrapper around simulator.py /
waiver.py / trades.py / repo.py / season_records.py / win_probability.py
/ ingest.py.

Long, combinatorial searches (waiver pickups, waiver plan, trade finder,
data refresh) run on a background thread via jobs.py so the frontend can
poll for live progress instead of blocking one giant request - the same
progress bars app.py showed, just over HTTP instead of in-process.

Every request (and every background job) gets its OWN sqlite3.Connection
via get_conn() below, opened and closed within that request/job. There is
deliberately no shared module-level connection: FastAPI's sync route
handlers run in a thread pool and background jobs run on their own thread
entirely, so a single shared connection could end up with two threads
issuing statements on it at once - sqlite3's check_same_thread=False (set
in db.get_conn) only disables Python's own safety check, it doesn't make
concurrent use of one connection safe. Background-job closures open their
connection INSIDE the job function (on the job's own thread), not in the
request handler that queues the job.

Run with:
    python server.py
or, for auto-reload while developing:
    uvicorn server:app --reload
"""

import contextlib
import io
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware

import config
import db
import ingest
import jobs
import objectives
import payouts
import league_info
import repo
import season_records as records
import simulator
import trades
import waiver
import win_probability

app = FastAPI(title="Fantasy Football Optimizer")


class NoCacheMiddleware(BaseHTTPMiddleware):
    """This is a local, single-user tool that gets edited/updated often -
    browsers caching the old index.html/app.js/style.css after an update
    (and silently running stale code) causes far more confusion than the
    negligible cost of disabling caching here."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response


app.add_middleware(NoCacheMiddleware)


@contextlib.contextmanager
def get_conn():
    """A fresh sqlite3.Connection for exactly one request or one background
    job, closed when the `with` block exits. See the module docstring for
    why this replaced a single shared module-level connection."""
    c = db.get_conn(config.DB_PATH)
    try:
        yield c
    finally:
        c.close()


# Schema init just needs a connection for the duration of this call - not
# a long-lived one, so it uses the same helper as everything else.
with get_conn() as _schema_conn:
    db.init_schema(_schema_conn)


# ------------------------------------------------------------------ util

def _league_cfg(league: str) -> dict:
    try:
        return league_info.get_league(league)
    except KeyError:
        raise HTTPException(404, f"No league named '{league}' in config.LEAGUES")


def _bad_request(e: ValueError):
    raise HTTPException(400, str(e))


def _optimizer_objective(value: str) -> str:
    try:
        return objectives.normalize_objective(value)
    except ValueError as e:
        raise HTTPException(400, str(e))


def _effective_decay(objective: str, decay: Optional[float]) -> Optional[float]:
    # Money mode already accounts for time through the actual payout schedule,
    # so recency weighting would create a second, conflicting weighting layer.
    return None if objective == "money" else decay


def _require_money_objective(cfg: dict, league: str, objective: str):
    if objective == "money" and not (cfg.get("buy_in") is not None and cfg.get("payouts")):
        raise ValueError(
            f"League '{league}' has no payout scheme configured for money optimization."
        )


class _JobLogStream(io.TextIOBase):
    """Redirects print()'d lines from ingest.main() into a job's log so
    the frontend can show a live-scrolling refresh log, the rough
    equivalent of app.py's `with st.spinner(...)`."""

    def __init__(self, job: jobs.Job):
        self.job = job
        self._buf = ""

    def write(self, s):  # noqa: D401 - matches TextIOBase.write's contract
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self.job.log_line(line)
        return len(s)


# ---------------------------------------------------------------- static

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("index.html")


# --------------------------------------------------------------- leagues

@app.get("/api/leagues")
def list_leagues():
    """Every league from config.py, for the sidebar's league picker."""
    return [
        {
            "key": l["name"],
            "platform": l["platform"],
            "end_week": league_info.league_end_week(l["name"]),
        }
        for l in config.LEAGUES
    ]


@app.get("/api/leagues/{league}/meta")
def league_meta(league: str):
    """Teams, default team, and the week-range slider's bounds/defaults -
    everything the sidebar needs once a league is picked."""
    cfg = _league_cfg(league)
    end_week = league_info.league_end_week(league)
    with get_conn() as conn:
        teams = repo.get_teams(conn, league)
    if not teams:
        raise HTTPException(
            400,
            f"No teams found for '{league}' - has `python ingest.py` been run yet?",
        )
    return {
        "teams": teams,
        "end_week": end_week,
        "current_week": league_info.current_week(last_week=end_week),
        "default_team_id": cfg.get("my_team_id"),
        "default_decay": config.DEFAULT_DECAY,
        "default_std_fraction": config.DEFAULT_SCORE_STD_FRACTION,
        "money_objective_available": bool(cfg.get("buy_in") is not None and cfg.get("payouts")),
    }


# -------------------------------------------------------------- standings

@app.get("/api/leagues/{league}/standings")
def standings(league: str, start_week: int, end_week: int, decay: Optional[float] = None):
    _league_cfg(league)
    with get_conn() as conn:
        results = simulator.simulate_all_teams(conn, league, start_week, end_week, decay=decay)
    rows = [
        {"team_id": tid, "team_name": r["team_name"], "manager_name": r["manager_name"],
         "raw_total": round(r["raw_total"], 1), "weighted_total": round(r["weighted_total"], 1),
         "total": round(r["total"], 1)}
        for tid, r in results.items()
    ]
    rows.sort(key=lambda r: r["total"], reverse=True)
    return {"rows": rows, "decay": decay}


# --------------------------------------------------------------- records

@app.get("/api/leagues/{league}/records")
def projected_records(league: str, as_of_week: Optional[int] = None):
    _league_cfg(league)
    try:
        ps = league_info.playoff_settings(league)
    except ValueError as e:
        _bad_request(e)

    as_of = as_of_week or league_info.current_week(last_week=ps["end_week"])
    with get_conn() as conn:
        try:
            result = records.project_league(
                conn, league, end_week=ps["end_week"], regular_season_weeks=ps["regular_season_weeks"],
                playoff_teams=ps["playoff_teams"], playoff_weeks=ps["playoff_weeks"], as_of_week=as_of,
            )
        except ValueError as e:
            _bad_request(e)

    names = {r["team_id"]: r["team_name"] for r in result["teams"]}
    seeds = {r["team_id"]: r["seed"] for r in result["teams"]}

    def _named(tid):
        return {"team_id": tid, "team_name": names.get(tid), "seed": seeds.get(tid)}

    playoffs = result["playoffs"]
    rounds = []
    for rnd in playoffs["rounds"]:
        rounds.append({
            "week": rnd["week"],
            "byes": [_named(t) for t in rnd["byes"]],
            "matchups": [
                {**m, "team_a_name": names.get(m["team_a"]), "team_b_name": names.get(m["team_b"]),
                 "winner_name": names.get(m["winner"])}
                for m in rnd["matchups"]
            ],
        })

    teams = [{k: v for k, v in row.items() if k != "games"} for row in result["teams"]]

    return {
        "as_of_week": as_of,
        "regular_season_weeks": ps["regular_season_weeks"],
        "playoff_teams": ps["playoff_teams"],
        "playoff_weeks": ps["playoff_weeks"],
        "end_week": ps["end_week"],
        "teams": teams,
        "games_by_team": {row["team_id"]: row["games"] for row in result["teams"]},
        "playoffs": {"rounds": rounds, "champion": _named(playoffs["champion"]) if playoffs["champion"] else None},
        "champion": _named(result["champion"]["team_id"]) if result["champion"] else None,
    }


# ----------------------------------------------------------- win/champ odds

@app.get("/api/leagues/{league}/odds")
def odds(league: str, week: int, as_of_week: Optional[int] = None,
         std_fraction: float = config.DEFAULT_SCORE_STD_FRACTION):
    cfg = _league_cfg(league)
    try:
        ps = league_info.playoff_settings(league)
    except ValueError as e:
        _bad_request(e)

    as_of = as_of_week or league_info.current_week(last_week=ps["end_week"])

    with get_conn() as conn:
        schedule = repo.get_schedule(conn, league)
        sim = simulator.simulate_all_teams(conn, league, 1, ps["end_week"], as_of_week=as_of)
        weekly_means = {tid: r["weekly"] for tid, r in sim.items()}
        names = {tid: r["team_name"] for tid, r in sim.items()}

        matchups = win_probability.week_matchup_probabilities(schedule, weekly_means, week, std_fraction)
        for m in matchups:
            m["team_a_name"] = names.get(m["team_a"])
            m["team_b_name"] = names.get(m["team_b"])

        try:
            season = win_probability.project_league_probabilities(conn, league, ps["end_week"], ps["regular_season_weeks"],  ps["playoff_teams"], ps["playoff_weeks"], as_of_week=as_of, std_fraction=std_fraction)
        except ValueError as e:
            _bad_request(e)

        # Expected payouts ride along when the league has a buy-in/payout scheme configured.
        # A bad payout config is reported next to the card instead of failing the whole odds call.
        payout_data, payout_error = None, None
        if cfg.get("buy_in") is not None and cfg.get("payouts"):
            try:
                payout_data = payouts.expected_payouts(conn, league, as_of_week=as_of, std_fraction=std_fraction)
            except ValueError as e:
                payout_error = str(e)

    return {"as_of_week": as_of, "week": week, "matchups": matchups, "season": season,
            "payouts": payout_data, "payouts_error": payout_error}


@app.post("/api/leagues/{league}/odds/calibrate")
def calibrate(league: str, min_games: int = 8):
    _league_cfg(league)
    with get_conn() as conn:
        try:
            return win_probability.calibrate_std_fraction(conn, league, min_games=min_games)
        except ValueError as e:
            _bad_request(e)


# ------------------------------------------------------------------ waiver

class WaiverPickupsRequest(BaseModel):
    team_id: int
    start_week: int
    end_week: int
    as_of_week: Optional[int] = None
    top_n: int = 15
    fa_per_position: int = waiver.DEFAULT_FA_PER_POSITION
    decay: Optional[float] = None
    objective: str = "points"


@app.post("/api/leagues/{league}/waiver/pickups")
def waiver_pickups_start(league: str, req: WaiverPickupsRequest):
    # Validated on THIS (request) thread, so a bad league name surfaces as a
    # real 404 to the client. If this ran inside _run instead, jobs.py's
    # generic `except Exception` would catch the HTTPException on the job's
    # own thread and flatten it into a plain job.error string - the frontend
    # would see something like "404: No league named '...'" instead of an
    # actual 404 response.
    cfg = _league_cfg(league)

    def _run(job: jobs.Job):
        objective = _optimizer_objective(req.objective)
        _require_money_objective(cfg, league, objective)
        with get_conn() as conn:
            picks = waiver.best_pickups(
                conn, league, req.team_id, req.start_week, req.end_week,
                as_of_week=req.as_of_week, top_n=req.top_n,
                fa_per_position=req.fa_per_position,
                decay=_effective_decay(objective, req.decay),
                objective=objective,
                progress_callback=lambda *a, **kw: job.report(*a, **kw),
            )
        job.set_result({"picks": picks})

    return {"job_id": jobs.start(_run)}


class WaiverPlanRequest(BaseModel):
    team_id: int
    start_week: int
    end_week: int
    as_of_week: Optional[int] = None
    fa_per_position: int = waiver.DEFAULT_FA_PER_POSITION
    max_moves: int = 50
    decay: Optional[float] = None
    objective: str = "points"


@app.post("/api/leagues/{league}/waiver/plan")
def waiver_plan_start(league: str, req: WaiverPlanRequest):
    cfg = _league_cfg(league)  # validated on the request thread - see waiver_pickups_start

    def _run(job: jobs.Job):
        objective = _optimizer_objective(req.objective)
        _require_money_objective(cfg, league, objective)
        with get_conn() as conn:
            plan = waiver.plan_waiver_moves(
                conn, league, req.team_id, req.start_week, req.end_week,
                as_of_week=req.as_of_week, fa_per_position=req.fa_per_position,
                max_moves=req.max_moves,
                decay=_effective_decay(objective, req.decay),
                objective=objective,
                progress_callback=lambda *a, **kw: job.report(*a, **kw),
            )
        job.set_result(plan)

    return {"job_id": jobs.start(_run)}


@app.get("/api/leagues/{league}/waiver/explain")
def waiver_explain(league: str, team_id: int, add: str, drop: str, start_week: int, end_week: int,
                    as_of_week: Optional[int] = None, decay: Optional[float] = None,
                    objective: str = "points"):
    cfg = _league_cfg(league)
    objective = _optimizer_objective(objective)
    try:
        _require_money_objective(cfg, league, objective)
    except ValueError as e:
        _bad_request(e)
    with get_conn() as conn:
        try:
            return waiver.explain_pickup(
                conn, league, team_id, add, drop, start_week, end_week,
                as_of_week=as_of_week,
                decay=_effective_decay(objective, decay),
                objective=objective,
            )
        except ValueError as e:
            _bad_request(e)


# ------------------------------------------------------------------ trades

class TradeSuggestRequest(BaseModel):
    team_id: int
    start_week: int
    end_week: int
    as_of_week: Optional[int] = None
    partner_team_id: Optional[int] = None
    combo_sizes: List[int] = [1]
    decay: Optional[float] = None
    objective: str = "points"
    excluded_player_ids: List[str] = []
    included_player_ids: List[str] = []


@app.post("/api/leagues/{league}/trades/suggest")
def trade_suggest_start(league: str, req: TradeSuggestRequest):
    cfg = _league_cfg(league)  # validated on the request thread - see waiver_pickups_start

    def _run(job: jobs.Job):
        objective = _optimizer_objective(req.objective)
        _require_money_objective(cfg, league, objective)
        with get_conn() as conn:
            proposals = trades.suggest_trades(
                conn, league, req.team_id, req.start_week, req.end_week, as_of_week=req.as_of_week,
                partner_team_id=req.partner_team_id,
                combo_sizes=tuple(sorted(set(req.combo_sizes))) or (1,),
                decay=_effective_decay(objective, req.decay),
                objective=objective,
                excluded_player_ids=set(req.excluded_player_ids),
                included_player_ids=set(req.included_player_ids),
                progress_callback=lambda *a, **kw: job.report(*a, **kw),
            )
        job.set_result({"proposals": proposals})

    return {"job_id": jobs.start(_run)}


class TradeEvaluateRequest(BaseModel):
    team_a: int
    give: List[str]
    team_b: int
    get: List[str]
    start_week: int
    end_week: int
    as_of_week: Optional[int] = None
    decay: Optional[float] = None
    objective: str = "points"


@app.post("/api/leagues/{league}/trades/evaluate")
def trade_evaluate(league: str, req: TradeEvaluateRequest):
    cfg = _league_cfg(league)
    objective = _optimizer_objective(req.objective)
    try:
        _require_money_objective(cfg, league, objective)
    except ValueError as e:
        _bad_request(e)
    with get_conn() as conn:
        try:
            return trades.evaluate_trade(
                conn, league, req.team_a, req.give, req.team_b, req.get,
                req.start_week, req.end_week, as_of_week=req.as_of_week,
                decay=_effective_decay(objective, req.decay),
                objective=objective,
            )
        except ValueError as e:
            _bad_request(e)


@app.post("/api/leagues/{league}/trades/explain")
def trade_explain(league: str, req: TradeEvaluateRequest):
    cfg = _league_cfg(league)
    objective = _optimizer_objective(req.objective)
    try:
        _require_money_objective(cfg, league, objective)
    except ValueError as e:
        _bad_request(e)
    with get_conn() as conn:
        try:
            return trades.explain_trade(
                conn, league, req.team_a, req.give, req.team_b, req.get,
                req.start_week, req.end_week, as_of_week=req.as_of_week,
                decay=_effective_decay(objective, req.decay),
                objective=objective,
            )
        except ValueError as e:
            _bad_request(e)


# ----------------------------------------------------------------- rosters

@app.get("/api/leagues/{league}/roster")
def roster(league: str, team_id: Optional[int] = None, week: int = 1,
           end_week: Optional[int] = None, decay: Optional[float] = None):
    """Return a team's roster or free agents.

    Uses its own request-local connection via get_conn() - unchanged in
    spirit from before, now just sharing the same helper every other
    endpoint uses instead of hand-rolling its own open/close.
    """
    _league_cfg(league)

    with get_conn() as roster_conn:
        if team_id is None:
            player_ids = repo.get_free_agent_ids(roster_conn, league, week)
        else:
            player_ids = repo.get_roster_player_ids(
                roster_conn, league, team_id, week
            )

        players = repo.get_roster_with_projection(
            roster_conn, league, player_ids, week
        )

        reserved = (
            repo.get_reserved_player_ids(
                roster_conn, league, team_id, week
            )
            if team_id is not None
            else set()
        )

        end = end_week or league_info.league_end_week(league)
        totals = repo.get_projection_totals(
            roster_conn,
            league,
            [p["player_id"] for p in players],
            week,
            end,
            decay,
        )

        rows = []
        for p in players:
            t = totals.get(
                p["player_id"],
                {"raw": 0.0, "weighted": 0.0},
            )
            rows.append({
                **p,
                "reserved": p["player_id"] in reserved,
                "rest_of_season_raw": round(t["raw"], 1),
                "rest_of_season_weighted": round(t["weighted"], 1),
            })

        rows.sort(
            key=lambda r: (
                r["rest_of_season_weighted"]
                if decay is not None
                else r["rest_of_season_raw"]
            ),
            reverse=True,
        )

        return {"players": rows, "week": week, "end_week": end}


class MovePlayerRequest(BaseModel):
    player_id: str
    to_team_id: Optional[int] = None
    from_week: int


@app.post("/api/leagues/{league}/roster/move")
def move_player(league: str, req: MovePlayerRequest):
    _league_cfg(league)
    with get_conn() as conn:
        # db.move_player no longer commits internally (it now follows the
        # same "caller wraps in `with conn:`" convention as the rest of
        # db.py's write helpers), so this wrap is required here now.
        with conn:
            rows_updated = db.move_player(conn, league, req.player_id, req.to_team_id, req.from_week)
    if rows_updated == 0:
        raise HTTPException(
            400,
            f"No ownership rows updated - is week {req.from_week} within the range "
            "covered by ingest.py for this league?",
        )
    return {"rows_updated": rows_updated}


# ------------------------------------------------------------------ refresh

@app.post("/api/refresh")
def refresh_start():
    """Re-runs ingest.py for every configured league, streaming its
    print() output into the job's log so the frontend can show a live
    scrolling status (the web equivalent of app.py's refresh spinner).
    ingest.main() opens its own per-league connections internally
    (see ingest.process_single_league), so this job doesn't need one."""

    def _run(job: jobs.Job):
        job.report(0, None, "Starting refresh...")
        stream = _JobLogStream(job)
        with contextlib.redirect_stdout(stream):
            ingest.main()
        job.report(1, 1, "Done.")
        job.set_result({"ok": True})

    return {"job_id": jobs.start(_run)}


# -------------------------------------------------------------------- jobs

@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Unknown job id")
    return JSONResponse(job.to_dict())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=False)