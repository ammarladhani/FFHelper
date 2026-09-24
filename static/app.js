"use strict";

/* ==========================================================================
   API client
   ========================================================================== */

const API = {
  async get(path, params) {
    const url = new URL(path, window.location.origin);
    if (params) {
      Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") url.searchParams.set(k, v);
      });
    }
    const res = await fetch(url);
    return API._handle(res);
  },
  async post(path, body) {
    const res = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    return API._handle(res);
  },
  async _handle(res) {
    let data = null;
    try { data = await res.json(); } catch (e) { /* no body */ }
    if (!res.ok) {
      const msg = (data && data.detail) ? data.detail : `Request failed (${res.status})`;
      throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    }
    return data;
  },
};

function pollJob(jobId, onProgress) {
  return new Promise((resolve, reject) => {
    const tick = async () => {
      let j;
      try { j = await API.get(`/api/jobs/${jobId}`); } catch (e) { reject(e); return; }
      if (onProgress) onProgress(j);
      if (j.status === "done") resolve(j.result);
      else if (j.status === "error") reject(new Error(j.error || "Job failed"));
      else setTimeout(tick, 350);
    };
    tick();
  });
}

/* ==========================================================================
   Small DOM / formatting helpers
   ========================================================================== */

function $(id) { return document.getElementById(id); }
function esc(s) { const d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
function fmt1(n) { return (n === null || n === undefined || Number.isNaN(n)) ? "—" : Number(n).toFixed(1); }
function signed1(n) { if (n === null || n === undefined) return "—"; return (n >= 0 ? "+" : "") + Number(n).toFixed(1); }
function pillClass(n) { return n > 0 ? "positive" : (n < 0 ? "negative" : "neutral"); }
function pillHTML(n, opts) {
  opts = opts || {};
  return `<span class="pill ${pillClass(n)}">${signed1(n)}${opts.suffix || ""}</span>`;
}
function money(n) { return (n === null || n === undefined) ? "—" : "$" + Number(n).toFixed(2); }
function moneyPill(n) {
  return `<span class="pill ${pillClass(n)}">${n >= 0 ? "+" : "−"}$${Math.abs(n).toFixed(2)}</span>`;
}
function spinnerRow(msg) {
  return `<div class="loading-row"><div class="spinner"></div><span>${esc(msg || "Working...")}</span></div>`;
}
function emptyState(icon, text) {
  return `<div class="empty-state"><div class="ic">${icon}</div>${esc(text)}</div>`;
}
function errorBanner(msg) {
  return `<div class="error-banner">${esc(msg)}</div>`;
}
function progressHTML(job, label) {
  const p = job.progress || {};
  const indeterminate = p.total === null || p.total === undefined || p.total === 0;
  const pct = indeterminate ? 0 : Math.min(100, Math.round((p.current / p.total) * 100));
  const countText = indeterminate ? `${p.current || 0}` : `${p.current || 0} / ${p.total}`;
  return `
    <div class="progress-wrap">
      <div class="progress-track"><div class="progress-fill ${indeterminate ? "indeterminate" : ""}" style="width:${pct}%"></div></div>
      <div class="progress-label"><span>${esc(p.message || label || "Working...")}</span><span>${countText}</span></div>
    </div>`;
}

/* Chart.js dark-theme helpers -------------------------------------------- */

function destroyChart(canvas) { if (canvas && canvas._chart) { canvas._chart.destroy(); canvas._chart = null; } }

function barChart(canvas, labels, data, opts) {
  if (typeof Chart === "undefined" || !canvas) return;
  opts = opts || {};
  destroyChart(canvas);
  canvas._chart = new Chart(canvas, {
    type: "bar",
    data: { labels, datasets: [{ data, backgroundColor: opts.color || "#46d488", borderRadius: 4, maxBarThickness: 30 }] },
    options: {
      indexAxis: opts.horizontal ? "y" : "x",
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: "#1c222a" }, ticks: { color: "#8b93a1", font: { family: "Inter", size: 11 } } },
        y: { grid: { color: "#1c222a" }, ticks: { color: "#8b93a1", font: { family: "Inter", size: 11 } } },
      },
    },
  });
}

function lineChart(canvas, labels, series) {
  if (typeof Chart === "undefined" || !canvas) return;
  destroyChart(canvas);
  canvas._chart = new Chart(canvas, {
    type: "line",
    data: {
      labels,
      datasets: series.map((s) => ({
        label: s.label, data: s.data, borderColor: s.color, backgroundColor: s.color + "26",
        tension: 0.3, pointRadius: 3, pointBackgroundColor: s.color, fill: !!s.fill, borderWidth: 2,
      })),
    },
    options: {
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#8b93a1", font: { family: "Inter", size: 11 } } } },
      scales: {
        x: { grid: { color: "#1c222a" }, ticks: { color: "#8b93a1", font: { family: "Inter", size: 11 } } },
        y: { grid: { color: "#1c222a" }, ticks: { color: "#8b93a1", font: { family: "Inter", size: 11 } } },
      },
    },
  });
}

/* ==========================================================================
   Global state (sidebar filters shared by most views)
   ========================================================================== */

const state = {
  league: null,
  teams: [],
  teamId: null,
  startWeek: 1,
  endWeek: 1,
  weighted: false,
  decay: 0.9,
  currentWeek: 1,
  leagueEndWeek: 1,
  lastRunScope: {}, // viewKey -> scope string, to flag stale results after a filter change
};

function decayOrNull() { return state.weighted ? state.decay : null; }

function scopeKey(extra) {
  return `${state.league}::${state.teamId}::${state.startWeek}-${state.endWeek}::${decayOrNull()}${extra ? "::" + extra : ""}`;
}

function staleBanner(viewKey, extra) {
  const key = scopeKey(extra);
  if (state.lastRunScope[viewKey] && state.lastRunScope[viewKey] !== key) {
    return `<div class="info-banner">Filters changed since this was run &mdash; results below may be stale. Run again to refresh.</div>`;
  }
  return "";
}

/* ==========================================================================
   Sidebar wiring
   ========================================================================== */

async function initSidebar() {
  const leagues = await API.get("/api/leagues");
  const sel = $("league-select");
  sel.innerHTML = leagues.map((l) => `<option value="${esc(l.key)}">${esc(l.key)} (${esc(l.platform)})</option>`).join("");
  sel.addEventListener("change", () => loadLeague(sel.value));

  $("team-select").addEventListener("change", (e) => {
    state.teamId = Number(e.target.value);
    $("team-name-label").textContent = teamLabel(state.teamId);
    refreshTradePartnerOptions();
    if (activeView === "evaluate") loadEvaluateTeamOptions();
  });

  $("start-week").addEventListener("change", (e) => { state.startWeek = Number(e.target.value) || 1; });
  $("end-week").addEventListener("change", (e) => { state.endWeek = Number(e.target.value) || state.leagueEndWeek; });

  $("weight-toggle").addEventListener("change", (e) => {
    state.weighted = e.target.checked;
    $("decay-row").style.display = state.weighted ? "block" : "none";
    updateDecayHint();
  });
  $("decay-slider").addEventListener("input", (e) => {
    state.decay = Number(e.target.value);
    updateDecayHint();
  });

  $("refresh-btn").addEventListener("click", runRefresh);

  if (leagues.length) await loadLeague(leagues[0].key);
}

function teamLabel(teamId) {
  const t = state.teams.find((t) => t.team_id === teamId);
  return t ? `${t.team_name} (${t.manager_name || "—"})` : "";
}

async function loadLeague(leagueKey) {
  state.league = leagueKey;
  let meta;
  try {
    meta = await API.get(`/api/leagues/${leagueKey}/meta`);
  } catch (e) {
    for (const v of VIEWS) $(`${v.key}-body`).innerHTML = errorBanner(e.message);
    return;
  }
  state.teams = meta.teams;
  state.leagueEndWeek = meta.end_week;
  state.currentWeek = meta.current_week;
  state.decay = meta.default_decay;
  state.teamId = meta.default_team_id || meta.teams[0].team_id;
  state.startWeek = meta.current_week;
  state.endWeek = meta.end_week;
  state.oddsStdFraction = meta.default_std_fraction || 0.2;
  state.oddsNSims = meta.default_n_sims || 2000;
  state.lastRunScope = {};

  const teamSel = $("team-select");
  teamSel.innerHTML = state.teams
    .map((t) => `<option value="${t.team_id}">${esc(t.team_name)} (${esc(t.manager_name || "—")})</option>`)
    .join("");
  teamSel.value = String(state.teamId);

  $("start-week").value = state.startWeek;
  $("start-week").max = state.leagueEndWeek;
  $("end-week").value = state.endWeek;
  $("end-week").max = state.leagueEndWeek;
  $("week-hint").textContent = `Current week is ${state.currentWeek}. Season runs through week ${state.leagueEndWeek}.`;
  $("decay-slider").value = state.decay;
  $("current-week-badge").textContent = `Week ${state.currentWeek}`;
  $("team-name-label").textContent = teamLabel(state.teamId);
  updateDecayHint();

  clearResultsOnLeagueSwitch();
  refreshOddsDefaults();
  refreshTradePartnerOptions();
  onViewEnter(activeView);
}

function clearResultsOnLeagueSwitch() {
  // Standings/Records bodies are pure results containers (their controls
  // live in the view-header) - safe to wipe outright. The other views
  // keep persistent controls inside the same body element, so only their
  // inner results/progress containers are cleared.
  $("standings-body").innerHTML = "";
  $("records-body").innerHTML = "";
  ["odds-results", "w-results", "w-plan-results", "w-progress", "w-plan-progress",
   "t-results", "t-progress", "e-results"].forEach((id) => {
    const node = $(id);
    if (node) node.innerHTML = "";
  });
}

function updateDecayHint() {
  const s = state.startWeek, e = Math.min(state.endWeek, state.leagueEndWeek || state.endWeek);
  const mid = Math.min(s + 4, e);
  const w = (n) => Math.pow(state.decay, n - s).toFixed(2);
  $("decay-hint").textContent = state.weighted
    ? `Week ${s} counts 1.00 → week ${mid} counts ${w(mid)} → week ${e} counts ${w(e)}`
    : "";
}

/* ==========================================================================
   Refresh (ingest.py) job
   ========================================================================== */

let refreshing = false;

async function runRefresh() {
  if (refreshing) return;
  refreshing = true;
  $("refresh-btn").disabled = true;
  const statusEl = $("refresh-status"), textEl = $("refresh-status-text"), logEl = $("refresh-log");
  statusEl.className = "refresh-status busy";
  textEl.textContent = "Refreshing...";
  logEl.style.display = "block";
  logEl.textContent = "";

  try {
    const { job_id } = await API.post("/api/refresh");
    await pollJob(job_id, (j) => { logEl.textContent = (j.log || []).join("\n"); logEl.scrollTop = logEl.scrollHeight; });
    statusEl.className = "refresh-status ok";
    textEl.textContent = `Refreshed ${new Date().toLocaleTimeString()}`;
    await loadLeague(state.league); // teams/rosters may have changed
  } catch (e) {
    statusEl.className = "refresh-status";
    statusEl.style.color = "var(--danger)";
    textEl.textContent = "Refresh failed";
    logEl.textContent += `\n${e.message}`;
  } finally {
    refreshing = false;
    $("refresh-btn").disabled = false;
  }
}

/* ==========================================================================
   Nav / router
   ========================================================================== */

const VIEWS = [
  { key: "standings", label: "Standings", icon: "📊", crumb: "Season projection" },
  { key: "records", label: "Projected Records", icon: "🏆", crumb: "Records & playoffs" },
  { key: "odds", label: "Win Probabilities", icon: "🎲", crumb: "Monte Carlo odds" },
  { key: "waiver", label: "Waiver Wire", icon: "🔄", crumb: "Free agent search" },
  { key: "trades", label: "Trade Finder", icon: "🤝", crumb: "Win-win search" },
  { key: "evaluate", label: "Evaluate Trade", icon: "⚖️", crumb: "Specific trade" },
  { key: "rosters", label: "Rosters", icon: "📋", crumb: "Browse & move" },
];

let activeView = "standings";

function buildNav() {
  $("nav").innerHTML = VIEWS.map(
    (v) => `<button class="nav-item" data-view="${v.key}"><span class="ic">${v.icon}</span>${esc(v.label)}</button>`
  ).join("");
  $("nav").addEventListener("click", (e) => {
    const btn = e.target.closest(".nav-item");
    if (btn) switchView(btn.dataset.view);
  });
}

function switchView(key) {
  activeView = key;
  for (const v of VIEWS) {
    $(`view-${v.key}`).classList.toggle("hidden", v.key !== key);
  }
  document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.view === key));
  const meta = VIEWS.find((v) => v.key === key);
  $("view-title").textContent = meta.label;
  $("view-crumb").textContent = meta.crumb;
  onViewEnter(key);
}

function onViewEnter(key) {
  if (key === "rosters") loadRosterTeamOptions();
  if (key === "evaluate") loadEvaluateTeamOptions();
}

/* ==========================================================================
   VIEW: Standings
   ========================================================================== */

function initStandingsView() {
  $("standings-run").addEventListener("click", runStandings);
}

async function runStandings() {
  const body = $("standings-body");
  const btn = $("standings-run");
  btn.disabled = true;
  body.innerHTML = spinnerRow("Simulating every team's optimal lineup, week by week...");
  try {
    const data = await API.get(`/api/leagues/${state.league}/standings`, {
      start_week: state.startWeek, end_week: state.endWeek, decay: decayOrNull(),
    });
    state.lastRunScope.standings = scopeKey();
    renderStandings(data);
  } catch (e) {
    body.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderStandings(data) {
  const body = $("standings-body");
  const rows = data.rows;
  const max = Math.max(1, ...rows.map((r) => r.total));
  const weighted = data.decay !== null && data.decay !== undefined;

  const tableRows = rows.map((r, i) => `
    <tr class="${r.team_id === state.teamId ? "me" : ""}">
      <td class="num mono">${i + 1}</td>
      <td>${esc(r.team_name)} ${r.team_id === state.teamId ? '<span class="pill gold">you</span>' : ""}</td>
      <td>${esc(r.manager_name || "—")}</td>
      <td class="num mono">${fmt1(r.raw_total)}</td>
      ${weighted ? `<td class="num mono">${fmt1(r.weighted_total)}</td>` : ""}
      <td>
        <div class="rank-bar-cell">
          <div class="rank-bar-track"><div class="rank-bar-fill" style="width:${(r.total / max) * 100}%"></div></div>
        </div>
      </td>
    </tr>`).join("");

  body.innerHTML = `
    ${staleBanner("standings")}
    <div class="card">
      <div class="card-header">
        <h3>Weeks ${state.startWeek}–${state.endWeek}${weighted ? ` &middot; weighted (decay ${data.decay})` : ""}</h3>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>#</th><th>Team</th><th>Manager</th><th class="num">Projected</th>
            ${weighted ? '<th class="num">Weighted</th>' : ""}<th>Rank</th></tr></thead>
          <tbody>${tableRows}</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h3>Projected total by team</h3></div>
      <div class="chart-box"><canvas id="standings-chart"></canvas></div>
    </div>`;

  barChart($("standings-chart"), rows.map((r) => r.team_name), rows.map((r) => r.total), { color: "#46d488" });
}

/* ==========================================================================
   VIEW: Projected Records
   ========================================================================== */

function initRecordsView() {
  $("records-run").addEventListener("click", runRecords);
}

async function runRecords() {
  const body = $("records-body");
  const btn = $("records-run");
  btn.disabled = true;
  body.innerHTML = spinnerRow("Simulating every team's lineup for the whole season...");
  try {
    const data = await API.get(`/api/leagues/${state.league}/records`, { as_of_week: state.startWeek });
    renderRecords(data);
  } catch (e) {
    body.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderRecords(data) {
  const body = $("records-body");
  const teams = data.teams;
  const nameOf = {};
  teams.forEach((t) => { nameOf[t.team_id] = t.team_name; });

  let firstCut = false;
  const rows = teams.map((t) => {
    const cutRow = !t.made_playoffs && !firstCut && (firstCut = true);
    return `
      ${cutRow ? `<tr class="cut-row"><td colspan="8" style="text-align:center;color:var(--text-faint);font-size:11.5px;">— playoff cut (top ${data.playoff_teams}) —</td></tr>` : ""}
      <tr class="${t.team_id === state.teamId ? "me" : ""}">
        <td class="num mono">${t.seed}</td>
        <td>${esc(t.team_name)} ${t.is_champion ? '<span class="badge champ">🏆 champion</span>' : ""} ${t.made_playoffs && !t.is_champion ? '<span class="badge playoff">playoffs</span>' : ""}</td>
        <td>${esc(t.manager_name || "—")}</td>
        <td class="num mono">${t.wins}-${t.losses}${t.ties ? "-" + t.ties : ""}</td>
        <td class="num mono">${fmt1(t.points_for)}</td>
        <td class="num mono">${fmt1(t.points_against)}</td>
      </tr>`;
  }).join("");

  const roundsHTML = data.playoffs.rounds.map((rnd) => `
    <div class="bracket-round">
      <div class="bracket-title">Week ${rnd.week}</div>
      ${rnd.byes.map((b) => `<div class="bracket-bye">#${b.seed} ${esc(b.team_name)} — bye</div>`).join("")}
      ${rnd.matchups.map((m) => `
        <div class="bracket-matchup">
          <div class="bracket-team ${m.winner === m.team_a ? "winner" : ""}">
            <span>#${m.seed_a} ${esc(m.team_a_name)}</span><span class="score">${fmt1(m.score_a)}</span>
          </div>
          <div class="bracket-team ${m.winner === m.team_b ? "winner" : ""}">
            <span>#${m.seed_b} ${esc(m.team_b_name)}</span><span class="score">${fmt1(m.score_b)}</span>
          </div>
        </div>`).join("")}
    </div>`).join("");

  const teamOptions = teams.map((t) => `<option value="${t.team_id}">${esc(t.team_name)}</option>`).join("");

  body.innerHTML = `
    ${data.champion ? `<div class="success-banner">🏆 Projected champion: <strong>${esc(data.champion.team_name)}</strong></div>` : ""}
    <div class="card">
      <div class="card-header">
        <h3>Regular season 1–${data.regular_season_weeks} &middot; playoffs weeks ${data.regular_season_weeks + 1}–${data.end_week}</h3>
        <span class="card-sub">rosters as of week ${data.as_of_week}</span>
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Seed</th><th>Team</th><th>Manager</th><th class="num">Record</th><th class="num">PF</th><th class="num">PA</th></tr></thead>
          <tbody>${rows}</tbody>
        </table>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h3>Projected playoff bracket</h3></div>
      <div class="two-col" style="grid-template-columns:repeat(${Math.max(1, data.playoffs.rounds.length)}, 1fr)">${roundsHTML || emptyState("🏈", "No playoff bracket to show.")}</div>
    </div>
    <div class="card">
      <div class="card-header">
        <h3>Game-by-game</h3>
        <select id="records-game-team">${teamOptions}</select>
      </div>
      <div id="records-game-table"></div>
    </div>`;

  const gamesByTeam = data.games_by_team;
  const renderGames = (teamId) => {
    const games = (gamesByTeam[teamId] || gamesByTeam[String(teamId)] || []).slice().sort((a, b) => a.week - b.week);
    const gRows = games.map((g) => `
      <tr>
        <td class="num mono">${g.week}</td>
        <td>${esc(nameOf[g.opponent] || g.opponent)}</td>
        <td class="num mono">${fmt1(g.points_for)}</td>
        <td class="num mono">${fmt1(g.points_against)}</td>
        <td><span class="pill ${g.result === "W" ? "positive" : g.result === "L" ? "negative" : "neutral"}">${g.result}</span></td>
        <td>${g.actual ? '<span class="badge">Actual</span>' : '<span class="badge">Projected</span>'}</td>
      </tr>`).join("");
    $("records-game-table").innerHTML = games.length
      ? `<div class="table-wrap"><table><thead><tr><th>Week</th><th>Opponent</th><th class="num">PF</th><th class="num">PA</th><th>Result</th><th>Source</th></tr></thead><tbody>${gRows}</tbody></table></div>`
      : emptyState("📭", "No games found for this team in the stored schedule.");
  };
  const gameSel = $("records-game-team");
  gameSel.value = String(state.teamId);
  renderGames(Number(gameSel.value));
  gameSel.addEventListener("change", (e) => renderGames(Number(e.target.value)));
}

/* ==========================================================================
   VIEW: Win Probabilities (+ expected payouts)
   ========================================================================== */

function initOddsView() {
  const body = $("odds-body");
  body.innerHTML = `
    <div class="card">
      <div class="card-header">
        <h3>Uncertainty settings</h3>
        <button class="btn btn-sm" id="odds-calibrate">📐 Estimate from actual results</button>
      </div>
      <div id="odds-calibrate-note" class="card-sub"></div>
      <div class="card-grid" style="margin-top:12px">
        <div class="field-group">
          <label class="field-label">Weekly score uncertainty (± % of projection)</label>
          <input type="range" id="odds-std" min="50" max="400" step="5" value="200" />
          <div class="field-hint" id="odds-std-label"></div>
        </div>
        <div class="field-group">
          <label class="field-label">Simulations</label>
          <select id="odds-nsims">
            ${[500, 1000, 2000, 5000, 10000, 20000, 50000, 100000].map((n) => `<option value="${n}">${n.toLocaleString()}</option>`).join("")}
          </select>
        </div>
        <div class="field-group">
          <label class="field-label">Matchup odds for week</label>
          <input type="number" id="odds-week" min="1" />
        </div>
      </div>
      <div style="margin-top:14px"><button class="btn btn-primary" id="odds-run">Calculate odds</button></div>
    </div>
    <div id="odds-results"></div>`;

  $("odds-std").addEventListener("input", (e) => { $("odds-std-label").textContent = `±${e.target.value / 10}% of projection`; });
  $("odds-calibrate").addEventListener("click", runCalibrate);
  $("odds-run").addEventListener("click", runOdds);
}

function refreshOddsDefaults() {
  $("odds-std").value = Math.round((state.oddsStdFraction || 0.2) * 1000);
  $("odds-std-label").textContent = `±${$("odds-std").value / 10}% of projection`;
  $("odds-week").value = state.startWeek;
  $("odds-week").max = state.leagueEndWeek;
  if (state.oddsNSims && $("odds-nsims").querySelector(`option[value="${state.oddsNSims}"]`)) {
    $("odds-nsims").value = state.oddsNSims;
  }
}

async function runCalibrate() {
  const note = $("odds-calibrate-note");
  note.textContent = "Estimating from played weeks...";
  try {
    const c = await API.post(`/api/leagues/${state.league}/odds/calibrate`);
    $("odds-std").value = Math.round(c.std_fraction * 1000);
    $("odds-std-label").textContent = `±${$("odds-std").value / 10}% of projection`;
    note.textContent = `From ${c.n_games} played team-weeks: ${(c.std_fraction * 100).toFixed(1)}% (bias ${(c.bias * 100).toFixed(1)}%).`;
  } catch (e) {
    note.textContent = `⚠️ ${e.message}`;
  }
}

async function runOdds() {
  const results = $("odds-results");
  const btn = $("odds-run");
  btn.disabled = true;
  results.innerHTML = spinnerRow("Simulating the season many times...");
  try {
    const data = await API.get(`/api/leagues/${state.league}/odds`, {
      week: $("odds-week").value, as_of_week: state.startWeek,
      std_fraction: Number($("odds-std").value) / 1000, n_sims: $("odds-nsims").value,
    });
    renderOdds(data);
  } catch (e) {
    results.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderOdds(data) {
  const results = $("odds-results");
  const matchupsHTML = data.matchups.length
    ? data.matchups.map((m) => {
        const pctA = Math.round(m.win_prob_a * 100);
        return `
        <div class="matchup-row">
          <div class="matchup-side"><span class="matchup-team">${esc(m.team_a_name)}</span><span class="matchup-proj">${fmt1(m.mean_a)} proj</span></div>
          <div style="display:flex;flex-direction:column;align-items:center;gap:4px">
            <div class="matchup-prob-bar"><div class="a" style="width:${pctA}%"></div><div class="b" style="width:${100 - pctA}%"></div></div>
            <div class="matchup-prob-label">${pctA}% – ${100 - pctA}%</div>
          </div>
          <div class="matchup-side right"><span class="matchup-team">${esc(m.team_b_name)}</span><span class="matchup-proj">${fmt1(m.mean_b)} proj</span></div>
        </div>`;
      }).join("")
    : emptyState("📭", "No unplayed matchups found for this week.");

  const seasonRows = data.season.teams.map((r) => `
    <tr class="${r.team_id === state.teamId ? "me" : ""}">
      <td>${esc(r.team_name)}</td><td>${esc(r.manager_name || "—")}</td>
      <td class="num mono">${r.playoff_pct.toFixed(1)}%</td>
      <td class="num mono">${r.champion_pct.toFixed(1)}%</td>
      <td class="num mono">${r.avg_wins.toFixed(1)}-${r.avg_losses.toFixed(1)}</td>
      <td class="num mono">${r.avg_seed}</td>
    </tr>`).join("");

  results.innerHTML = `
    <div class="card">
      <div class="card-header"><h3>Week ${data.week} matchup odds</h3></div>
      ${matchupsHTML}
    </div>
    <div class="card">
      <div class="card-header"><h3>Season-long odds</h3><span class="card-sub">${data.season.n_sims.toLocaleString()} simulations</span></div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>Team</th><th>Manager</th><th class="num">Playoffs</th><th class="num">Champion</th><th class="num">Avg record</th><th class="num">Avg seed</th></tr></thead>
          <tbody>${seasonRows}</tbody>
        </table>
      </div>
      <div class="chart-box" style="margin-top:14px"><canvas id="odds-champ-chart"></canvas></div>
    </div>
    ${payoutsCardHTML(data)}`;

  barChart($("odds-champ-chart"), data.season.teams.map((r) => r.team_name), data.season.teams.map((r) => r.champion_pct), { color: "#e8b84a" });
  if (data.payouts) {
    barChart($("payouts-chart"), data.payouts.teams.map((t) => t.team_name),
             data.payouts.teams.map((t) => t.expected_total), { color: "#46d488" });
  }
}

function payoutsCardHTML(data) {
  if (data.payouts_error) {
    return `<div class="error-banner">Expected payouts unavailable: ${esc(data.payouts_error)}</div>`;
  }
  const p = data.payouts;
  if (!p) return "";
  const s = p.summary;
  const me = p.teams.find((t) => t.team_id === state.teamId);

  const poolWarn = Math.abs(s.total_prizes - s.total_buy_ins) > 0.005
    ? `<div class="info-banner" style="margin-bottom:14px">Prizes total ${money(s.total_prizes)} but buy-ins total ${money(s.total_buy_ins)} (${s.n_teams} teams × ${money(s.buy_in)}) &mdash; double-check your payout config.</div>` : "";

  const hero = me ? `
    <div class="card-grid" style="margin-bottom:16px">
      <div class="stat-card"><span class="label">Already earned</span><span class="value">${money(me.earned)}</span></div>
      <div class="stat-card"><span class="label">Expected rest of season</span><span class="value">${money(me.expected_remaining)}</span></div>
      <div class="stat-card"><span class="label">Expected total</span><span class="value positive">${money(me.expected_total)}</span></div>
      <div class="stat-card"><span class="label">Net after ${money(me.buy_in)} buy-in</span><span class="value ${me.expected_net >= 0 ? "positive" : "negative"}">${(me.expected_net >= 0 ? "+" : "−") + money(Math.abs(me.expected_net))}</span></div>
    </div>` : "";

  const rows = p.teams.map((t) => `
    <tr class="${t.team_id === state.teamId ? "me" : ""}">
      <td>${esc(t.team_name)}</td><td>${esc(t.manager_name || "—")}</td>
      <td class="num mono">${money(t.earned)}</td>
      <td class="num mono">${money(t.expected_placement)}</td>
      <td class="num mono">${money(t.expected_weekly_high)}</td>
      <td class="num mono">${money(t.expected_total)}</td>
      <td class="num">${moneyPill(t.expected_net)}</td>
      <td class="num mono">${t.first_pct.toFixed(1)} / ${t.second_pct.toFixed(1)} / ${t.third_pct.toFixed(1)}%</td>
    </tr>`).join("");

  return `
    <div class="card">
      <div class="card-header">
        <h3>Expected payouts</h3>
        <span class="card-sub">weekly-high weeks settled: ${s.weekly_high_weeks_paid}/${s.weekly_high_weeks_total}</span>
      </div>
      ${poolWarn}${hero}
      <div class="table-wrap"><table>
        <thead><tr><th>Team</th><th>Manager</th><th class="num">Earned</th><th class="num">ROS placement</th><th class="num">ROS weekly high</th><th class="num">Expected total</th><th class="num">Net</th><th class="num">1st / 2nd / 3rd</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <div class="chart-box" style="margin-top:14px"><canvas id="payouts-chart"></canvas></div>
    </div>`;
}

/* ==========================================================================
   VIEW: Waiver Wire
   ========================================================================== */

function initWaiverView() {
  const body = $("waiver-body");
  body.innerHTML = `
    <div class="card">
      <div class="card-header"><h3>Find pickups</h3></div>
      <div class="card-grid">
        <div class="field-group"><label class="field-label">How many to show</label><input type="range" id="w-topn" min="5" max="50" value="15" /><div class="field-hint" id="w-topn-label"></div></div>
        <div class="field-group">
          <div class="checkbox-row"><input type="checkbox" id="w-bypos" /><label for="w-bypos">Search per-position (so a deep position like WR can't flood out QB/TE/K)</label></div>
        </div>
        <div class="field-group" id="w-fa-global"><label class="field-label">Free agents considered</label><input type="range" id="w-fa" min="10" max="100" value="40" /><div class="field-hint" id="w-fa-label"></div></div>
        <div class="field-group" id="w-fa-perpos" style="display:none"><label class="field-label">Free agents per position</label><input type="range" id="w-fa-pp" min="5" max="30" value="10" /><div class="field-hint" id="w-fa-pp-label"></div></div>
      </div>
      <div style="margin-top:14px"><button class="btn btn-primary" id="w-find-btn">Find pickups</button></div>
      <div id="w-progress" style="margin-top:12px"></div>
    </div>
    <div id="w-results"></div>

    <div class="card">
      <div class="card-header"><h3>Plan sequential moves</h3></div>
      <div class="view-desc">Finds the single best add/drop, applies it, then searches again against the resulting roster — repeating until nothing else helps.</div>
      <div style="margin-top:10px"><button class="btn btn-primary" id="w-plan-btn">Plan moves</button></div>
      <div id="w-plan-progress" style="margin-top:12px"></div>
    </div>
    <div id="w-plan-results"></div>`;

  const syncLabels = () => {
    $("w-topn-label").textContent = `${$("w-topn").value} pickups`;
    $("w-fa-label").textContent = `${$("w-fa").value} free agents`;
    $("w-fa-pp-label").textContent = `${$("w-fa-pp").value} per position`;
  };
  ["w-topn", "w-fa", "w-fa-pp"].forEach((id) => $(id).addEventListener("input", syncLabels));
  syncLabels();

  $("w-bypos").addEventListener("change", (e) => {
    $("w-fa-global").style.display = e.target.checked ? "none" : "block";
    $("w-fa-perpos").style.display = e.target.checked ? "block" : "none";
  });

  $("w-find-btn").addEventListener("click", runWaiverPickups);
  $("w-plan-btn").addEventListener("click", runWaiverPlan);
}

async function runWaiverPickups() {
  const btn = $("w-find-btn"), prog = $("w-progress"), results = $("w-results");
  btn.disabled = true;
  results.innerHTML = "";
  const byPos = $("w-bypos").checked;
  try {
    const { job_id } = await API.post(`/api/leagues/${state.league}/waiver/pickups`, {
      team_id: state.teamId, start_week: state.startWeek, end_week: state.endWeek,
      top_n: Number($("w-topn").value), by_position: byPos,
      fa_prefilter: Number($("w-fa").value), fa_per_position: Number($("w-fa-pp").value),
      decay: decayOrNull(),
    });
    const result = await pollJob(job_id, (j) => { prog.innerHTML = progressHTML(j, "Checking free agents..."); });
    prog.innerHTML = "";
    state.lastRunScope.waiver = scopeKey();
    renderWaiverPickups(result.picks);
  } catch (e) {
    prog.innerHTML = "";
    results.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderWaiverPickups(picks) {
  const results = $("w-results");
  if (!picks.length) { results.innerHTML = emptyState("🔍", "No pickups found for this search."); return; }
  results.innerHTML = `${staleBanner("waiver")}<div class="list-stack">${picks.map((p, i) => waiverPickCardHTML(p, i)).join("")}</div>`;
  picks.forEach((p, i) => {
    if (!p.add || !p.drop) return;
    $(`w-why-${i}`).addEventListener("click", () => toggleWaiverExplain(i, p));
  });
}

function waiverPickCardHTML(p, i) {
  const add = p.add ? p.add.name : "?";
  const drop = p.drop ? p.drop.name : "?";
  return `
    <div class="pick-card">
      <div class="pick-row">
        <div class="pick-players">
          <span class="add-name">+ ${esc(add)}</span>
          <span class="arrow">for</span>
          <span class="drop-name">${esc(drop)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:10px">
          ${pillHTML(p.projected_gain, { suffix: " pts" })}
          ${p.add && p.drop ? `<button class="btn btn-ghost btn-sm explain-toggle" id="w-why-${i}">Why? ▾</button>` : ""}
        </div>
      </div>
      <div class="explain-panel" id="w-explain-${i}"></div>
    </div>`;
}

async function toggleWaiverExplain(i, p) {
  const panel = $(`w-explain-${i}`);
  const isOpen = panel.classList.contains("open");
  if (isOpen) { panel.classList.remove("open"); return; }
  panel.classList.add("open");
  if (panel.dataset.loaded) return;
  panel.innerHTML = spinnerRow("Loading week-by-week detail...");
  try {
    const ex = await API.get(`/api/leagues/${state.league}/waiver/explain`, {
      team_id: state.teamId, add: p.add.player_id, drop: p.drop.player_id,
      start_week: state.startWeek, end_week: state.endWeek, decay: decayOrNull(),
    });
    const weeks = Object.keys(ex.weekly_before).map(Number).sort((a, b) => a - b);
    panel.innerHTML = `<div class="chart-box"><canvas id="w-explain-chart-${i}"></canvas></div>`;
    panel.dataset.loaded = "1";
    lineChart($(`w-explain-chart-${i}`), weeks.map((w) => `Wk ${w}`), [
      { label: "Before", data: weeks.map((w) => ex.weekly_before[w]), color: "#8b93a1" },
      { label: "After", data: weeks.map((w) => ex.weekly_after[w]), color: "#46d488" },
    ]);
  } catch (e) {
    panel.innerHTML = errorBanner(e.message);
  }
}

async function runWaiverPlan() {
  const btn = $("w-plan-btn"), prog = $("w-plan-progress"), results = $("w-plan-results");
  btn.disabled = true;
  results.innerHTML = "";
  const byPos = $("w-bypos").checked;
  try {
    const { job_id } = await API.post(`/api/leagues/${state.league}/waiver/plan`, {
      team_id: state.teamId, start_week: state.startWeek, end_week: state.endWeek,
      by_position: byPos, fa_prefilter: Number($("w-fa").value), fa_per_position: Number($("w-fa-pp").value),
      decay: decayOrNull(),
    });
    const result = await pollJob(job_id, (j) => { prog.innerHTML = progressHTML(j, "Chaining moves..."); });
    prog.innerHTML = "";
    renderWaiverPlan(result);
  } catch (e) {
    prog.innerHTML = "";
    results.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderWaiverPlan(plan) {
  const results = $("w-plan-results");
  if (!plan.moves.length) {
    results.innerHTML = `<div class="card"><div class="stat-card"><span class="label">Starting total</span><span class="value">${fmt1(plan.starting_total)}</span></div><p class="view-desc" style="margin-top:8px">No move found that improves your projected total.</p></div>`;
    return;
  }
  const stepsHTML = plan.moves.map((m) => `
    <div class="move-card">
      <div class="pick-row">
        <div style="display:flex;align-items:center;gap:10px">
          <div class="step-badge">${m.step}</div>
          <div class="pick-players">
            <span class="add-name">+ ${esc(m.add.name)}</span><span class="arrow">for</span><span class="drop-name">${esc(m.drop.name)}</span>
          </div>
        </div>
        ${pillHTML(m.gain, { suffix: " pts" })}
      </div>
    </div>`).join("");

  results.innerHTML = `
    <div class="card">
      <div class="card-grid">
        <div class="stat-card"><span class="label">Starting total</span><span class="value">${fmt1(plan.starting_total)}</span></div>
        <div class="stat-card"><span class="label">Final total</span><span class="value positive">${fmt1(plan.final_total)}</span></div>
        <div class="stat-card"><span class="label">Total gain</span><span class="value positive">${signed1(plan.total_gain)}</span></div>
      </div>
    </div>
    <div class="list-stack">${stepsHTML}</div>
    <div class="card">
      <div class="card-header"><h3>Running total</h3></div>
      <div class="chart-box"><canvas id="w-plan-chart"></canvas></div>
    </div>`;

  const labels = ["Start", ...plan.moves.map((m) => `Step ${m.step}`)];
  const values = [plan.starting_total, ...plan.moves.map((m) => m.running_total)];
  lineChart($("w-plan-chart"), labels, [{ label: "Running total", data: values, color: "#46d488", fill: true }]);
}

/* ==========================================================================
   VIEW: Trade Finder
   ========================================================================== */

function initTradesView() {
  const body = $("trades-body");
  body.innerHTML = `
    <div class="card">
      <div class="card-grid">
        <div class="field-group"><label class="field-label">Trade partner</label><select id="t-partner"></select></div>
        <div class="field-group"><label class="field-label">Search depth (prefilter)</label><input type="range" id="t-prefilter" min="4" max="25" value="12" /><div class="field-hint" id="t-prefilter-label"></div></div>
        <div class="field-group"><label class="field-label">Show top N</label><input type="range" id="t-topn" min="5" max="100" value="10" /><div class="field-hint" id="t-topn-label"></div></div>
        <div class="field-group">
          <label class="field-label">Trade size</label>
          <div style="display:flex;gap:14px;flex-wrap:wrap;padding-top:2px">
            <label class="checkbox-row"><input type="checkbox" id="t-size-1" checked /> 1-for-1</label>
            <label class="checkbox-row"><input type="checkbox" id="t-size-2" /> 2-for-2</label>
            <label class="checkbox-row"><input type="checkbox" id="t-size-3" /> 3-for-3</label>
          </div>
          <div class="field-hint">2-for-2 / 3-for-3 search far more combinations — much slower, especially at a high prefilter.</div>
        </div>
      </div>
      <div style="margin-top:14px"><button class="btn btn-primary" id="t-find-btn">Find trades</button></div>
      <div id="t-progress" style="margin-top:12px"></div>
    </div>
    <div id="t-results"></div>`;

  const sync = () => {
    $("t-prefilter-label").textContent = `${$("t-prefilter").value} candidates/side`;
    $("t-topn-label").textContent = `top ${$("t-topn").value}`;
    if (allTradeProposals.length) renderTradeResultsList(allTradeProposals);
  };
  ["t-prefilter", "t-topn"].forEach((id) => $(id).addEventListener("input", sync));
  sync();
  $("t-find-btn").addEventListener("click", runTradeFinder);
}

function refreshTradePartnerOptions() {
  const sel = $("t-partner");
  const others = state.teams.filter((t) => t.team_id !== state.teamId);
  sel.innerHTML = `<option value="">Any team</option>` + others.map((t) => `<option value="${t.team_id}">${esc(t.team_name)}</option>`).join("");
}

let allTradeProposals = [];
let tradeExcludeGivePositions = new Set();
let tradeExcludeGetPositions = new Set();
let tradeExcludePlayers = new Set();

async function runTradeFinder() {
  const btn = $("t-find-btn"), prog = $("t-progress"), results = $("t-results");
  const sizes = [1, 2, 3].filter((n) => $(`t-size-${n}`).checked);
  if (!sizes.length) { results.innerHTML = errorBanner("Pick at least one trade size (1-for-1, 2-for-2, or 3-for-3)."); return; }
  btn.disabled = true;
  results.innerHTML = "";
  const partner = $("t-partner").value;
  try {
    const { job_id } = await API.post(`/api/leagues/${state.league}/trades/suggest`, {
      team_id: state.teamId, start_week: state.startWeek, end_week: state.endWeek,
      candidate_prefilter: Number($("t-prefilter").value),
      partner_team_id: partner ? Number(partner) : null, combo_sizes: sizes, decay: decayOrNull(),
    });
    const result = await pollJob(job_id, (j) => { prog.innerHTML = progressHTML(j, "Searching trade combinations..."); });
    prog.innerHTML = "";
    state.lastRunScope.trades = scopeKey(partner || "any");
    allTradeProposals = result.proposals;
    tradeExcludeGivePositions = new Set();
    tradeExcludeGetPositions = new Set();
    tradeExcludePlayers = new Set();
    renderTradeFinder(allTradeProposals);
  } catch (e) {
    prog.innerHTML = "";
    results.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderTradeFinder(all) {
  const results = $("t-results");
  if (!all.length) { results.innerHTML = emptyState("🤷", "No win-win trades found with the current search depth."); return; }

  const givePositions = new Set(), getPositions = new Set();
  const playersById = new Map();
  all.forEach((p) => {
    p.give.forEach((x) => { if (x.position) givePositions.add(x.position); playersById.set(x.player_id, x.name); });
    p.get.forEach((x) => { if (x.position) getPositions.add(x.position); playersById.set(x.player_id, x.name); });
  });
  const giveArr = Array.from(givePositions).sort();
  const getArr = Array.from(getPositions).sort();
  const playerArr = Array.from(playersById.entries()).sort((a, b) => a[1].localeCompare(b[1]));

  const posBoxHTML = (id, arr) => arr.length
    ? arr.map((pos) => `<label class="player-select-row"><input type="checkbox" data-pos="${esc(pos)}" /><span>${esc(pos)}</span></label>`).join("")
    : `<div class="player-select-row"><span>—</span></div>`;

  results.innerHTML = `
    ${staleBanner("trades", $("t-partner").value || "any")}
    <div class="card">
      <div class="card-header"><h3>Filter results</h3><button class="btn btn-ghost btn-sm" id="t-filter-clear">Clear filters</button></div>
      <div class="card-grid">
        <div class="field-group">
          <label class="field-label">Exclude positions you'd give</label>
          <div class="player-select-box" id="t-filter-give-positions">${posBoxHTML("give", giveArr)}</div>
        </div>
        <div class="field-group">
          <label class="field-label">Exclude positions you'd get</label>
          <div class="player-select-box" id="t-filter-get-positions">${posBoxHTML("get", getArr)}</div>
        </div>
        <div class="field-group">
          <label class="field-label">Exclude players (either side)</label>
          <input type="text" id="t-filter-player-search" placeholder="Search players..." />
          <div class="player-select-box" id="t-filter-players">
            ${playerArr.map(([id, name]) => `<label class="player-select-row"><input type="checkbox" data-pid="${esc(id)}" /><span>${esc(name)}</span></label>`).join("")}
          </div>
        </div>
      </div>
    </div>
    <div id="t-results-list"></div>`;

  wireTradeFilterEvents();
  renderTradeResultsList(all);
}

function applyTradeFilters(all) {
  if (!tradeExcludeGivePositions.size && !tradeExcludeGetPositions.size && !tradeExcludePlayers.size) return all;
  return all.filter((p) => {
    if (p.give.some((x) => x.position && tradeExcludeGivePositions.has(x.position))) return false;
    if (p.get.some((x) => x.position && tradeExcludeGetPositions.has(x.position))) return false;
    if ([...p.give, ...p.get].some((x) => tradeExcludePlayers.has(x.player_id))) return false;
    return true;
  });
}

function wireTradeFilterEvents() {
  document.querySelectorAll("#t-filter-give-positions input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) tradeExcludeGivePositions.add(cb.dataset.pos); else tradeExcludeGivePositions.delete(cb.dataset.pos);
      renderTradeResultsList(allTradeProposals);
    });
  });
  document.querySelectorAll("#t-filter-get-positions input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) tradeExcludeGetPositions.add(cb.dataset.pos); else tradeExcludeGetPositions.delete(cb.dataset.pos);
      renderTradeResultsList(allTradeProposals);
    });
  });
  document.querySelectorAll("#t-filter-players input[type=checkbox]").forEach((cb) => {
    cb.addEventListener("change", () => {
      if (cb.checked) tradeExcludePlayers.add(cb.dataset.pid); else tradeExcludePlayers.delete(cb.dataset.pid);
      renderTradeResultsList(allTradeProposals);
    });
  });
  const search = $("t-filter-player-search");
  if (search) search.addEventListener("input", (e) => {
    const q = e.target.value.toLowerCase();
    document.querySelectorAll("#t-filter-players .player-select-row").forEach((row) => {
      const name = row.querySelector("span:last-child").textContent.toLowerCase();
      row.style.display = name.includes(q) ? "" : "none";
    });
  });
  const clearBtn = $("t-filter-clear");
  if (clearBtn) clearBtn.addEventListener("click", () => {
    tradeExcludeGivePositions = new Set();
    tradeExcludeGetPositions = new Set();
    tradeExcludePlayers = new Set();
    document.querySelectorAll("#t-filter-give-positions input, #t-filter-get-positions input, #t-filter-players input")
      .forEach((cb) => { cb.checked = false; });
    renderTradeResultsList(allTradeProposals);
  });
}

function renderTradeResultsList(all) {
  const container = $("t-results-list");
  if (!container) return;
  const filtered = applyTradeFilters(all);
  const topN = Number($("t-topn").value);
  const shown = filtered.slice(0, topN);
  const anyFilter = tradeExcludeGivePositions.size || tradeExcludeGetPositions.size || tradeExcludePlayers.size;
  const filterNote = anyFilter ? ` &middot; ${all.length - filtered.length} hidden by filters` : "";

  if (!filtered.length) {
    container.innerHTML = `<div class="card">${emptyState("🚫", "No trades left after filtering — try clearing a filter.")}</div>`;
    return;
  }

  const cards = shown.map((p) => `
    <div class="proposal-card">
      <div class="pick-row">
        <div class="give-get">
          <span class="give-list">Give: ${p.give.map((x) => esc(x.name)).join(", ")}</span>
          <span class="arrow">→</span>
          <span class="get-list">Get: ${p.get.map((x) => esc(x.name)).join(", ")}</span>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <span class="pill positive">you ${signed1(p.my_delta)}</span>
          <span class="pill positive">${esc(p.partner_team_name)} ${signed1(p.partner_delta)}</span>
        </div>
      </div>
    </div>`).join("");

  const giveCounts = {}, getCounts = {};
  filtered.forEach((p) => {
    p.give.forEach((x) => { giveCounts[x.name] = (giveCounts[x.name] || 0) + 1; });
    p.get.forEach((x) => { getCounts[x.name] = (getCounts[x.name] || 0) + 1; });
  });
  const topEntries = (obj, n) => Object.entries(obj).sort((a, b) => b[1] - a[1]).slice(0, n);
  const giveTop = topEntries(giveCounts, 10), getTop = topEntries(getCounts, 10);

  container.innerHTML = `
    <div class="card"><div class="card-header"><h3>Showing ${shown.length} of ${filtered.length} win-win trades${filterNote}</h3></div>
      <div class="list-stack">${cards}</div>
    </div>
    <div class="two-col">
      <div class="card"><div class="card-header"><h3>Most frequently traded away</h3></div><div class="chart-box"><canvas id="t-give-chart"></canvas></div></div>
      <div class="card"><div class="card-header"><h3>Most frequently received</h3></div><div class="chart-box"><canvas id="t-get-chart"></canvas></div></div>
    </div>`;

  barChart($("t-give-chart"), giveTop.map((e) => e[0]), giveTop.map((e) => e[1]), { horizontal: true, color: "#f1585d" });
  barChart($("t-get-chart"), getTop.map((e) => e[0]), getTop.map((e) => e[1]), { horizontal: true, color: "#46d488" });
}

/* ==========================================================================
   VIEW: Evaluate Trade
   ========================================================================== */

function initEvaluateView() {
  $("evaluate-body").innerHTML = `
    <div class="card">
      <div class="field-group"><label class="field-label">Trade with</label><select id="e-partner"></select></div>
      <div class="two-col" style="margin-top:14px">
        <div class="field-group"><label class="field-label" id="e-mine-label">You give</label><div class="player-select-box" id="e-mine-box"></div></div>
        <div class="field-group"><label class="field-label" id="e-theirs-label">You get</label><div class="player-select-box" id="e-theirs-box"></div></div>
      </div>
      <div style="margin-top:14px"><button class="btn btn-primary" id="e-run-btn">Evaluate trade</button></div>
    </div>
    <div id="e-results"></div>`;
  $("e-partner").addEventListener("change", loadEvaluateRosters);
  $("e-run-btn").addEventListener("click", runEvaluate);
}

async function loadEvaluateTeamOptions() {
  const sel = $("e-partner");
  const others = state.teams.filter((t) => t.team_id !== state.teamId);
  if (!others.length) { $("evaluate-body").innerHTML = emptyState("🤷", "No other teams in this league."); return; }
  sel.innerHTML = others.map((t) => `<option value="${t.team_id}">${esc(t.team_name)}</option>`).join("");
  await loadEvaluateRosters();
}

async function loadEvaluateRosters() {
  const partnerId = Number($("e-partner").value);
  if (!partnerId) return;
  $("e-mine-label").textContent = `You give (${teamLabel(state.teamId)})`;
  $("e-theirs-label").textContent = `You get (${teamLabel(partnerId)})`;
  $("e-mine-box").innerHTML = spinnerRow("Loading...");
  $("e-theirs-box").innerHTML = spinnerRow("Loading...");
  const [mine, theirs] = await Promise.all([
    API.get(`/api/leagues/${state.league}/roster`, { team_id: state.teamId, week: state.startWeek }),
    API.get(`/api/leagues/${state.league}/roster`, { team_id: partnerId, week: state.startWeek }),
  ]);
  $("e-mine-box").innerHTML = playerCheckboxList(mine.players.filter((p) => !p.reserved), "e-mine");
  $("e-theirs-box").innerHTML = playerCheckboxList(theirs.players.filter((p) => !p.reserved), "e-theirs");
}

function playerCheckboxList(players, prefix) {
  if (!players.length) return emptyState("📭", "No tradeable players found.");
  return players.map((p) => `
    <label class="player-select-row">
      <input type="checkbox" name="${prefix}" value="${esc(p.player_id)}" />
      <span>${esc(p.name)}</span><span class="pos">${esc(p.position || "")}</span>
    </label>`).join("");
}

function checkedValues(prefix) {
  return Array.from(document.querySelectorAll(`input[name="${prefix}"]:checked`)).map((el) => el.value);
}

async function runEvaluate() {
  const btn = $("e-run-btn"), results = $("e-results");
  const give = checkedValues("e-mine"), get = checkedValues("e-theirs");
  if (!give.length || !get.length) { results.innerHTML = errorBanner("Pick at least one player on each side."); return; }
  const partnerId = Number($("e-partner").value);
  btn.disabled = true;
  results.innerHTML = spinnerRow("Simulating both rosters with and without the trade...");
  try {
    const data = await API.post(`/api/leagues/${state.league}/trades/explain`, {
      team_a: state.teamId, give, team_b: partnerId, get,
      start_week: state.startWeek, end_week: state.endWeek, decay: decayOrNull(),
    });
    renderEvaluate(data, partnerId);
  } catch (e) {
    results.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

function renderEvaluate(data, partnerId) {
  const results = $("e-results");
  const a = data.team_a, b = data.team_b;
  results.innerHTML = `
    <div class="card">
      <div class="card-grid">
        <div class="stat-card">
          <span class="label">${esc(teamLabel(state.teamId))}</span>
          <span class="value ${a.raw_delta >= 0 ? "positive" : "negative"}">${fmt1(a.raw_total_after)}</span>
          ${pillHTML(a.raw_delta, { suffix: " pts" })}
        </div>
        <div class="stat-card">
          <span class="label">${esc(teamLabel(partnerId))}</span>
          <span class="value ${b.raw_delta >= 0 ? "positive" : "negative"}">${fmt1(b.raw_total_after)}</span>
          ${pillHTML(b.raw_delta, { suffix: " pts" })}
        </div>
      </div>
    </div>
    <div class="two-col">
      <div class="card"><div class="card-header"><h3>${esc(teamLabel(state.teamId))}</h3></div><div class="chart-box"><canvas id="e-chart-a"></canvas></div></div>
      <div class="card"><div class="card-header"><h3>${esc(teamLabel(partnerId))}</h3></div><div class="chart-box"><canvas id="e-chart-b"></canvas></div></div>
    </div>`;

  const weeksA = Object.keys(a.weekly_before).map(Number).sort((x, y) => x - y);
  const weeksB = Object.keys(b.weekly_before).map(Number).sort((x, y) => x - y);
  lineChart($("e-chart-a"), weeksA.map((w) => `Wk ${w}`), [
    { label: "Before", data: weeksA.map((w) => a.weekly_before[w]), color: "#8b93a1" },
    { label: "After", data: weeksA.map((w) => a.weekly_after[w]), color: "#46d488" },
  ]);
  lineChart($("e-chart-b"), weeksB.map((w) => `Wk ${w}`), [
    { label: "Before", data: weeksB.map((w) => b.weekly_before[w]), color: "#8b93a1" },
    { label: "After", data: weeksB.map((w) => b.weekly_after[w]), color: "#5b9df5" },
  ]);
}

/* ==========================================================================
   VIEW: Rosters
   ========================================================================== */

function initRostersView() {
  $("rosters-body").innerHTML = `
    <div class="card">
      <div class="controls-row">
        <div class="field-inline"><label>Team</label><select id="r-team"></select></div>
        <div class="field-inline"><label>As of week</label><input type="number" id="r-week" min="1" /></div>
      </div>
      <div id="r-table" style="margin-top:14px"></div>
    </div>
    <div class="card">
      <div class="card-header"><h3>Move a player</h3></div>
      <div class="view-desc">Reassigns a player straight in the local database — a scratch what-if. Overwritten next time you refresh data.</div>
      <div class="card-grid" style="margin-top:12px">
        <div class="field-group"><label class="field-label">Effective from week</label><input type="number" id="m-week" min="1" /></div>
        <div class="field-group"><label class="field-label">From</label><select id="m-from"></select></div>
        <div class="field-group"><label class="field-label">To</label><select id="m-to"></select></div>
      </div>
      <div class="field-group" style="margin-top:10px"><label class="field-label">Player</label><select id="m-player"></select></div>
      <div style="margin-top:14px"><button class="btn btn-primary" id="m-move-btn">Move player</button></div>
      <div id="m-result" style="margin-top:10px"></div>
    </div>`;

  $("r-team").addEventListener("change", runRosterView);
  $("r-week").addEventListener("change", runRosterView);
  $("m-from").addEventListener("change", loadMovePlayerOptions);
  $("m-week").addEventListener("change", loadMovePlayerOptions);
  $("m-move-btn").addEventListener("click", runMovePlayer);
}

function teamOptionsHTML(includeFA) {
  let html = state.teams.map((t) => `<option value="${t.team_id}">${esc(t.team_name)}</option>`).join("");
  if (includeFA) html = `<option value="FA">🆓 Free Agency</option>` + html;
  return html;
}

async function loadRosterTeamOptions() {
  $("r-team").innerHTML = teamOptionsHTML(false);
  $("r-team").value = String(state.teamId);
  $("r-week").value = state.startWeek;
  $("r-week").max = state.leagueEndWeek;
  $("m-week").value = state.startWeek;
  $("m-week").max = state.leagueEndWeek;
  $("m-from").innerHTML = teamOptionsHTML(true);
  $("m-to").innerHTML = teamOptionsHTML(true);
  await runRosterView();
  await loadMovePlayerOptions();
}

async function runRosterView() {
  const teamId = Number($("r-team").value);
  const week = Number($("r-week").value) || state.startWeek;
  const table = $("r-table");
  table.innerHTML = spinnerRow("Loading roster...");
  try {
    const data = await API.get(`/api/leagues/${state.league}/roster`, {
      team_id: teamId, week, end_week: state.endWeek, decay: decayOrNull(),
    });
    renderRosterTable(data);
  } catch (e) {
    table.innerHTML = errorBanner(e.message);
  }
}

function renderRosterTable(data) {
  const table = $("r-table");
  if (!data.players.length) { table.innerHTML = emptyState("📭", "No players found for this team/week."); return; }
  const weighted = decayOrNull() !== null;
  const rows = data.players.map((p) => `
    <tr>
      <td>${esc(p.name)} ${p.reserved ? '<span class="badge">IR/Taxi</span>' : ""}</td>
      <td class="mono">${esc(p.position || "—")}</td>
      <td class="num mono">${p.projected === null || p.projected === undefined ? "—" : fmt1(p.projected)}</td>
      <td class="num mono">${fmt1(p.rest_of_season_raw)}</td>
      ${weighted ? `<td class="num mono">${fmt1(p.rest_of_season_weighted)}</td>` : ""}
    </tr>`).join("");
  table.innerHTML = `
    <div class="table-wrap">
      <table>
        <thead><tr><th>Name</th><th>Pos</th><th class="num">Wk ${data.week}</th><th class="num">Rest of season</th>${weighted ? '<th class="num">Weighted</th>' : ""}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

async function loadMovePlayerOptions() {
  const fromVal = $("m-from").value;
  const week = Number($("m-week").value) || state.startWeek;
  const teamId = fromVal === "FA" ? null : Number(fromVal);
  const data = await API.get(`/api/leagues/${state.league}/roster`, { team_id: teamId, week });
  const sel = $("m-player");
  if (!data.players.length) { sel.innerHTML = `<option value="">(no players found)</option>`; return; }
  sel.innerHTML = data.players.map((p) => `<option value="${esc(p.player_id)}">${esc(p.name)} (${esc(p.position || "?")})</option>`).join("");
}

async function runMovePlayer() {
  const btn = $("m-move-btn"), resultEl = $("m-result");
  const fromVal = $("m-from").value, toVal = $("m-to").value;
  if (fromVal === toVal) { resultEl.innerHTML = errorBanner("From and To can't be the same."); return; }
  btn.disabled = true;
  resultEl.innerHTML = "";
  try {
    await API.post(`/api/leagues/${state.league}/roster/move`, {
      player_id: $("m-player").value,
      to_team_id: toVal === "FA" ? null : Number(toVal),
      from_week: Number($("m-week").value) || state.startWeek,
    });
    resultEl.innerHTML = `<div class="success-banner">Moved. Effective week ${$("m-week").value}.</div>`;
    await runRosterView();
    await loadMovePlayerOptions();
  } catch (e) {
    resultEl.innerHTML = errorBanner(e.message);
  } finally {
    btn.disabled = false;
  }
}

/* ==========================================================================
   Boot
   ========================================================================== */

async function boot() {
  try {
    if (typeof Chart !== "undefined") {
      Chart.defaults.font.family = "Inter";
      Chart.defaults.color = "#8b93a1";
    } else {
      console.warn("Chart.js did not load - charts will be skipped, everything else still works.");
    }
    buildNav();
    initStandingsView();
    initRecordsView();
    initOddsView();
    initWaiverView();
    initTradesView();
    initEvaluateView();
    initRostersView();
    switchView("standings");
    await initSidebar();
    refreshOddsDefaults();
    refreshTradePartnerOptions();
  } catch (e) {
    console.error("Startup failed:", e);
    document.getElementById("app").innerHTML =
      `<div style="padding:40px;font-family:sans-serif;color:#f1585d;max-width:700px">
        <h2 style="color:#e9edf1">The app failed to start</h2>
        <p style="color:#8b93a1">Open your browser's developer console (F12) for details. Error:</p>
        <pre style="background:#12161b;padding:14px;border-radius:8px;color:#f1585d;white-space:pre-wrap">${(e && e.stack) || e}</pre>
      </div>`;
  }
}

document.addEventListener("DOMContentLoaded", boot);