import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

const tabs = [
  { key: "view", label: "Database" },
  { key: "events", label: "Events" },
  { key: "sim", label: "Swiss Group Stage" },
  { key: "playoff", label: "Playoff Bracket" },
  { key: "admin", label: "Data Management" },
];

const parseJsonSafe = async (res) => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

const requestJson = async (path, init) => {
  const res = await fetch(`http://127.0.0.1:8000${path}`, init);
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(String(detail));
  }
  return data;
};

const api = window.api || {
  get: (path) => requestJson(path),
  post: (path, body) =>
    requestJson(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  delete: (path) =>
    requestJson(path, {
      method: "DELETE",
    }),
  openExternal: async (url) => {
    if (typeof window !== "undefined" && typeof window.open === "function") {
      window.open(url, "_blank", "noopener,noreferrer");
    }
    return { status: "ok" };
  },
};

const TOP_RATING_TIERS = [5, 10, 20, 30, 50];

const formatTopxImportedAt = (unixSeconds) => {
  const ts = Number(unixSeconds);
  if (!Number.isFinite(ts) || ts <= 0) return "Not imported yet";
  return new Date(ts * 1000).toLocaleString();
};

const TabButton = ({ active, onClick, children }) => (
  <button className={active ? "tab active" : "tab"} onClick={onClick}>
    {children}
  </button>
);

const Section = ({ title, children }) => (
  <section className="card">
    <header>
      <h2>{title}</h2>
    </header>
    <div>{children}</div>
  </section>
);

const Input = ({ label, value, onChange, type = "text", placeholder = "" }) => (
  <label className="field">
    <span>{label}</span>
    <input value={value} onChange={(e) => onChange(e.target.value)} type={type} placeholder={placeholder} />
  </label>
);

const Select = ({ label, value, onChange, options }) => (
  <label className="field">
    <span>{label}</span>
    <select value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  </label>
);

const Badge = ({ children }) => <span className="badge">{children}</span>;
const ROLE_NAME_MAP = {
  0: "Main AWP",
  1: "Support",
  2: "Attacker",
  3: "Leader",
  4: "Stathunter",
  5: "Entry Fragger",
  6: "Camper",
  7: "Defender",
  8: "HS Machine",
  9: "Noob",
  10: "Multi Fragger",
  11: "Eco Friendly",
};

const roleLabel = (roleName) => {
  const n = Number(roleName);
  if (Number.isFinite(n) && ROLE_NAME_MAP[n]) return ROLE_NAME_MAP[n];
  return roleName || "-";
};

const buildNiceStepAxis = (values, step = 0.05) => {
  const finite = (values || []).filter((value) => Number.isFinite(value));
  if (finite.length === 0) {
    return { domain: ["auto", "auto"], ticks: undefined };
  }

  let min = Math.min(...finite);
  let max = Math.max(...finite);
  const stepValue = Number(step) || 0.05;

  min = Math.floor(min / stepValue) * stepValue - stepValue;
  max = Math.ceil(max / stepValue) * stepValue + stepValue;
  if (min === max) {
    min -= stepValue;
    max += stepValue;
  }

  min = Number(min.toFixed(4));
  max = Number(max.toFixed(4));

  const ticks = [];
  for (let value = min; value <= max + stepValue / 2; value += stepValue) {
    ticks.push(Number(value.toFixed(2)));
  }

  return {
    domain: [min, max],
    ticks,
  };
};

const buildPlayerValueRows = (teamCombos) => {
  const byId = new Map();
  (teamCombos || []).forEach((team) => {
    (team?.players || []).forEach((p) => {
      const pid = Number(p?.player_id);
      if (!Number.isFinite(pid) || pid <= 0) return;
      const price = Number(p?.price);
      const points = Number(p?.total_ev);
      const cur = byId.get(pid) || {
        player_id: pid,
        name: p?.name || `Player ${pid}`,
        price: Number.isFinite(price) ? price : 0,
        points_sum: 0,
        n: 0,
      };
      if (Number.isFinite(price) && price > 0) cur.price = price;
      if (Number.isFinite(points)) {
        cur.points_sum += points;
        cur.n += 1;
      }
      byId.set(pid, cur);
    });
  });

  const rows = Array.from(byId.values())
    .map((r) => {
      const avgPoints = r.n > 0 ? r.points_sum / r.n : 0;
      return {
        player_id: r.player_id,
        name: r.name,
        price: r.price,
        points: avgPoints,
      };
    })
    .filter((r) => Number.isFinite(r.price) && Number.isFinite(r.points))
    .sort((a, b) => b.points - a.points);

  if (rows.length === 0) return { rows: [], slope: 0, intercept: 0 };

  const xMean = rows.reduce((s, r) => s + r.price, 0) / rows.length;
  const yMean = rows.reduce((s, r) => s + r.points, 0) / rows.length;
  const num = rows.reduce((s, r) => s + (r.price - xMean) * (r.points - yMean), 0);
  const den = rows.reduce((s, r) => s + (r.price - xMean) ** 2, 0);
  const slope = den > 0 ? num / den : 0;
  const intercept = yMean - slope * xMean;

  const withDistance = rows.map((r) => {
    const onLine = intercept + slope * r.price;
    return {
      ...r,
      on_line: onLine,
      distance: r.points - onLine,
    };
  });

  return { rows: withDistance, slope, intercept };
};

const buildPlayerValueRowsFromSimulation = (simResults, players) => {
  const playerById = new Map();
  (players || []).forEach((p) => {
    const pid = Number(p?.player_id);
    if (!Number.isFinite(pid) || pid <= 0) return;
    playerById.set(pid, p);
  });

  const raw = simResults && typeof simResults === "object" ? simResults : {};
  const teamMap = raw.teams && typeof raw.teams === "object" ? raw.teams : raw;
  const byId = new Map();

  Object.entries(teamMap || {}).forEach(([tid, teamData]) => {
    const playersData = teamData?.players || {};
    Object.entries(playersData).forEach(([pidRaw, comps]) => {
      const pid = Number(pidRaw);
      if (!Number.isFinite(pid) || pid <= 0) return;
      const player = playerById.get(pid) || {};
      const pointsCandidates = [
        Number(comps?.total_points),
        Number(comps?.total),
        Number(comps?.expected_total_points),
      ];
      const points = pointsCandidates.find((v) => Number.isFinite(v));
      if (!Number.isFinite(points)) return;

      const price = Number(player?.price);
      byId.set(pid, {
        player_id: pid,
        name: player?.name || `Player ${pid}`,
        team_id: Number(player?.team_id || tid || 0),
        price: Number.isFinite(price) ? price : 0,
        points,
      });
    });
  });

  const rows = Array.from(byId.values())
    .filter((r) => Number.isFinite(r.price) && Number.isFinite(r.points))
    .sort((a, b) => b.points - a.points);

  if (rows.length === 0) return { rows: [], slope: 0, intercept: 0 };

  const xMean = rows.reduce((s, r) => s + r.price, 0) / rows.length;
  const yMean = rows.reduce((s, r) => s + r.points, 0) / rows.length;
  const num = rows.reduce((s, r) => s + (r.price - xMean) * (r.points - yMean), 0);
  const den = rows.reduce((s, r) => s + (r.price - xMean) ** 2, 0);
  const slope = den > 0 ? num / den : 0;
  const intercept = yMean - slope * xMean;

  const withDistance = rows.map((r) => {
    const onLine = intercept + slope * r.price;
    return {
      ...r,
      on_line: onLine,
      distance: r.points - onLine,
    };
  });

  return { rows: withDistance, slope, intercept };
};

function PriceVsPointsPanel({ title, rows, slope, intercept }) {
  const [search, setSearch] = useState("");
  const [sortBy, setSortBy] = useState("distance_desc");
  const minPrice = useMemo(() => Math.min(...rows.map((r) => Number(r.price))), [rows]);
  const maxPrice = useMemo(() => Math.max(...rows.map((r) => Number(r.price))), [rows]);
  const chartRows = useMemo(
    () =>
      [...rows]
        .sort((a, b) => a.price - b.price)
        .map((r) => ({
          ...r,
          trend: r.on_line,
        })),
    [rows]
  );
  const filteredRows = useMemo(() => {
    const q = search.trim().toLowerCase();
    let out = [...rows];
    if (q) {
      out = out.filter(
        (r) =>
          String(r.name || "").toLowerCase().includes(q) ||
          String(r.player_id || "").includes(q) ||
          String(r.team_id || "").includes(q)
      );
    }
    switch (sortBy) {
      case "points_desc":
        out.sort((a, b) => b.points - a.points);
        break;
      case "price_asc":
        out.sort((a, b) => a.price - b.price);
        break;
      case "price_desc":
        out.sort((a, b) => b.price - a.price);
        break;
      case "distance_abs_desc":
        out.sort((a, b) => Math.abs(b.distance) - Math.abs(a.distance));
        break;
      case "distance_asc":
        out.sort((a, b) => a.distance - b.distance);
        break;
      case "name_asc":
        out.sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
        break;
      case "distance_desc":
      default:
        out.sort((a, b) => b.distance - a.distance);
        break;
    }
    return out;
  }, [rows, search, sortBy]);

  const PlayerValueTooltip = ({ active, payload }) => {
    if (!active || !payload || payload.length === 0) return null;
    const scatterEntry = payload.find((p) => p?.dataKey === "points") || payload[0];
    const d = scatterEntry?.payload;
    if (!d) return null;
    return (
      <div style={{ background: "#0e1f3f", border: "1px solid #2f5ca5", borderRadius: 10, color: "#dcecff", padding: 10 }}>
        <div style={{ fontWeight: 700 }}>{d.name} ({d.player_id})</div>
        <div>Points: {Number(d.points).toFixed(2)}</div>
        <div>Average line: {Number(d.trend).toFixed(2)}</div>
        <div>Distance: {Number(d.distance) >= 0 ? "+" : ""}{Number(d.distance).toFixed(2)}</div>
        <div>Price: {d.price}</div>
      </div>
    );
  };

  return (
    <div className="card sub">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">Generate player valuation data first to view price vs points.</p>
      ) : (
        <div className="stack">
          <div className="value-chart-wrap">
            <ResponsiveContainer width="100%" height={380}>
              <ComposedChart data={chartRows} margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                <CartesianGrid stroke="#284061" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="price"
                  domain={[minPrice, maxPrice]}
                  tick={{ fill: "#9fc5ff", fontSize: 12 }}
                  axisLine={{ stroke: "#365a89" }}
                  tickLine={{ stroke: "#365a89" }}
                  name="Price"
                />
                <YAxis
                  type="number"
                  dataKey="points"
                  tick={{ fill: "#9fc5ff", fontSize: 12 }}
                  axisLine={{ stroke: "#365a89" }}
                  tickLine={{ stroke: "#365a89" }}
                  name="Points"
                />
                <Tooltip
                  shared={false}
                  cursor={false}
                  content={<PlayerValueTooltip />}
                />
                <Legend wrapperStyle={{ color: "#9fc5ff" }} />
                <Line
                  type="linear"
                  dataKey="trend"
                  name="Average line"
                  stroke="#35a2ff"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
                <Scatter name="Players" dataKey="points" fill="#4fc3ff" />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="muted">Trend line (average): points = {intercept.toFixed(2)} + {slope.toFixed(4)} x price</p>
          <div className="grid two">
            <Input label="Search Players" value={search} onChange={setSearch} placeholder="name, player id, or team id" />
            <Select
              label="Sort Table"
              value={sortBy}
              onChange={setSortBy}
              options={[
                { value: "distance_desc", label: "Distance (high to low)" },
                { value: "distance_asc", label: "Distance (low to high)" },
                { value: "distance_abs_desc", label: "Distance (abs high to low)" },
                { value: "points_desc", label: "Points (high to low)" },
                { value: "price_asc", label: "Price (low to high)" },
                { value: "price_desc", label: "Price (high to low)" },
                { value: "name_asc", label: "Name (A-Z)" },
              ]}
            />
          </div>
          <p className="muted">
            Showing {filteredRows.length} of {rows.length} players
          </p>
          <table>
            <thead>
              <tr>
                <th>Player</th>
                <th>Player ID</th>
                <th>Price</th>
                <th>Points</th>
                <th>On Line</th>
                <th>Distance</th>
              </tr>
            </thead>
            <tbody>
              {filteredRows.map((r) => (
                <tr key={`dist-${r.player_id}`}>
                  <td>{r.name}</td>
                  <td>{r.player_id}</td>
                  <td>{r.price}</td>
                  <td>{r.points.toFixed(2)}</td>
                  <td>{r.on_line.toFixed(2)}</td>
                  <td>{r.distance >= 0 ? "+" : ""}{r.distance.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function GroupStageTab({
  teams,
  teamLookup,
  selected,
  setSelected,
  bo,
  setBo,
  sims,
  setSims,
  results,
  setResults,
  simUpdatedAt,
  onResetSimulation,
  onOpenPlayer,
}) {
  const [busy, setBusy] = useState(false);
  const [processedSims, setProcessedSims] = useState(0);
  const [totalSims, setTotalSims] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [runMessage, setRunMessage] = useState("");
  const [playerLookup, setPlayerLookup] = useState({});
  const simPollingRef = useRef(false);
  const SIM_JOB_ID_KEY = "swiss_sim_job_id";
  const SIM_JOB_STARTED_AT_KEY = "swiss_sim_job_started_at";
  const toggle = (tid) =>
    setSelected((prev) => (prev.includes(tid) ? prev.filter((x) => x !== tid) : [...prev, tid]));
  const selectAllTeams = () => setSelected(teams.map((t) => t.team_id));
  const clearTeams = () => setSelected([]);

  const loadPlayers = async () => {
    const data = await api.get("/players/");
    const map = {};
    data.forEach((p) => {
      map[p.player_id] = p.name;
    });
    setPlayerLookup(map);
  };

  useEffect(() => {
    loadPlayers();
  }, []);

  const formatEta = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "-";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return `${h}h ${m}m ${r}s`;
    if (m > 0) return `${m}m ${r}s`;
    return `${r}s`;
  };

  const run = async () => {
    if (selected.length < 2) return;
    setBusy(true);
    setRunMessage("");
    setProcessedSims(0);
    setTotalSims(128);
    setEtaSeconds(null);
    const vrs = {};
    teams.forEach((t) => {
      if (selected.includes(t.team_id)) vrs[t.team_id] = t.vrs_rank ?? 999;
    });
    const body = {
      team_ids: selected,
      vrs_ranks: vrs,
      bo3_mode: bo,
      n_sims: Number(sims || 0),
    };
    const pollSimulationJob = async (jobId, startedAtMs) => {
      if (!jobId) return;
      if (simPollingRef.current) return;
      simPollingRef.current = true;
      try {
        let done = false;
        while (!done) {
          const status = await api.get(`/simulate/job/${jobId}`);
          if (status?.detail) {
            setRunMessage(String(status.detail));
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            return;
          }
          const processed = Number(status.processed_sims || 0);
          const total = Number(status.total_sims || 0);
          setProcessedSims(processed);
          setTotalSims(total);
          if (processed > 0 && total > processed) {
            const elapsedSec = Math.max(0.001, (Date.now() - startedAtMs) / 1000);
            const rate = processed / elapsedSec;
            if (rate > 0) setEtaSeconds((total - processed) / rate);
          } else if (total > 0 && processed >= total) {
            setEtaSeconds(0);
          }

          if (status.status === "failed") {
            setRunMessage(status.error || "Simulation failed.");
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            return;
          }
          if (status.status === "completed") {
            setResults(status.result || null);
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
      } finally {
        simPollingRef.current = false;
      }
    };
    try {
      const start = await api.post("/simulate/start", body);
      if (start?.detail) {
        setRunMessage(String(start.detail));
        return;
      }
      const jobId = start?.job_id;
      if (!jobId) {
        setRunMessage("Failed to start simulation job.");
        return;
      }
      const startedAt = Date.now();
      localStorage.setItem(SIM_JOB_ID_KEY, String(jobId));
      localStorage.setItem(SIM_JOB_STARTED_AT_KEY, String(startedAt));
      await pollSimulationJob(jobId, startedAt);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const resumeSimulationJob = async () => {
      const jobId = localStorage.getItem(SIM_JOB_ID_KEY);
      if (!jobId || simPollingRef.current) return;
      const startedAt = Number(localStorage.getItem(SIM_JOB_STARTED_AT_KEY) || Date.now());
      simPollingRef.current = true;
      setBusy(true);
      try {
        let done = false;
        while (!done) {
          const status = await api.get(`/simulate/job/${jobId}`);
          if (status?.detail) {
            setRunMessage(String(status.detail));
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            return;
          }
          const processed = Number(status.processed_sims || 0);
          const total = Number(status.total_sims || 0);
          setProcessedSims(processed);
          setTotalSims(total);
          if (processed > 0 && total > processed) {
            const elapsedSec = Math.max(0.001, (Date.now() - startedAt) / 1000);
            const rate = processed / elapsedSec;
            if (rate > 0) setEtaSeconds((total - processed) / rate);
          } else if (total > 0 && processed >= total) {
            setEtaSeconds(0);
          }
          if (status.status === "failed") {
            setRunMessage(status.error || "Simulation failed.");
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            return;
          }
          if (status.status === "completed") {
            setResults(status.result || null);
            localStorage.removeItem(SIM_JOB_ID_KEY);
            localStorage.removeItem(SIM_JOB_STARTED_AT_KEY);
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
      } finally {
        simPollingRef.current = false;
        setBusy(false);
      }
    };
    resumeSimulationJob();
  }, []);

  return (
    <Section title="Swiss Group Stage">
      <div className="stack">
        <div className="actions" style={{ marginTop: 0 }}>
          <button className="secondary" onClick={selectAllTeams}>
            Select All
          </button>
          <button className="secondary" onClick={clearTeams} disabled={selected.length === 0}>
            Clear
          </button>
        </div>
        <div className="chips">
          {teams.map((t) => (
            <button
              key={t.team_id}
              className={selected.includes(t.team_id) ? "chip active" : "chip"}
              onClick={() => toggle(t.team_id)}
            >
              {t.name} <Badge>id {t.team_id}</Badge>
            </button>
          ))}
        </div>
        <div className="grid three">
          <Select
            label="BO Mode"
            value={bo}
            onChange={setBo}
            options={[
              { value: "elim_qual", label: "BO3 on Elimination/Qualification Matches" },
              { value: "all", label: "BO3 on All Matches" },
              { value: "none", label: "No BO3 (All BO1)" },
            ]}
          />
          <Input label="# Sims" value={sims} onChange={setSims} />
          <div className="field">
            <span>Run</span>
            <button className="primary" onClick={run} disabled={busy || selected.length < 2}>
              {busy ? "Running..." : "Run Swiss Group Stage"}
            </button>
          </div>
        </div>
        <div className="actions" style={{ marginTop: 8 }}>
          <button className="danger" onClick={onResetSimulation} disabled={busy || !results}>
            Reset Stored Simulation
          </button>
          {simUpdatedAt && <p className="muted">Stored: {new Date(simUpdatedAt).toLocaleString()}</p>}
        </div>
        {busy && (
          <div className="card sub">
            <p className="muted">
              Running Swiss simulations: {processedSims.toLocaleString()} / {totalSims.toLocaleString()}
            </p>
            <p className="muted">ETA: {formatEta(etaSeconds)}</p>
            <div className="progress">
              <div
                className="progress-bar determinate"
                style={{ width: `${totalSims > 0 ? Math.min(100, (processedSims / totalSims) * 100) : 0}%` }}
              />
            </div>
          </div>
        )}
        {runMessage && (
          <div className="card sub">
            <p className="muted">{runMessage}</p>
          </div>
        )}
        {results && (
          <div className="grid two">
            {Object.entries(results).map(([tid, data]) => (
              <div key={tid} className="card sub">
                <h3>{teamLookup[Number(tid)] || `Team ${tid}`}</h3>
                <div className="records">
                  {["3-0", "3-1", "3-2", "2-3", "1-3", "0-3"].map((r) => (
                    <span key={r}>
                      {r}: {(data[r] * 100).toFixed(1)}%
                    </span>
                  ))}
                </div>
                <table className="swiss-player-table">
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Total</th>
                      <th>Rating</th>
                      <th>Win</th>
                      <th>Role</th>
                      <th>Booster</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.players || {}).map(([pid, comps]) => (
                      <tr key={pid}>
                        <td className="player-col" title={String(playerLookup[Number(pid)] || pid)}>
                          <button className="inline-link-btn" onClick={() => onOpenPlayer && onOpenPlayer(Number(pid))}>
                            {playerLookup[Number(pid)] || pid}
                          </button>
                        </td>
                        <td>{comps.total.toFixed(2)}</td>
                        <td>{comps.rating.toFixed(2)}</td>
                        <td>{comps.win.toFixed(2)}</td>
                        <td>{comps.role.toFixed(2)}</td>
                        <td>{comps.booster.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
      </div>
    </Section>
  );
}

function TopTeamsTab({ teamLookup, selected, bo, sims, results, onOpenPlayer }) {
  const [busy, setBusy] = useState(false);
  const [generationBusy, setGenerationBusy] = useState(false);
  const [cacheId, setCacheId] = useState("");
  const [totalTeams, setTotalTeams] = useState(0);
  const [filteredCount, setFilteredCount] = useState(0);
  const [topTeams, setTopTeams] = useState([]);
  const [pageTeams, setPageTeams] = useState([]);
  const [page, setPage] = useState(0);
  const [comboSearch, setComboSearch] = useState("");
  const [sortKey, setSortKey] = useState("ev_desc");
  const [message, setMessage] = useState("");
  const [include, setInclude] = useState(new Set());
  const [exclude, setExclude] = useState(new Set());
  const [appliedInclude, setAppliedInclude] = useState(new Set());
  const [appliedExclude, setAppliedExclude] = useState(new Set());
  const [includeInput, setIncludeInput] = useState("");
  const [excludeInput, setExcludeInput] = useState("");
  const [processedCombos, setProcessedCombos] = useState(0);
  const [totalCombos, setTotalCombos] = useState(0);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [jobPhase, setJobPhase] = useState("queued");
  const [finalizeProgress, setFinalizeProgress] = useState(0);
  const [finalizeStep, setFinalizeStep] = useState("");
  const [filterModalOpen, setFilterModalOpen] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");
  const [allPlayers, setAllPlayers] = useState([]);
  const topTeamsPollingRef = useRef(false);
  const TOP5_JOB_ID_KEY = "swiss_top5_job_id";
  const TOP5_JOB_STARTED_AT_KEY = "swiss_top5_job_started_at";

  const clearStoredData = () => {
    setCacheId("");
    setTotalTeams(0);
    setFilteredCount(0);
    setTopTeams([]);
    setPageTeams([]);
    setPage(0);
    setComboSearch("");
    setInclude(new Set());
    setExclude(new Set());
    setAppliedInclude(new Set());
    setAppliedExclude(new Set());
    setIncludeInput("");
    setExcludeInput("");
    setProcessedCombos(0);
    setTotalCombos(0);
    setEtaSeconds(null);
    setJobPhase("queued");
    setFinalizeProgress(0);
    setFinalizeStep("");
    setMessage("");
  };

  const formatEta = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "-";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return `${h}h ${m}m ${r}s`;
    if (m > 0) return `${m}m ${r}s`;
    return `${r}s`;
  };

  const parsePlayerIdSet = (text) => {
    const ids = (text || "")
      .split(",")
      .map((x) => Number(x.trim()))
      .filter((x) => Number.isFinite(x) && x > 0);
    return new Set(ids);
  };

  const parseIdsToSet = (text) =>
    new Set(
      (text || "")
        .split(",")
        .map((x) => Number(x.trim()))
        .filter((x) => Number.isFinite(x) && x > 0)
    );

  const setFromSetToCsv = (idsSet) => Array.from(idsSet).sort((a, b) => a - b).join(", ");

  const toggleIncludeId = (pid) => {
    const includeSet = parseIdsToSet(includeInput);
    const excludeSet = parseIdsToSet(excludeInput);
    if (includeSet.has(pid)) {
      includeSet.delete(pid);
    } else {
      includeSet.add(pid);
      excludeSet.delete(pid);
    }
    setIncludeInput(setFromSetToCsv(includeSet));
    setExcludeInput(setFromSetToCsv(excludeSet));
  };

  const toggleExcludeId = (pid) => {
    const includeSet = parseIdsToSet(includeInput);
    const excludeSet = parseIdsToSet(excludeInput);
    if (excludeSet.has(pid)) {
      excludeSet.delete(pid);
    } else {
      excludeSet.add(pid);
      includeSet.delete(pid);
    }
    setIncludeInput(setFromSetToCsv(includeSet));
    setExcludeInput(setFromSetToCsv(excludeSet));
  };

  useEffect(() => {
    setInclude(parsePlayerIdSet(includeInput));
  }, [includeInput]);

  useEffect(() => {
    setExclude(parsePlayerIdSet(excludeInput));
  }, [excludeInput]);

  useEffect(() => {
    if (allPlayers.length > 0 || !results) return;
    const loadPlayers = async () => {
      const data = await api.get("/players/");
      setAllPlayers(Array.isArray(data) ? data : []);
    };
    loadPlayers();
  }, [results, allPlayers.length]);

  useEffect(() => {
    clearStoredData();
  }, [results, selected, bo, sims]);

  const queryStoredTeams = async (targetPage = page, activeCacheId = cacheId) => {
    if (!activeCacheId) return;
    setBusy(true);
    try {
      const res = await api.post("/best-team/query", {
        cache_id: activeCacheId,
        include_player_ids: Array.from(appliedInclude),
        exclude_player_ids: Array.from(appliedExclude),
        search: comboSearch,
        sort_key: sortKey,
        page: targetPage,
        page_size: 200,
      });
      if (res?.detail) {
        setMessage(String(res.detail));
        return;
      }
      setTotalTeams(res.total_teams || 0);
      setFilteredCount(res.filtered_count || 0);
      setTopTeams(res.top_teams || []);
      setPageTeams(res.page_teams || []);
      setPage(targetPage);
    } finally {
      setBusy(false);
    }
  };

  const findTopTeams = async () => {
    if (!results) return;
    setBusy(true);
    setGenerationBusy(true);
    setProcessedCombos(0);
    setTotalCombos(0);
    setEtaSeconds(null);
    setJobPhase("queued");
    setFinalizeProgress(0);
    setFinalizeStep("");
    setMessage("");
    const pollTopTeamsJob = async (jobId, startedAtMs) => {
      if (!jobId) return;
      if (topTeamsPollingRef.current) return;
      topTeamsPollingRef.current = true;
      try {
        let done = false;
        let finalResult = null;
        while (!done) {
          const status = await api.get(`/best-team/job/${jobId}`);
          if (status?.detail) {
            setMessage(String(status.detail));
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            return;
          }
          const processed = Number(status.processed_combinations || 0);
          const total = Number(status.total_combinations || 0);
          setJobPhase(String(status.phase || status.status || "queued"));
          setFinalizeProgress(Number(status.finalize_progress || 0));
          setFinalizeStep(String(status.finalize_step || ""));
          setProcessedCombos(processed);
          setTotalCombos(total);
          if (processed > 0 && total > processed) {
            const elapsedSec = Math.max(0.001, (Date.now() - startedAtMs) / 1000);
            const rate = processed / elapsedSec;
            if (rate > 0) {
              setEtaSeconds((total - processed) / rate);
            }
          } else if (total > 0 && processed >= total) {
            setEtaSeconds(0);
          }

          if (status.status === "failed") {
            setMessage(status.error || "Top 5 generation failed.");
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            return;
          }
          if (status.status === "completed") {
            finalResult = status.result;
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }

        const data = finalResult || {};
        if (data.error) {
          setMessage(data.error);
          setCacheId("");
          setTotalTeams(0);
          setFilteredCount(0);
          setTopTeams([]);
          setPageTeams([]);
          return;
        }
        setCacheId(data.cache_id || "");
        setTotalTeams(data.total_teams || 0);
        setTopTeams(data.top_teams || []);
        setFilteredCount(data.total_teams || 0);
        setPage(0);
        setMessage(`Stored ${data.total_teams || 0} team combinations.`);
        await queryStoredTeams(0);
      } finally {
        topTeamsPollingRef.current = false;
      }
    };
    try {
      const start = await api.post("/best-team/from-latest/start", {});
      if (start?.detail) {
        setMessage(String(start.detail));
        return;
      }
      const jobId = start.job_id;
      if (!jobId) {
        setMessage("Failed to start Top 5 generation job.");
        return;
      }
      const startedAt = Date.now();
      localStorage.setItem(TOP5_JOB_ID_KEY, String(jobId));
      localStorage.setItem(TOP5_JOB_STARTED_AT_KEY, String(startedAt));
      await pollTopTeamsJob(jobId, startedAt);
    } finally {
      setGenerationBusy(false);
      setBusy(false);
    }
  };

  useEffect(() => {
    if (!cacheId) return;
    queryStoredTeams(0);
  }, [appliedInclude, appliedExclude, comboSearch, sortKey]);

  useEffect(() => {
    const loadLatestCache = async () => {
      if (!results) return;
      const latest = await api.get("/best-team/latest");
      if (!latest?.exists || !latest?.cache_id) return;
      setCacheId(latest.cache_id);
      setTotalTeams(latest.total_teams || 0);
      setFilteredCount(latest.total_teams || 0);
      setMessage(`Loaded persisted team combinations (${latest.total_teams || 0}).`);
      await queryStoredTeams(0, latest.cache_id);
    };
    loadLatestCache();
  }, [results]);

  const modalMatches = useMemo(() => {
    const q = filterSearch.trim().toLowerCase();
    if (!q) return [];
    return allPlayers
      .filter((p) => String(p.name || "").toLowerCase().includes(q))
      .slice(0, 25);
  }, [allPlayers, filterSearch]);

  const playerNameById = useMemo(() => {
    const m = {};
    allPlayers.forEach((p) => {
      m[Number(p.player_id)] = String(p.name || p.player_id);
    });
    return m;
  }, [allPlayers]);
  const includedIds = useMemo(() => Array.from(parseIdsToSet(includeInput)).sort((a, b) => a - b), [includeInput]);
  const excludedIds = useMemo(() => Array.from(parseIdsToSet(excludeInput)).sort((a, b) => a - b), [excludeInput]);

  useEffect(() => {
    const resumeTop5Job = async () => {
      if (!results) return;
      const jobId = localStorage.getItem(TOP5_JOB_ID_KEY);
      if (!jobId || topTeamsPollingRef.current) return;
      const startedAt = Number(localStorage.getItem(TOP5_JOB_STARTED_AT_KEY) || Date.now());
      topTeamsPollingRef.current = true;
      setBusy(true);
      setGenerationBusy(true);
      try {
        let done = false;
        let finalResult = null;
        while (!done) {
          const status = await api.get(`/best-team/job/${jobId}`);
          if (status?.detail) {
            setMessage(String(status.detail));
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            return;
          }
          const processed = Number(status.processed_combinations || 0);
          const total = Number(status.total_combinations || 0);
          setJobPhase(String(status.phase || status.status || "queued"));
          setFinalizeProgress(Number(status.finalize_progress || 0));
          setFinalizeStep(String(status.finalize_step || ""));
          setProcessedCombos(processed);
          setTotalCombos(total);
          if (processed > 0 && total > processed) {
            const elapsedSec = Math.max(0.001, (Date.now() - startedAt) / 1000);
            const rate = processed / elapsedSec;
            if (rate > 0) {
              setEtaSeconds((total - processed) / rate);
            }
          } else if (total > 0 && processed >= total) {
            setEtaSeconds(0);
          }

          if (status.status === "failed") {
            setMessage(status.error || "Top 5 generation failed.");
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            return;
          }
          if (status.status === "completed") {
            finalResult = status.result;
            localStorage.removeItem(TOP5_JOB_ID_KEY);
            localStorage.removeItem(TOP5_JOB_STARTED_AT_KEY);
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
        const data = finalResult || {};
        if (data.error) {
          setMessage(data.error);
          return;
        }
        setCacheId(data.cache_id || "");
        setTotalTeams(data.total_teams || 0);
        setTopTeams(data.top_teams || []);
        setFilteredCount(data.total_teams || 0);
        setPage(0);
        setMessage(`Stored ${data.total_teams || 0} team combinations.`);
        await queryStoredTeams(0);
      } finally {
        topTeamsPollingRef.current = false;
        setGenerationBusy(false);
        setBusy(false);
      }
    };
    resumeTop5Job();
  }, [results]);

  return (
    <Section title="Top 5 Teams">
      <div className="stack">
        {!results && (
          <div className="card sub">
            <p className="muted">Run Swiss Group Stage in the Group Stage tab first.</p>
          </div>
        )}
        {results && (
          <div className="card sub">
            <p className="muted">
              Using Group Stage setup: {selected.length} teams, {sims} simulations, mode{" "}
              {bo === "elim_qual" ? "BO3 on Elimination/Qualification Matches" : bo === "all" ? "BO3 on All Matches" : "No BO3 (All BO1)"}.
            </p>
          </div>
        )}
        {results && (
          <div className="actions">
            <button className="primary" onClick={findTopTeams} disabled={busy}>
              {busy ? "Working..." : "Generate & Store Team Combos"}
            </button>
            <button
              className="danger"
              onClick={async () => {
                if (cacheId) await api.delete(`/best-team/cache/${cacheId}`);
                clearStoredData();
              }}
              disabled={!cacheId}
            >
              Delete Stored Data
            </button>
          </div>
        )}
        {generationBusy && (
          <div className="card sub">
            <p className="muted">
              Processing combinations: {processedCombos.toLocaleString()} / {totalCombos.toLocaleString()}
            </p>
            <p className="muted">ETA: {formatEta(etaSeconds)}</p>
            <div className="progress">
              <div
                className="progress-bar determinate"
                style={{ width: `${totalCombos > 0 ? Math.min(100, (processedCombos / totalCombos) * 100) : 0}%` }}
              />
            </div>
            <p className="muted" style={{ marginTop: 8 }}>
              Finalizing/Persisting: {Math.round(finalizeProgress * 100)}%{finalizeStep ? ` (${finalizeStep})` : ""}
            </p>
            <div className="progress">
              <div className="progress-bar determinate" style={{ width: `${Math.min(100, Math.max(0, finalizeProgress * 100))}%` }} />
            </div>
            <p className="muted">Phase: {jobPhase}</p>
          </div>
        )}
        {message && (
          <div className="card sub">
            <p className="muted">{message}</p>
          </div>
        )}
        {cacheId && topTeams && topTeams.length > 0 && (
          <div className="card sub">
            <h3>Top Teams (Filtered)</h3>
            <div className="top5-filters">
              <div className="grid two">
                <div className="field">
                  <span>Included Players</span>
                  <div className="chips">
                    {includedIds.length === 0 && <span className="muted">None</span>}
                    {includedIds.map((pid) => (
                      <span key={`inc-${pid}`} className="chip active">
                        {playerNameById[pid] || `Player ${pid}`}
                        <button className="close" style={{ marginLeft: 8, padding: "2px 6px" }} onClick={() => toggleIncludeId(pid)}>
                          x
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
                <div className="field">
                  <span>Excluded Players</span>
                  <div className="chips">
                    {excludedIds.length === 0 && <span className="muted">None</span>}
                    {excludedIds.map((pid) => (
                      <span key={`exc-${pid}`} className="chip active">
                        {playerNameById[pid] || `Player ${pid}`}
                        <button className="close" style={{ marginLeft: 8, padding: "2px 6px" }} onClick={() => toggleExcludeId(pid)}>
                          x
                        </button>
                      </span>
                    ))}
                  </div>
                </div>
              </div>
              <div className="actions top5-filter-actions">
                <button className="secondary" onClick={() => setFilterModalOpen(true)}>
                  Include/Exclude by Name
                </button>
                <button
                  className="primary"
                  onClick={() => {
                    setAppliedInclude(new Set(Array.from(include)));
                    setAppliedExclude(new Set(Array.from(exclude)));
                  }}
                  disabled={!cacheId}
                >
                  Apply Filters
                </button>
              </div>
              <div className="grid three top5-controls">
                <Input label="Search Combos" value={comboSearch} onChange={setComboSearch} placeholder="Player/team name or id" />
                <Select
                  label="Sort by"
                  value={sortKey}
                  onChange={setSortKey}
                  options={[
                    { value: "ev_desc", label: "EV desc" },
                    { value: "ev_asc", label: "EV asc" },
                    { value: "cost_asc", label: "Cost asc" },
                    { value: "cost_desc", label: "Cost desc" },
                    { value: "cpp_desc", label: "Value (EV/Cost) desc" },
                  ]}
                />
                <div className="field top5-counter">
                  <span>Filtered / Stored</span>
                  <p className="muted">{filteredCount} / {totalTeams}</p>
                </div>
              </div>
            </div>
            {topTeams.map((team, idx) => (
              <div key={idx} className="card sub">
                <h4>
                  #{idx + 1} EV {team.total_ev.toFixed(2)} | Cost {team.cost}
                </h4>
                <table>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Team</th>
                      <th>Assigned Role</th>
                      <th>Cost</th>
                      <th>Total EV</th>
                      <th>Rating</th>
                      <th>Win</th>
                      <th>Role</th>
                      <th>Booster</th>
                    </tr>
                  </thead>
                  <tbody>
                    {team.players.map((p) => (
                      <tr key={p.player_id}>
                        <td>
                          <button className="inline-link-btn" onClick={() => onOpenPlayer && onOpenPlayer(Number(p.player_id))}>
                            {p.name}
                          </button>
                        </td>
                        <td>{teamLookup[p.team_id] || p.team_id}</td>
                        <td>{roleLabel(p.role_name)}</td>
                        <td>{p.price}</td>
                        <td>{p.total_ev.toFixed(2)}</td>
                        <td>{p.rating_ev.toFixed(2)}</td>
                        <td>{p.win_ev.toFixed(2)}</td>
                        <td>{p.role_ev.toFixed(2)}</td>
                        <td>{p.booster_ev.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
        {cacheId && filteredCount > 0 && (
          <div className="card sub">
            <h3>All Filtered Teams ({filteredCount})</h3>
            <div className="actions">
              <button
                className="secondary"
                onClick={() => {
                  const prev = Math.max(0, page - 1);
                  if (prev !== page) queryStoredTeams(prev);
                }}
                disabled={page === 0}
              >
                Prev 200
              </button>
              <button
                className="secondary"
                onClick={() => {
                  const next = (page + 1) * 200 < filteredCount ? page + 1 : page;
                  if (next !== page) queryStoredTeams(next);
                }}
                disabled={(page + 1) * 200 >= filteredCount}
              >
                Next 200
              </button>
              <p className="muted">
                Page {page + 1} showing {Math.min(200, Math.max(0, filteredCount - page * 200))} of {filteredCount}
              </p>
            </div>
            <table>
              <thead>
                <tr>
                  <th>#</th>
                  <th>EV</th>
                  <th>Cost</th>
                  <th>Players</th>
                </tr>
              </thead>
              <tbody>
                {pageTeams.map((team, idx) => (
                  <tr key={idx + page * 200}>
                    <td>{idx + 1 + page * 200}</td>
                    <td>{team.total_ev.toFixed(2)}</td>
                    <td>{team.cost}</td>
                    <td>{team.players.map((p) => `${p.name} (${teamLookup[p.team_id] || p.team_id}, ${roleLabel(p.role_name)})`).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {cacheId && filteredCount === 0 && (
          <div className="card sub">
            <p className="muted">No team combinations match current include/exclude/search filters.</p>
          </div>
        )}
        {filterModalOpen && (
          <div className="modal-backdrop" onClick={() => setFilterModalOpen(false)}>
            <div className="modal" onClick={(e) => e.stopPropagation()}>
              <div className="modal-header">
                <h3>Include/Exclude Players</h3>
                <button className="close" onClick={() => setFilterModalOpen(false)}>
                  Close
                </button>
              </div>
              <div className="modal-body">
                <Input
                  label="Search player name"
                  value={filterSearch}
                  onChange={setFilterSearch}
                  placeholder="Type e.g. zy to find ZywOo"
                />
                {filterSearch.trim().length === 0 && <p className="muted">Start typing a player name.</p>}
                {filterSearch.trim().length > 0 && modalMatches.length === 0 && <p className="muted">No matching players.</p>}
                {modalMatches.length > 0 && (
                  <table>
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>ID</th>
                        <th>Include</th>
                        <th>Exclude</th>
                      </tr>
                    </thead>
                    <tbody>
                      {modalMatches.map((p) => {
                        const pid = Number(p.player_id);
                        const isIncluded = include.has(pid);
                        const isExcluded = exclude.has(pid);
                        return (
                          <tr key={pid}>
                            <td>{p.name}</td>
                            <td>{pid}</td>
                            <td>
                              <button
                                className={isIncluded ? "chip active" : "chip"}
                                onClick={() => toggleIncludeId(pid)}
                              >
                                {isIncluded ? "Included" : "Include"}
                              </button>
                            </td>
                            <td>
                              <button
                                className={isExcluded ? "chip active" : "chip"}
                                onClick={() => toggleExcludeId(pid)}
                              >
                                {isExcluded ? "Excluded" : "Exclude"}
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    </Section>
  );
}

function BracketTab({ teams, teamLookup }) {
  const [selected, setSelected] = useState([]);
  const [simState, setSimState] = useState(null);
  const [winnerPicks, setWinnerPicks] = useState({});
  const [busy, setBusy] = useState(false);

  const toggle = (tid) =>
    setSelected((prev) => (prev.includes(tid) ? prev.filter((x) => x !== tid) : [...prev, tid]));
  const selectAllTeams = () => setSelected(teams.map((t) => t.team_id));
  const clearTeams = () => setSelected([]);
  const resetSimulator = () => {
    setSimState(null);
    setWinnerPicks({});
  };

  const startSimulator = async () => {
    if (selected.length < 2) return;
    if (selected.length % 2 !== 0) return;
    setBusy(true);
    const vrs = {};
    teams.forEach((t) => {
      if (selected.includes(t.team_id)) vrs[t.team_id] = t.vrs_rank ?? 999;
    });
    const data = await api.post("/bracket/swiss-manual/init", {
      team_ids: selected,
      vrs_ranks: vrs,
    });
    setSimState(data);
    setWinnerPicks({});
    setBusy(false);
  };

  const allCurrentMatches = (simState?.pools || []).flatMap((p) => p.matches || []);

  const applyRound = async () => {
    if (!simState || simState.done) return;
    if (allCurrentMatches.length === 0) return;

    const missing = allCurrentMatches.some((m) => !winnerPicks[m.match_id]);
    if (missing) return;

    setBusy(true);
    const results = allCurrentMatches.map((m) => ({
      team_a_id: m.team_a_id,
      team_b_id: m.team_b_id,
      winner_id: winnerPicks[m.match_id],
    }));
    const next = await api.post("/bracket/swiss-manual/apply-round", {
      team_states: simState.team_states,
      results,
    });
    setSimState(next);
    setWinnerPicks({});
    setBusy(false);
  };

  return (
    <Section title="Swiss Bracket Simulator">
      <div className="stack">
        {!simState && (
          <>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="secondary" onClick={selectAllTeams}>
                Select All
              </button>
              <button className="secondary" onClick={clearTeams} disabled={selected.length === 0}>
                Clear
              </button>
            </div>
            <div className="chips">
              {teams.map((t) => (
                <button
                  key={t.team_id}
                  className={selected.includes(t.team_id) ? "chip active" : "chip"}
                  onClick={() => toggle(t.team_id)}
                >
                  {t.name} <Badge>id {t.team_id}</Badge>
                </button>
              ))}
            </div>
            <div className="actions">
              <button className="primary" onClick={startSimulator} disabled={busy || selected.length < 2 || selected.length % 2 !== 0}>
                {busy ? "Starting..." : "Start Bracket Simulator"}
              </button>
            </div>
            {selected.length % 2 !== 0 && selected.length > 0 && (
              <p className="muted">Select an even number of teams to start.</p>
            )}
          </>
        )}

        {simState && (
          <>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="secondary" onClick={resetSimulator}>
                New Bracket
              </button>
            </div>

            {!simState.done && (
              <div className="card sub">
                <h3>Round {simState.round}</h3>
                {(simState.pools || []).map((pool) => (
                  <div key={pool.record} className="card sub">
                    <h4>Pool {pool.record}</h4>
                    {(pool.matches || []).map((m) => {
                      const a = teamLookup[m.team_a_id] || `Team ${m.team_a_id}`;
                      const b = teamLookup[m.team_b_id] || `Team ${m.team_b_id}`;
                      const picked = winnerPicks[m.match_id];
                      return (
                        <div key={m.match_id} className="actions" style={{ marginTop: 8 }}>
                          <span className="muted" style={{ minWidth: 220 }}>{a} vs {b}</span>
                          <button
                            className={picked === m.team_a_id ? "chip active" : "chip"}
                            onClick={() => setWinnerPicks((prev) => ({ ...prev, [m.match_id]: m.team_a_id }))}
                          >
                            {a}
                          </button>
                          <button
                            className={picked === m.team_b_id ? "chip active" : "chip"}
                            onClick={() => setWinnerPicks((prev) => ({ ...prev, [m.match_id]: m.team_b_id }))}
                          >
                            {b}
                          </button>
                        </div>
                      );
                    })}
                  </div>
                ))}
                <div className="actions">
                  <button className="primary" onClick={applyRound} disabled={busy}>
                    {busy ? "Applying..." : "Apply Round"}
                  </button>
                </div>
              </div>
            )}

            <div className="card sub">
              <h3>{simState.done ? "Final Standings" : "Current Standings"}</h3>
              <table>
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>Record</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {(simState.standings || []).map((s) => (
                    <tr key={s.team_id}>
                      <td>{teamLookup[s.team_id] || `Team ${s.team_id}`}</td>
                      <td>{s.wins}-{s.losses}</td>
                      <td>{s.qualified ? "Qualified" : s.eliminated ? "Eliminated" : "Alive"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>
    </Section>
  );
}

function SwissPlayerValueTab({ results, players }) {
  const valueData = useMemo(() => buildPlayerValueRowsFromSimulation(results, players), [results, players]);

  return (
    <Section title="Player Value">
      <div className="stack">
        {!results && (
          <div className="card sub">
            <p className="muted">Run Swiss Group Stage first.</p>
          </div>
        )}
        {results && (
          <div className="card sub">
            <p className="muted">Loaded {valueData.rows.length} players from Swiss valuations.</p>
          </div>
        )}
        {results && (
          <PriceVsPointsPanel
            title="Player Price vs Points (Swiss)"
            rows={valueData.rows}
            slope={valueData.slope}
            intercept={valueData.intercept}
          />
        )}
      </div>
    </Section>
  );
}

function PlayoffTab({ teams, teamLookup, players, sortTeams, applyFilters, onOpenPlayer }) {
  const [playoffTab, setPlayoffTab] = useState("stage");
  const [events, setEvents] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [eventTeamNames, setEventTeamNames] = useState(new Set());
  const [latestPayload, setLatestPayload] = useState(null);
  const [updatedAt, setUpdatedAt] = useState("");
  const [slots, setSlots] = useState(Array(8).fill(""));
  const [hasThirdPlaceDecider, setHasThirdPlaceDecider] = useState(false);
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [topTeams, setTopTeams] = useState(null);
  const [allTeams, setAllTeams] = useState(null);
  const [baseTeams, setBaseTeams] = useState(null);
  const [filteredCount, setFilteredCount] = useState(0);
  const [page, setPage] = useState(0);
  const [topMessage, setTopMessage] = useState("");
  const [includeSet, setIncludeSet] = useState(new Set());
  const [excludeSet, setExcludeSet] = useState(new Set());
  const [includeTeamSet, setIncludeTeamSet] = useState(new Set());
  const [excludeTeamSet, setExcludeTeamSet] = useState(new Set());
  const [appliedIncludeSet, setAppliedIncludeSet] = useState(new Set());
  const [appliedExcludeSet, setAppliedExcludeSet] = useState(new Set());
  const [appliedIncludeTeamSet, setAppliedIncludeTeamSet] = useState(new Set());
  const [appliedExcludeTeamSet, setAppliedExcludeTeamSet] = useState(new Set());
  const [comboSearch, setComboSearch] = useState("");
  const [showFilterModal, setShowFilterModal] = useState(false);
  const [filterSearch, setFilterSearch] = useState("");
  const [sortKey, setSortKey] = useState("ev_desc");
  const [processedSims, setProcessedSims] = useState(0);
  const [totalSims, setTotalSims] = useState(0);
  const [processedCombos, setProcessedCombos] = useState(0);
  const [totalCombos, setTotalCombos] = useState(0);
  const [topEtaSeconds, setTopEtaSeconds] = useState(null);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [runMessage, setRunMessage] = useState("");
  const playoffPollingRef = useRef(false);
  const normalizeTeamName = (name) => String(name || "").trim().toLowerCase();
  const playerLookup = useMemo(() => {
    const m = {};
    players.forEach((p) => (m[p.player_id] = p.name));
    return m;
  }, [players]);
  const teamByName = useMemo(() => {
    const m = {};
    teams.forEach((t) => {
      const key = String(t?.name || "").trim().toLowerCase();
      if (key) m[key] = t.team_id;
    });
    return m;
  }, [teams]);
  const teamPlayerOptions = useMemo(() => {
    const safePlayers = Array.isArray(players)
      ? players.filter((p) => p && p.player_id !== null && p.player_id !== undefined)
      : [];
    const sorted = [...safePlayers].sort((a, b) => String(a?.name || "").localeCompare(String(b?.name || "")));
    return [
      { value: "", label: "-" },
      ...sorted.map((p) => ({
        value: String(p.player_id),
        label: p.name || `Player ${p.player_id}`,
      })),
    ];
  }, [players]);
  const playerNameById = playerLookup;
  const filteredTeams = useMemo(() => {
    if (!selectedEventId) return [];
    if (!eventTeamNames || eventTeamNames.size === 0) return [];
    return teams.filter((t) => eventTeamNames.has(normalizeTeamName(t.name)));
  }, [teams, selectedEventId, eventTeamNames]);
  const playoffTeamsForFilters = useMemo(() => {
    const selectedIds = Array.from(new Set(slots.map((s) => Number(s)).filter((id) => Number.isFinite(id) && id > 0)));
    const byId = new Map(teams.map((t) => [Number(t.team_id), t]));
    return selectedIds
      .map((id) => byId.get(id))
      .filter(Boolean)
      .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  }, [slots, teams]);
  const teamPlayerIds = (teamId) => {
    const team = teams.find((t) => Number(t.team_id) === Number(teamId));
    if (!team) return [];
    return [team.player1_id, team.player2_id, team.player3_id, team.player4_id, team.player5_id]
      .map((pid) => Number(pid))
      .filter((pid) => Number.isFinite(pid) && pid > 0);
  };
  const buildEffectivePlayerFilters = (playerInclude, playerExclude, teamInclude, teamExclude) => {
    const include = new Set(playerInclude || []);
    const exclude = new Set(playerExclude || []);
    Array.from(teamInclude || []).forEach((tid) => {
      teamPlayerIds(tid).forEach((pid) => include.add(pid));
    });
    Array.from(teamExclude || []).forEach((tid) => {
      teamPlayerIds(tid).forEach((pid) => exclude.add(pid));
    });
    // Include wins over exclude for conflicting picks.
    Array.from(include).forEach((pid) => exclude.delete(pid));
    return { include, exclude };
  };
  const effectiveAppliedFilters = useMemo(
    () => buildEffectivePlayerFilters(appliedIncludeSet, appliedExcludeSet, appliedIncludeTeamSet, appliedExcludeTeamSet),
    [appliedIncludeSet, appliedExcludeSet, appliedIncludeTeamSet, appliedExcludeTeamSet, teams]
  );

  const setSlot = (idx, val) => {
    setSlots((prev) => {
      const next = [...prev];
      next[idx] = val;
      return next;
    });
  };

  const loadEventsForPlayoff = async () => {
    const data = await api.get("/events/");
    if (data?.detail) return;
    const allEvents = Array.isArray(data.events) ? data.events : [];
    setEvents(allEvents);
    const active = data.active_event_id;
    const fallback = allEvents.length > 0 ? allEvents[0].event_id : "";
    const nextSelected = active ?? fallback;
    setSelectedEventId(nextSelected === "" ? "" : String(nextSelected));
  };

  const loadEventTeams = async (eventId) => {
    if (!eventId) {
      setEventTeamNames(new Set());
      return;
    }
    const data = await api.get(`/events/${eventId}`);
    if (data?.detail) return;
    setEventTeamNames(new Set((data.teams || []).map((t) => normalizeTeamName(t.team_name))));
  };

  const loadLatestPlayoff = async () => {
    const data = await api.get("/playoff/latest");
    if (!data?.exists) {
      setLatestPayload(null);
      setResults(null);
      setUpdatedAt("");
      return;
    }
    const payload = data.payload || {};
    setLatestPayload(payload);
    setSlots((payload.team_slots || []).map((x) => String(x)));
    setHasThirdPlaceDecider(Boolean(payload.has_third_place_decider));
    setResults(data.results || null);
    setUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  useEffect(() => {
    loadEventsForPlayoff();
    loadLatestPlayoff();
  }, []);

  useEffect(() => {
    loadEventTeams(selectedEventId);
  }, [selectedEventId]);

  useEffect(() => {
    const allowed = new Set(filteredTeams.map((t) => t.team_id));
    setSlots((prev) => prev.map((v) => (allowed.has(Number(v)) ? v : "")));
  }, [filteredTeams]);

  const run = async () => {
    const ids = slots.map((s) => Number(s));
    if (ids.some((id) => !id)) return;
    setBusy(true);
    setRunMessage("");
    setProcessedSims(0);
    setTotalSims(hasThirdPlaceDecider ? 256 : 128);
    setEtaSeconds(null);

    const pollPlayoffJob = async (jobId, startedAtMs) => {
      if (!jobId) return;
      if (playoffPollingRef.current) return;
      playoffPollingRef.current = true;
      try {
        let done = false;
        while (!done) {
          const status = await api.get(`/playoff/job/${jobId}`);
          if (status?.detail) {
            setRunMessage(String(status.detail));
            return;
          }
          const processed = Number(status.processed_sims || 0);
          const total = Number(status.total_sims || 0);
          setProcessedSims(processed);
          setTotalSims(total);
          if (processed > 0 && total > processed) {
            const elapsedSec = Math.max(0.001, (Date.now() - startedAtMs) / 1000);
            const rate = processed / elapsedSec;
            if (rate > 0) setEtaSeconds((total - processed) / rate);
          } else if (total > 0 && processed >= total) {
            setEtaSeconds(0);
          }

          if (status.status === "failed") {
            setRunMessage(status.error || "Playoff run failed.");
            return;
          }
          if (status.status === "completed") {
            const data = status.result || null;
            setResults(data);
            setLatestPayload({ team_slots: ids, has_third_place_decider: hasThirdPlaceDecider });
            setUpdatedAt(new Date().toISOString());
            setTopTeams(null);
            setAllTeams(null);
            setBaseTeams(null);
            setTopMessage("");
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 200));
        }
      } finally {
        playoffPollingRef.current = false;
      }
    };

    try {
      const start = await api.post("/playoff/start", {
        team_slots: ids,
        has_third_place_decider: hasThirdPlaceDecider,
      });
      if (start?.detail) {
        setRunMessage(String(start.detail));
        return;
      }
      const jobId = start?.job_id;
      if (!jobId) {
        setRunMessage("Failed to start playoff job.");
        return;
      }
      await pollPlayoffJob(jobId, Date.now());
    } finally {
      setBusy(false);
    }
  };

  const findTopTeams = async () => {
    setBusy(true);
    setTopMessage("");
    setAllTeams(null);
    setBaseTeams(null);
    setPage(0);
    setProcessedCombos(0);
    setTotalCombos(0);
    setTopEtaSeconds(null);
    try {
      const start = await api.post("/playoff/best-team/from-latest/start", {
        include_player_ids: Array.from(effectiveAppliedFilters.include),
        exclude_player_ids: Array.from(effectiveAppliedFilters.exclude),
      });
      if (start?.detail) {
        setTopMessage(String(start.detail));
        setTopTeams([]);
        setAllTeams([]);
        setBaseTeams([]);
        setFilteredCount(0);
        return;
      }
      const jobId = start?.job_id;
      if (!jobId) {
        setTopMessage("Failed to start Top 5 generation job.");
        return;
      }

      let done = false;
      const startedAtMs = Date.now();
      while (!done) {
        const status = await api.get(`/playoff/best-team/job/${jobId}`);
        if (status?.detail) {
          setTopMessage(String(status.detail));
          return;
        }
        const processed = Number(status.processed_combinations || 0);
        const total = Number(status.total_combinations || 0);
        setProcessedCombos(processed);
        setTotalCombos(total);
        if (processed > 0 && total > processed) {
          const elapsedSec = Math.max(0.001, (Date.now() - startedAtMs) / 1000);
          const rate = processed / elapsedSec;
          if (rate > 0) setTopEtaSeconds((total - processed) / rate);
        } else if (total > 0 && processed >= total) {
          setTopEtaSeconds(0);
        }

        if (status.status === "failed") {
          setTopMessage(status.error || "Top 5 generation failed.");
          setTopTeams([]);
          setAllTeams([]);
          setBaseTeams([]);
          setFilteredCount(0);
          return;
        }
        if (status.status === "completed") {
          const data = status.result || {};
          if (data.error) {
            setTopMessage(data.error);
            setTopTeams([]);
            setAllTeams([]);
            setBaseTeams([]);
            setFilteredCount(0);
          } else if (data.top_teams && data.top_teams.length > 0) {
            const all = data.all_teams || [];
            setBaseTeams(all);
            applyTop5ViewFilters(all);
            setTopMessage("");
          } else {
            setTopTeams([]);
            setAllTeams([]);
            setBaseTeams([]);
            setFilteredCount(0);
            setTopMessage("No valid teams found from stored playoff valuations.");
          }
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    } finally {
      setBusy(false);
    }
  };

  const slotLabels = [
    "QF1 - Top Left",
    "QF1 - Bottom Left",
    "QF2 - Top Right",
    "QF2 - Bottom Right",
    "QF3 - Top Left (bottom half)",
    "QF3 - Bottom Left (bottom half)",
    "QF4 - Top Right (bottom half)",
    "QF4 - Bottom Right (bottom half)",
  ];

  const resetStoredPlayoff = async () => {
    await api.delete("/playoff/latest");
    setLatestPayload(null);
    setResults(null);
    setUpdatedAt("");
    setTopTeams(null);
    setAllTeams(null);
    setBaseTeams(null);
    setFilteredCount(0);
    setTopMessage("");
  };

  const modalMatches = useMemo(() => {
    const q = filterSearch.trim().toLowerCase();
    if (!q) return [];
    return players
      .filter((p) => String(p.name || "").toLowerCase().includes(q))
      .slice(0, 25);
  }, [players, filterSearch]);

  useEffect(() => {
    if (!baseTeams) return;
    applyTop5ViewFilters(baseTeams);
  }, [baseTeams, effectiveAppliedFilters, comboSearch, sortKey]);

  const applyTop5ViewFilters = (teamsIn) => {
    const base = Array.isArray(teamsIn) ? teamsIn : [];
    let filtered = applyFilters(base, effectiveAppliedFilters.include, effectiveAppliedFilters.exclude);
    const q = comboSearch.trim().toLowerCase();
    if (q) {
      filtered = filtered.filter((team) => {
        if (String(team.cost ?? "").includes(q) || String(team.total_ev ?? "").includes(q)) return true;
        return (team.players || []).some((p) => {
          const name = String(p.name || "").toLowerCase();
          const teamName = String(teamLookup[p.team_id] || "").toLowerCase();
          return (
            name.includes(q) ||
            teamName.includes(q) ||
            String(p.player_id).includes(q) ||
            String(p.team_id).includes(q)
          );
        });
      });
    }
    const sorted = sortTeams(filtered, sortKey);
    setAllTeams(sorted);
    setTopTeams(sorted.slice(0, 10));
    setFilteredCount(sorted.length);
    setPage(0);
  };
  const playoffPlayerValueData = useMemo(
    () => buildPlayerValueRowsFromSimulation(results, players),
    [results, players]
  );

  const formatEta = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "-";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return `${h}h ${m}m ${r}s`;
    if (m > 0) return `${m}m ${r}s`;
    return `${r}s`;
  };

  return (
    <Section title="Playoff Bracket (BO3)">
      <div className="stack">
        <div className="grid three">
          <Select
            label="Event"
            value={selectedEventId}
            onChange={setSelectedEventId}
            options={
              events.length > 0
                ? events.map((e) => ({ value: String(e.event_id), label: `Event ${e.event_id}` }))
                : [{ value: "", label: "No events imported" }]
            }
          />
          <div className="field">
            <span>Teams In Event</span>
            <div className="pill">{filteredTeams.length}</div>
          </div>
          <div className="field">
            <span>Stored Valuations</span>
            <div className="pill">{results ? "Loaded" : "None"}</div>
          </div>
        </div>

        <div className="tab-bar small">
          <button className={playoffTab === "stage" ? "tab active" : "tab"} onClick={() => setPlayoffTab("stage")}>
            Bracket Stage
          </button>
          <button className={playoffTab === "top5" ? "tab active" : "tab"} onClick={() => setPlayoffTab("top5")}>
            Top 5 Teams
          </button>
          <button className={playoffTab === "value" ? "tab active" : "tab"} onClick={() => setPlayoffTab("value")}>
            Player Value
          </button>
        </div>

        {playoffTab === "stage" && (
          <>
            <div className="grid two">
              {slots.map((val, idx) => (
                <Select
                  key={idx}
                  label={slotLabels[idx]}
                  value={val}
                  onChange={(v) => setSlot(idx, v)}
                  options={[
                    { value: "", label: "Select team" },
                    ...filteredTeams.map((t) => ({ value: t.team_id, label: `${t.name} (${t.team_id})` })),
                  ]}
                />
              ))}
            </div>
            <div className="actions">
              <label className="checkbox-inline">
                <input
                  type="checkbox"
                  checked={hasThirdPlaceDecider}
                  onChange={(e) => setHasThirdPlaceDecider(e.target.checked)}
                  disabled={busy}
                />
                <span>Third-place decider match</span>
              </label>
              <button className="primary" onClick={run} disabled={busy || slots.some((s) => !s)}>
                {busy ? "Running..." : "Run Playoff And Store Valuations"}
              </button>
              <button className="danger" onClick={resetStoredPlayoff} disabled={busy || !results}>
                Reset Stored Valuations
              </button>
              {updatedAt && <p className="muted">Stored: {new Date(updatedAt).toLocaleString()}</p>}
            </div>
          </>
        )}

        {playoffTab === "top5" && (
          <>
            {!results && (
              <div className="card sub">
                <p className="muted">Run Playoff Bracket in the Bracket Stage tab first.</p>
              </div>
            )}
            {results && (
              <>
                <div className="actions">
                  <button className="primary" onClick={findTopTeams} disabled={busy || !results}>
                    {busy ? "Working..." : "Generate & Store Team Combos"}
                  </button>
                </div>
                <div className="card sub">
                  <h3>Top Teams (Filtered)</h3>
                  <div className="top5-filters">
                    <div className="grid two">
                      <div className="field">
                        <span>Included Players</span>
                        <div className="chips">
                          {Array.from(includeSet).length === 0 && <span className="muted">None</span>}
                          {Array.from(includeSet).sort((a, b) => a - b).map((pid) => (
                            <span key={`p-inc-${pid}`} className="chip active">
                              {playerNameById[pid] || `Player ${pid}`}
                              <button
                                className="close"
                                style={{ marginLeft: 8, padding: "2px 6px" }}
                                onClick={() =>
                                  setIncludeSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(pid);
                                    return next;
                                  })
                                }
                              >
                                x
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                      <div className="field">
                        <span>Excluded Players</span>
                        <div className="chips">
                          {Array.from(excludeSet).length === 0 && <span className="muted">None</span>}
                          {Array.from(excludeSet).sort((a, b) => a - b).map((pid) => (
                            <span key={`p-exc-${pid}`} className="chip active">
                              {playerNameById[pid] || `Player ${pid}`}
                              <button
                                className="close"
                                style={{ marginLeft: 8, padding: "2px 6px" }}
                                onClick={() =>
                                  setExcludeSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(pid);
                                    return next;
                                  })
                                }
                              >
                                x
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="grid two">
                      <div className="field">
                        <span>Included Teams</span>
                        <div className="chips">
                          {Array.from(includeTeamSet).length === 0 && <span className="muted">None</span>}
                          {Array.from(includeTeamSet).sort((a, b) => a - b).map((tid) => (
                            <span key={`t-inc-${tid}`} className="chip active">
                              {teamLookup[tid] || `Team ${tid}`}
                              <button
                                className="close"
                                style={{ marginLeft: 8, padding: "2px 6px" }}
                                onClick={() =>
                                  setIncludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(tid);
                                    return next;
                                  })
                                }
                              >
                                x
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                      <div className="field">
                        <span>Excluded Teams</span>
                        <div className="chips">
                          {Array.from(excludeTeamSet).length === 0 && <span className="muted">None</span>}
                          {Array.from(excludeTeamSet).sort((a, b) => a - b).map((tid) => (
                            <span key={`t-exc-${tid}`} className="chip active">
                              {teamLookup[tid] || `Team ${tid}`}
                              <button
                                className="close"
                                style={{ marginLeft: 8, padding: "2px 6px" }}
                                onClick={() =>
                                  setExcludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(tid);
                                    return next;
                                  })
                                }
                              >
                                x
                              </button>
                            </span>
                          ))}
                        </div>
                      </div>
                    </div>
                    <div className="field">
                      <span>Bracket Teams</span>
                      <div className="chips">
                        {playoffTeamsForFilters.length === 0 && <span className="muted">Select playoff slots first.</span>}
                        {playoffTeamsForFilters.map((t) => {
                          const tid = Number(t.team_id);
                          const isIncluded = includeTeamSet.has(tid);
                          const isExcluded = excludeTeamSet.has(tid);
                          return (
                            <span key={`filter-team-${tid}`} className="chip" style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
                              <span>{t.name} ({tid})</span>
                              <button
                                className={isIncluded ? "chip active" : "chip"}
                                style={{ padding: "2px 8px" }}
                                onClick={() => {
                                  setIncludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(tid)) next.delete(tid);
                                    else next.add(tid);
                                    return next;
                                  });
                                  setExcludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(tid);
                                    return next;
                                  });
                                }}
                              >
                                {isIncluded ? "Included" : "Include"}
                              </button>
                              <button
                                className={isExcluded ? "chip active" : "chip"}
                                style={{ padding: "2px 8px" }}
                                onClick={() => {
                                  setExcludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    if (next.has(tid)) next.delete(tid);
                                    else next.add(tid);
                                    return next;
                                  });
                                  setIncludeTeamSet((prev) => {
                                    const next = new Set(prev);
                                    next.delete(tid);
                                    return next;
                                  });
                                }}
                              >
                                {isExcluded ? "Excluded" : "Exclude"}
                              </button>
                            </span>
                          );
                        })}
                      </div>
                    </div>
                    <div className="actions top5-filter-actions">
                      <button className="secondary" onClick={() => setShowFilterModal(true)} disabled={!latestPayload}>
                        Include/Exclude by Name
                      </button>
                      <button
                        className="primary"
                        onClick={() => {
                          setAppliedIncludeSet(new Set(Array.from(includeSet)));
                          setAppliedExcludeSet(new Set(Array.from(excludeSet)));
                          setAppliedIncludeTeamSet(new Set(Array.from(includeTeamSet)));
                          setAppliedExcludeTeamSet(new Set(Array.from(excludeTeamSet)));
                        }}
                        disabled={!latestPayload}
                      >
                        Apply Filters
                      </button>
                    </div>
                    <div className="grid three top5-controls">
                      <Input label="Search Combos" value={comboSearch} onChange={setComboSearch} placeholder="Player/team name or id" />
                      <Select
                        label="Sort by"
                        value={sortKey}
                        onChange={setSortKey}
                        options={[
                          { value: "ev_desc", label: "EV desc" },
                          { value: "ev_asc", label: "EV asc" },
                          { value: "cost_asc", label: "Cost asc" },
                          { value: "cost_desc", label: "Cost desc" },
                          { value: "cpp_desc", label: "Value (EV/Cost) desc" },
                        ]}
                      />
                      <div className="field top5-counter">
                        <span>Filtered / Stored</span>
                        <p className="muted">{filteredCount} / {(baseTeams || []).length}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}
      </div>
      {playoffTab === "stage" && busy && (
        <div className="card sub">
          <p className="muted">
            Calculated outcomes: {processedSims.toLocaleString()} / {totalSims.toLocaleString() || (hasThirdPlaceDecider ? 256 : 128)}
          </p>
          <p className="muted">ETA: {formatEta(etaSeconds)}</p>
          <div className="progress">
            <div
              className="progress-bar determinate"
              style={{ width: `${totalSims > 0 ? Math.min(100, (processedSims / totalSims) * 100) : 0}%` }}
            />
          </div>
        </div>
      )}
      {playoffTab === "stage" && runMessage && (
        <div className="card sub">
          <p className="muted">{runMessage}</p>
        </div>
      )}
      {playoffTab === "stage" && results && (
        <div className="stack">
          <div className="card sub">
            <h3>Player Expected Values</h3>
          </div>
          <div className="grid two">
            {Object.entries(results.teams).map(([tid, data]) => (
              <div key={tid} className="card sub">
                <h4>{teamLookup[Number(tid)] || `Team ${tid}`}</h4>
                <table>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Total</th>
                      <th>Rating</th>
                      <th>Win</th>
                      <th>Role</th>
                      <th>Booster</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.players || {}).map(([pid, comps]) => (
                      <tr key={pid}>
                        <td>
                          <button className="inline-link-btn" onClick={() => onOpenPlayer && onOpenPlayer(Number(pid))}>
                            {playerLookup[Number(pid)] || pid}
                          </button>
                        </td>
                        <td>{comps.total_points.toFixed(2)}</td>
                        <td>{comps.rating_points_total.toFixed(2)}</td>
                        <td>{comps.win_points_total.toFixed(2)}</td>
                        <td>{comps.role_points_total.toFixed(2)}</td>
                        <td>{comps.booster_points_total.toFixed(2)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        </div>
      )}
      {playoffTab === "top5" && topTeams && topTeams.length > 0 && (
        <div className="card sub">
          <h3>Top Teams</h3>
          {topTeams.map((team, idx) => (
            <div key={idx} className="card sub">
              <h4>
                #{idx + 1} EV {team.total_ev.toFixed(2)} | Cost {team.cost}
              </h4>
              <table>
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Team</th>
                    <th>Assigned Role</th>
                    <th>Cost</th>
                    <th>Total EV</th>
                    <th>Rating</th>
                    <th>Win</th>
                    <th>Role</th>
                    <th>Booster</th>
                  </tr>
                </thead>
                <tbody>
                  {team.players.map((p) => (
                    <tr key={p.player_id}>
                      <td>
                        <button className="inline-link-btn" onClick={() => onOpenPlayer && onOpenPlayer(Number(p.player_id))}>
                          {p.name}
                        </button>
                      </td>
                      <td>{teamLookup[p.team_id] || p.team_id}</td>
                      <td>{roleLabel(p.role_name)}</td>
                      <td>{p.price}</td>
                      <td>{p.total_ev.toFixed(2)}</td>
                      <td>{p.rating_ev.toFixed(2)}</td>
                      <td>{p.win_ev.toFixed(2)}</td>
                      <td>{p.role_ev.toFixed(2)}</td>
                      <td>{p.booster_ev.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
      {playoffTab === "top5" && allTeams && allTeams.length > 0 && (
        <div className="card sub">
          <h3>All Filtered Teams ({filteredCount})</h3>
          <div className="actions">
            <button className="secondary" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
              Prev 200
            </button>
            <button
              className="secondary"
              onClick={() => setPage((p) => ((p + 1) * 200 < allTeams.length ? p + 1 : p))}
              disabled={(page + 1) * 200 >= allTeams.length}
            >
              Next 200
            </button>
            <p className="muted">
              Page {page + 1} showing {Math.min(200, Math.max(0, filteredCount - page * 200))} of {filteredCount}
            </p>
          </div>
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>EV</th>
                <th>Cost</th>
                <th>Players</th>
              </tr>
            </thead>
            <tbody>
              {allTeams.slice(page * 200, page * 200 + 200).map((team, idx) => (
                <tr key={idx + page * 200}>
                  <td>{idx + 1 + page * 200}</td>
                  <td>{team.total_ev.toFixed(2)}</td>
                  <td>{team.cost}</td>
                  <td>{team.players.map((p) => `${p.name} (${teamLookup[p.team_id] || p.team_id}, ${roleLabel(p.role_name)})`).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {playoffTab === "top5" && busy && totalCombos > 0 && (
        <div className="card sub">
          <p className="muted">
            Processing combinations: {processedCombos.toLocaleString()} / {totalCombos.toLocaleString()}
          </p>
          <p className="muted">ETA: {formatEta(topEtaSeconds)}</p>
          <div className="progress">
            <div
              className="progress-bar determinate"
              style={{ width: `${totalCombos > 0 ? Math.min(100, (processedCombos / totalCombos) * 100) : 0}%` }}
            />
          </div>
        </div>
      )}
      {playoffTab === "top5" && baseTeams && filteredCount === 0 && (
        <div className="card sub">
          <p className="muted">No team combinations match current include/exclude/search filters.</p>
        </div>
      )}
      {playoffTab === "value" && results && (
        <PriceVsPointsPanel
          title="Player Price vs Points (Playoff)"
          rows={playoffPlayerValueData.rows}
          slope={playoffPlayerValueData.slope}
          intercept={playoffPlayerValueData.intercept}
        />
      )}
      {playoffTab === "value" && !results && (
        <div className="card sub">
          <p className="muted">Run Playoff Bracket first.</p>
        </div>
      )}
      {playoffTab === "top5" && topMessage && (
        <div className="card sub">
          <p className="muted">{topMessage}</p>
        </div>
      )}

      {showFilterModal && (
        <div className="modal-backdrop" onClick={() => setShowFilterModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>Select Players to Include/Exclude</h3>
              <button className="close" onClick={() => setShowFilterModal(false)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <Input
                label="Search player name"
                value={filterSearch}
                onChange={setFilterSearch}
                placeholder="Type e.g. zy to find ZywOo"
              />
              {filterSearch.trim().length === 0 && <p className="muted">Start typing a player name.</p>}
              {filterSearch.trim().length > 0 && modalMatches.length === 0 && <p className="muted">No matching players.</p>}
              {modalMatches.length > 0 && (
                <table>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>ID</th>
                      <th>Include</th>
                      <th>Exclude</th>
                    </tr>
                  </thead>
                  <tbody>
                    {modalMatches.map((p) => {
                      const pid = Number(p.player_id);
                      const isIncluded = includeSet.has(pid);
                      const isExcluded = excludeSet.has(pid);
                      return (
                        <tr key={pid}>
                          <td>{p.name}</td>
                          <td>{pid}</td>
                          <td>
                            <button
                              className={isIncluded ? "chip active" : "chip"}
                              onClick={() => {
                                setIncludeSet((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(pid)) next.delete(pid);
                                  else next.add(pid);
                                  return next;
                                });
                                setExcludeSet((prev) => {
                                  const next = new Set(prev);
                                  next.delete(pid);
                                  return next;
                                });
                              }}
                            >
                              {isIncluded ? "Included" : "Include"}
                            </button>
                          </td>
                          <td>
                            <button
                              className={isExcluded ? "chip active" : "chip"}
                              onClick={() => {
                                setExcludeSet((prev) => {
                                  const next = new Set(prev);
                                  if (next.has(pid)) next.delete(pid);
                                  else next.add(pid);
                                  return next;
                                });
                                setIncludeSet((prev) => {
                                  const next = new Set(prev);
                                  next.delete(pid);
                                  return next;
                                });
                              }}
                            >
                              {isExcluded ? "Excluded" : "Exclude"}
                            </button>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
            <div className="actions">
              <button className="primary" onClick={() => setShowFilterModal(false)}>
                Done
              </button>
            </div>
          </div>
        </div>
      )}
    </Section>
  );
}

function EventsTab({ refreshData, notify, players }) {
  const [eventId, setEventId] = useState("");
  const [events, setEvents] = useState([]);
  const [activeEventId, setActiveEventId] = useState(null);
  const [selectedEventId, setSelectedEventId] = useState(null);
  const [selectedEvent, setSelectedEvent] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const playerById = useMemo(() => {
    const out = {};
    (players || []).forEach((p) => {
      out[p.player_id] = p;
    });
    return out;
  }, [players]);

  const hasJsonEntries = (raw) => {
    if (!raw) return false;
    try {
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (Array.isArray(parsed)) return parsed.length > 0;
      if (parsed && typeof parsed === "object") return Object.keys(parsed).length > 0;
      return false;
    } catch {
      return false;
    }
  };

  const isPlayerComplete = (playerId) => {
    const p = playerById[Number(playerId)];
    if (!p) return false;
    const hasCore = typeof p.name === "string" && p.name.trim().length > 0 && Number.isFinite(Number(p.rating));
    const hasRolesAndBoosters = hasJsonEntries(p.boosters_json) && hasJsonEntries(p.roles_json);
    return hasCore && hasRolesAndBoosters;
  };

  const loadEvents = async () => {
    const res = await api.get("/events/");
    if (res?.detail) {
      setMessage(String(res.detail));
      return;
    }
    setEvents(res.events || []);
    setActiveEventId(res.active_event_id ?? null);
    if (!selectedEventId && Array.isArray(res.events) && res.events.length > 0) {
      setSelectedEventId(res.events[0].event_id);
    }
  };

  const loadEventDetail = async (targetEventId) => {
    if (!targetEventId) {
      setSelectedEvent(null);
      return;
    }
    const detail = await api.get(`/events/${targetEventId}`);
    if (detail?.detail) {
      setMessage(String(detail.detail));
      return;
    }
    setSelectedEvent(detail);
  };

  useEffect(() => {
    loadEvents();
  }, []);

  useEffect(() => {
    if (!selectedEventId) return;
    loadEventDetail(selectedEventId);
  }, [selectedEventId]);

  const importEvent = async () => {
    if (!eventId.trim()) {
      setMessage("Enter an event id first.");
      return;
    }
    setBusy(true);
    setMessage("");
    try {
      const res = await api.post("/events/import-hltv-event", { event_id: eventId.trim() });
      if (res?.detail) {
        setMessage(String(res.detail));
        return;
      }
      setActiveEventId(res.active_event_id ?? Number(eventId.trim()));
      setSelectedEventId(Number(res.event_id));
      setEventId("");
      setMessage(`Imported event ${res.event_id}: players ${res.imported_players ?? 0}, teams ${res.imported_teams ?? 0}.`);
      notify("Event imported");
      await loadEvents();
      await loadEventDetail(Number(res.event_id));
      await refreshData();
    } finally {
      setBusy(false);
    }
  };

  const refreshAll = async () => {
    setBusy(true);
    setMessage("");
    try {
      await loadEvents();
      if (selectedEventId) {
        await loadEventDetail(selectedEventId);
      }
      await refreshData();
      notify("Refreshed events and player data");
    } finally {
      setBusy(false);
    }
  };

  const activateEvent = async (targetEventId) => {
    setBusy(true);
    setMessage("");
    try {
      const res = await api.post("/events/activate", { event_id: Number(targetEventId) });
      if (res?.detail) {
        setMessage(String(res.detail));
        return;
      }
      setActiveEventId(res.active_event_id);
      notify(`Active event set to ${res.active_event_id}`);
      await refreshData();
    } finally {
      setBusy(false);
    }
  };

  const groupedPlayers = useMemo(() => {
    const byTeam = {};
    (selectedEvent?.players || []).forEach((p) => {
      const teamName = p.team_name || "Unknown Team";
      if (!byTeam[teamName]) byTeam[teamName] = [];
      byTeam[teamName].push(p);
    });
    return byTeam;
  }, [selectedEvent]);

  return (
    <div className="stack">
      <Section title="Events">
        <div className="stack">
          <div className="grid three">
            <Input label="Event ID" value={eventId} onChange={setEventId} placeholder="e.g. 12345" />
            <div className="field">
              <span>Import</span>
              <button className="primary" onClick={importEvent} disabled={busy}>
                {busy ? "Working..." : "Import Event"}
              </button>
            </div>
            <div className="field">
              <span>Active Event</span>
              <div className="pill">{activeEventId ? `Event ${activeEventId}` : "None"}</div>
            </div>
          </div>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="secondary" onClick={refreshAll} disabled={busy}>
              {busy ? "Working..." : "Refresh All"}
            </button>
          </div>

          {events.length > 0 && (
            <div className="card sub">
              <h4>Imported Events</h4>
              <table>
                <thead>
                  <tr>
                    <th>Event</th>
                    <th>Imported</th>
                    <th>Teams</th>
                    <th>Players</th>
                    <th>Price Range</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev) => (
                    <tr key={ev.event_id} className={selectedEventId === ev.event_id ? "row-active" : ""}>
                      <td>{ev.event_id}</td>
                      <td>{ev.imported_at ? new Date(ev.imported_at).toLocaleString() : "-"}</td>
                      <td>{ev.team_count ?? 0}</td>
                      <td>{ev.player_count ?? 0}</td>
                      <td>
                        {ev.min_price ?? "-"} - {ev.max_price ?? "-"}
                      </td>
                      <td>
                        <div className="actions" style={{ marginTop: 0 }}>
                          <button
                            className="secondary"
                            onClick={() => setSelectedEventId(ev.event_id)}
                            disabled={busy}
                          >
                            View
                          </button>
                          <button
                            className={activeEventId === ev.event_id ? "primary" : "secondary"}
                            onClick={() => activateEvent(ev.event_id)}
                            disabled={busy || activeEventId === ev.event_id}
                          >
                            {activeEventId === ev.event_id ? "Active" : "Set Active"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {selectedEvent && (
            <div className="card sub">
              <h4>Event {selectedEvent.event_id} Teams & Prices</h4>
              <div className="grid two">
                {Object.entries(groupedPlayers).map(([teamName, teamPlayers]) => (
                  <div key={teamName} className="card sub">
                    <h4>{teamName}</h4>
                    <table>
                      <thead>
                        <tr>
                          <th>Status</th>
                          <th>Player</th>
                          <th>ID</th>
                          <th>Price</th>
                        </tr>
                      </thead>
                      <tbody>
                        {teamPlayers.map((p) => {
                          const complete = isPlayerComplete(p.player_id);
                          return (
                          <tr key={`${teamName}-${p.player_id}`}>
                            <td className="status-cell">
                              <span
                                className={`status-dot ${complete ? "ok" : "missing"}`}
                                title={complete ? "All player data present" : "Missing player data"}
                              />
                            </td>
                            <td>{p.player_name || `Player ${p.player_id}`}</td>
                            <td>{p.player_id}</td>
                            <td>{p.price}</td>
                          </tr>
                        )})}
                      </tbody>
                    </table>
                  </div>
                ))}
              </div>
            </div>
          )}

          {message && <p className="muted">{message}</p>}
        </div>
      </Section>
    </div>
  );
}

function MatchesDataPanel({ notify }) {
  const [recentResults, setRecentResults] = useState([]);
  const [recentResultsLoading, setRecentResultsLoading] = useState(false);
  const [recentResultsError, setRecentResultsError] = useState("");
  const [recentResultsPages, setRecentResultsPages] = useState("3");
  const [recentResultsOffset, setRecentResultsOffset] = useState(0);
  const [selectedMatchUrl, setSelectedMatchUrl] = useState("");
  const [selectedMatchRow, setSelectedMatchRow] = useState(null);
  const [showMatchModal, setShowMatchModal] = useState(false);
  const initialLoadRef = useRef(false);

  const loadStoredRecentResults = async (offset = 0) => {
    const safeOffset = Math.max(0, Number(offset) || 0);
    setRecentResultsLoading(true);
    setRecentResultsError("");
    try {
      const res = await api.get(`/events/hltv-results?limit=100&offset=${safeOffset}`);
      if (res?.detail) {
        setRecentResults([]);
        setRecentResultsError(String(res.detail));
      } else {
        setRecentResults(Array.isArray(res?.results) ? res.results : []);
        setRecentResultsOffset(safeOffset);
      }
    } catch (e) {
      setRecentResults([]);
      setRecentResultsError("Failed to load stored HLTV results.");
    } finally {
      setRecentResultsLoading(false);
    }
  };

  const importRecentResultsToDb = async () => {
    const pages = Math.max(1, Math.min(30, Number(recentResultsPages) || 1));
    setRecentResultsLoading(true);
    setRecentResultsError("");
    try {
      const res = await api.post("/events/hltv-results/import", {
        pages,
        start_offset: 0,
        page_stride: 100,
        per_page_limit: 100,
      });
      if (res?.detail) {
        setRecentResultsError(String(res.detail));
        return;
      }
      if (notify) {
        notify(
          `Imported HLTV results: ${res.fetched || 0} fetched, ${res.inserted || 0} inserted, ${res.updated || 0} updated, ${res.enriched_matches || 0} enriched`
        );
      }
      await loadStoredRecentResults(0);
    } catch (e) {
      setRecentResultsError("Failed to import/store HLTV results.");
    } finally {
      setRecentResultsLoading(false);
    }
  };

  const clearStoredResults = async () => {
    setRecentResultsLoading(true);
    setRecentResultsError("");
    try {
      const res = await api.delete("/events/hltv-results");
      if (res?.detail) {
        setRecentResultsError(String(res.detail));
        return;
      }
      setRecentResults([]);
      setRecentResultsOffset(0);
      setSelectedMatchUrl("");
      setSelectedMatchRow(null);
      setShowMatchModal(false);
      if (notify) notify(`Deleted ${Number(res?.deleted || 0)} stored matches`);
    } catch (e) {
      setRecentResultsError("Failed to delete stored HLTV results.");
    } finally {
      setRecentResultsLoading(false);
    }
  };

  const openMatchModal = (row) => {
    const url = String(row?.match_url || "").trim();
    if (!url) return;
    setSelectedMatchUrl(url);
    setSelectedMatchRow(row || null);
    setShowMatchModal(true);
  };

  useEffect(() => {
    if (initialLoadRef.current) return;
    initialLoadRef.current = true;
    loadStoredRecentResults(0);
  }, []);

  return (
    <div className="stack">
      <div className="grid three">
        <Input
          label="# Pages To Import (100 each)"
          value={recentResultsPages}
          onChange={setRecentResultsPages}
          placeholder="e.g. 3"
        />
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="primary" onClick={importRecentResultsToDb} disabled={recentResultsLoading}>
          {recentResultsLoading ? "Importing..." : "Import HLTV Results To SQL"}
        </button>
        <button className="danger" onClick={clearStoredResults} disabled={recentResultsLoading}>
          {recentResultsLoading ? "Deleting..." : "Delete All Stored Matches"}
        </button>
        <button className="secondary" onClick={loadStoredRecentResults} disabled={recentResultsLoading}>
          {recentResultsLoading ? "Loading..." : "Reload Stored Results"}
        </button>
        <span className="muted">{recentResults.length} loaded</span>
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(Math.max(0, recentResultsOffset - 100))}
          disabled={recentResultsLoading || recentResultsOffset <= 0}
        >
          Prev 100
        </button>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(recentResultsOffset + 100)}
          disabled={recentResultsLoading || recentResults.length < 100}
        >
          Next 100
        </button>
        <span className="muted">Offset: {recentResultsOffset}</span>
      </div>
      {recentResultsError && <p className="error">{recentResultsError}</p>}
      {!recentResultsLoading && !recentResultsError && recentResults.length === 0 && <p className="muted">No results loaded yet.</p>}
      {recentResults.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>Team A</th>
              <th>Score</th>
              <th>Team B</th>
              <th>Winner</th>
              <th>Date Played</th>
              <th>Match</th>
            </tr>
          </thead>
          <tbody>
            {recentResults.map((r, idx) => {
              const s1 = Number(r?.score1);
              const s2 = Number(r?.score2);
              const scoreText = Number.isFinite(s1) && Number.isFinite(s2) ? `${s1} - ${s2}` : "-";
              return (
                <tr
                  key={`hltv-res-${idx}-${r?.match_url || ""}`}
                  className={String(r?.match_url || "") === selectedMatchUrl ? "row-active" : ""}
                  onClick={() => openMatchModal(r)}
                >
                  <td>{idx + 1}</td>
                  <td>{r?.team1 || "-"}</td>
                  <td>{scoreText}</td>
                  <td>{r?.team2 || "-"}</td>
                  <td>{r?.winner || "-"}</td>
                  <td>{r?.match_date || "-"}</td>
                  <td>
                    {r?.match_url ? (
                      <a href={String(r.match_url)} target="_blank" rel="noreferrer">
                        Open
                      </a>
                    ) : (
                      "-"
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      {showMatchModal && selectedMatchRow && (
        <div className="modal-backdrop" onClick={() => setShowMatchModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3 className="player-modal-title">
                {selectedMatchRow?.team1 || "-"} vs {selectedMatchRow?.team2 || "-"}
              </h3>
              <button className="close" onClick={() => setShowMatchModal(false)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <p className="muted">
                Date played: {selectedMatchRow?.match_date || "-"} | Final score:{" "}
                {Number.isFinite(Number(selectedMatchRow?.score1)) && Number.isFinite(Number(selectedMatchRow?.score2))
                  ? `${Number(selectedMatchRow.score1)} - ${Number(selectedMatchRow.score2)}`
                  : "-"}
              </p>
              <table>
                <thead>
                  <tr>
                    <th>Team</th>
                    <th>HLTV Rank</th>
                    <th>HLTV Points</th>
                    <th>VRS Rank</th>
                    <th>VRS Points</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>{selectedMatchRow?.team1 || "-"}</td>
                    <td>{selectedMatchRow?.hltv_rank_1 ?? "-"}</td>
                    <td>{selectedMatchRow?.hltv_points_1 ?? "-"}</td>
                    <td>{selectedMatchRow?.vrs_rank_1 ?? "-"}</td>
                    <td>{selectedMatchRow?.vrs_points_1 ?? "-"}</td>
                  </tr>
                  <tr>
                    <td>{selectedMatchRow?.team2 || "-"}</td>
                    <td>{selectedMatchRow?.hltv_rank_2 ?? "-"}</td>
                    <td>{selectedMatchRow?.hltv_points_2 ?? "-"}</td>
                    <td>{selectedMatchRow?.vrs_rank_2 ?? "-"}</td>
                    <td>{selectedMatchRow?.vrs_points_2 ?? "-"}</td>
                  </tr>
                </tbody>
              </table>
              <h4>Maps Played</h4>
              {(() => {
                let maps = [];
                try {
                  const parsed = JSON.parse(String(selectedMatchRow?.maps_json || "[]"));
                  if (Array.isArray(parsed)) maps = parsed;
                } catch {}
                if (maps.length === 0) return <p className="muted">No map details stored for this match.</p>;
                return (
                  <table>
                    <thead>
                      <tr>
                        <th>Map</th>
                        <th>{selectedMatchRow?.team1 || "Team 1"}</th>
                        <th>{selectedMatchRow?.team2 || "Team 2"}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {maps.map((m, i) => (
                        <tr key={`map-${i}-${m?.map || ""}`}>
                          <td>{m?.map || "-"}</td>
                          <td>{m?.score1 ?? "-"}</td>
                          <td>{m?.score2 ?? "-"}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                );
              })()}
            </div>
            <div className="actions">
              <button className="secondary" onClick={() => setShowMatchModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function DatabaseTab({ players, teams, loading, error, refresh, notify, openPlayerId, onOpenPlayerHandled }) {
  const [dbTab, setDbTab] = useState("players");
  const [playerSearch, setPlayerSearch] = useState("");
  const [playerSort, setPlayerSort] = useState("name_asc");
  const [teamSearch, setTeamSearch] = useState("");
  const [teamSort, setTeamSort] = useState("name_asc");
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [showPlayerModal, setShowPlayerModal] = useState(false);
  const [playerTab, setPlayerTab] = useState("info");
  const [playerForm, setPlayerForm] = useState({
    player_id: "",
    name: "",
    rating: "",
    last_topx_import_at: "",
    rating_top5: "",
    maps_top5: "",
    rating_top10: "",
    maps_top10: "",
    rating_top20: "",
    maps_top20: "",
    rating_top30: "",
    maps_top30: "",
    rating_top50: "",
    maps_top50: "",
  });
  const [topRatingsBusy, setTopRatingsBusy] = useState(false);
  const [playerTopxFeedback, setPlayerTopxFeedback] = useState(null);
  const [batchTopRatingsBusy, setBatchTopRatingsBusy] = useState(false);
  const [batchTopRatingsStatus, setBatchTopRatingsStatus] = useState("idle");
  const [batchTopRatingsJobId, setBatchTopRatingsJobId] = useState("");
  const [batchTopRatingsProcessed, setBatchTopRatingsProcessed] = useState(0);
  const [batchTopRatingsTotal, setBatchTopRatingsTotal] = useState(0);
  const [batchTopRatingsOk, setBatchTopRatingsOk] = useState(0);
  const [batchTopRatingsFailed, setBatchTopRatingsFailed] = useState(0);
  const [batchTopRatingsLastError, setBatchTopRatingsLastError] = useState("");
  const [batchTopRatingsEtaSeconds, setBatchTopRatingsEtaSeconds] = useState(null);
  const [playerCurve, setPlayerCurve] = useState(null);
  const [playerCurveLoading, setPlayerCurveLoading] = useState(false);
  const [playerCurveError, setPlayerCurveError] = useState("");
  const batchTopRatingsPollingRef = useRef(false);

  const [selectedTeam, setSelectedTeam] = useState(null);
  const [showTeamModal, setShowTeamModal] = useState(false);
  const [rankingsRefreshBusy, setRankingsRefreshBusy] = useState(false);
  const [teamForm, setTeamForm] = useState({
    team_id: "",
    name: "",
    hltv_rank: "",
    hltv_points: "",
    vrs_rank: "",
    vrs_points: "",
    win_rate: "",
    p1: "",
    p2: "",
    p3: "",
    p4: "",
    p5: "",
  });
  const playerLookup = useMemo(() => {
    const m = {};
    players.forEach((p) => (m[p.player_id] = p.name));
    return m;
  }, [players]);
  const playerTeamLookup = useMemo(() => {
    const m = {};
    teams.forEach((t) => {
      const ids = [t.player1_id, t.player2_id, t.player3_id, t.player4_id, t.player5_id].filter(Boolean);
      ids.forEach((pid) => {
        if (!m[pid]) m[pid] = [];
        m[pid].push(t.name || `Team ${t.team_id}`);
      });
    });
    return m;
  }, [teams]);
  const playerTeamLinks = useMemo(() => {
    const m = {};
    teams.forEach((t) => {
      const ids = [t.player1_id, t.player2_id, t.player3_id, t.player4_id, t.player5_id].filter(Boolean);
      ids.forEach((pid) => {
        if (!m[pid]) m[pid] = [];
        m[pid].push({ team_id: t.team_id, team_name: t.name || `Team ${t.team_id}` });
      });
    });
    return m;
  }, [teams]);

  const boosterNames = {
    0: "Best Pistol Round",
    1: "Bottom of scoreboard",
    2: "Clutch",
    3: "Top of scoreboard",
    4: "Avenger",
    5: "Bait",
    6: "Rambo",
    7: "Flash",
    8: "Mister consistent",
    9: "Kobe",
    10: "Saver",
    11: "Assist",
    12: "Aim bot",
    13: "Quad",
    14: "Carry",
    15: "Cannon fodder",
    16: "Farmer",
    17: "Hellcase",
  };

  const roleNames = {
    0: "Main AWP",
    1: "Support",
    2: "Attacker",
    3: "Leader",
    4: "Stathunter",
    5: "Entry Fragger",
    6: "Camper",
    7: "Defender",
    8: "HS Machine",
    9: "Noob",
    10: "Multi Fragger",
    11: "Eco Friendly",
  };

  const [boosterForm, setBoosterForm] = useState({});
  const [roleForm, setRoleForm] = useState({});
  const boosterAverages = useMemo(() => {
    const sums = {};
    const counts = {};
    for (const p of players || []) {
      let parsed = {};
      try {
        parsed = p.boosters_json ? JSON.parse(p.boosters_json) : {};
      } catch {
        parsed = {};
      }
      for (const key of Object.keys(boosterNames)) {
        const v = Number(parsed?.[key]);
        if (!Number.isFinite(v)) continue;
        sums[key] = (sums[key] || 0) + v;
        counts[key] = (counts[key] || 0) + 1;
      }
    }
    const avg = {};
    for (const key of Object.keys(boosterNames)) {
      if (counts[key] > 0) avg[key] = sums[key] / counts[key];
    }
    return avg;
  }, [players]);

  useEffect(() => {
    if (selectedPlayer) {
      const p = players.find((x) => x.player_id === selectedPlayer);
      if (p) {
        setTopRatingsBusy(false);
        setPlayerTopxFeedback(null);
        let boostersObj = {};
        let rolesObj = {};
        try {
          boostersObj = p.boosters_json ? JSON.parse(p.boosters_json) : {};
        } catch {}
        try {
          rolesObj = p.roles_json ? JSON.parse(p.roles_json) : {};
        } catch {}

        setPlayerForm({
          player_id: p.player_id,
          name: p.name || "",
          rating: p.rating ?? "",
          last_topx_import_at: p.last_topx_import_at ?? "",
          rating_top5: p.rating_top5 ?? "",
          maps_top5: p.maps_top5 ?? "",
          rating_top10: p.rating_top10 ?? "",
          maps_top10: p.maps_top10 ?? "",
          rating_top20: p.rating_top20 ?? "",
          maps_top20: p.maps_top20 ?? "",
          rating_top30: p.rating_top30 ?? "",
          maps_top30: p.maps_top30 ?? "",
          rating_top50: p.rating_top50 ?? "",
          maps_top50: p.maps_top50 ?? "",
        });
        setBoosterForm(boostersObj || {});
        setRoleForm(rolesObj || {});
      }
    }
  }, [selectedPlayer, players]);

  useEffect(() => {
    if (!openPlayerId) return;
    const pid = Number(openPlayerId);
    if (!Number.isFinite(pid) || pid <= 0) {
      if (onOpenPlayerHandled) onOpenPlayerHandled();
      return;
    }
    setDbTab("players");
    setSelectedPlayer(pid);
    setShowPlayerModal(true);
    setPlayerTab("info");
    if (onOpenPlayerHandled) onOpenPlayerHandled();
  }, [openPlayerId, onOpenPlayerHandled]);

  useEffect(() => {
    const pid = Number(selectedPlayer);
    if (playerTab !== "topxGraph" || !showPlayerModal || !Number.isFinite(pid) || pid <= 0) {
      if (!showPlayerModal || playerTab !== "topxGraph") {
        setPlayerCurveError("");
      }
      return;
    }
    let cancelled = false;
    const run = async () => {
      setPlayerCurveLoading(true);
      setPlayerCurveError("");
      try {
        const res = await api.get(`/players/${pid}/rating-curve`);
        if (!cancelled) setPlayerCurve(res || null);
      } catch (e) {
        if (!cancelled) {
          setPlayerCurve(null);
          setPlayerCurveError("Failed to load Top-X graph.");
        }
      } finally {
        if (!cancelled) setPlayerCurveLoading(false);
      }
    };
    run();
    return () => {
      cancelled = true;
    };
  }, [playerTab, selectedPlayer, showPlayerModal, playerForm.last_topx_import_at]);

  useEffect(() => {
    if (selectedTeam) {
      const t = teams.find((x) => x.team_id === selectedTeam);
      if (t) {
        setTeamForm({
          team_id: t.team_id,
          name: t.name || "",
          hltv_rank: t.hltv_rank || "",
          hltv_points: t.hltv_points || "",
          vrs_rank: t.vrs_rank || "",
          vrs_points: t.vrs_points || "",
          win_rate: t.win_rate || "",
          p1: t.player1_id ? String(t.player1_id) : "",
          p2: t.player2_id ? String(t.player2_id) : "",
          p3: t.player3_id ? String(t.player3_id) : "",
          p4: t.player4_id ? String(t.player4_id) : "",
          p5: t.player5_id ? String(t.player5_id) : "",
        });
      }
    }
  }, [selectedTeam, teams]);

  const saveTeam = async () => {
    if (!teamForm.name) return;
    const ids = [teamForm.p1, teamForm.p2, teamForm.p3, teamForm.p4, teamForm.p5].map((x) => Number(x || 0));
    await api.post("/teams/", {
      name: teamForm.name,
      hltv_rank: teamForm.hltv_rank === "" ? undefined : Number(teamForm.hltv_rank),
      hltv_points: teamForm.hltv_points === "" ? undefined : Number(teamForm.hltv_points),
      vrs_rank: teamForm.vrs_rank === "" ? undefined : Number(teamForm.vrs_rank),
      vrs_points: teamForm.vrs_points === "" ? undefined : Number(teamForm.vrs_points),
      win_rate: teamForm.win_rate === "" ? undefined : Number(teamForm.win_rate),
      player_ids: ids,
    });
    notify("Team saved");
    refresh();
    setShowTeamModal(false);
  };

  const deleteTeam = async () => {
    if (!selectedTeam) return;
    await api.delete(`/teams/${selectedTeam}`);
    notify("Team deleted");
    setSelectedTeam(null);
    setTeamForm({
      team_id: "",
      name: "",
      hltv_rank: "",
      hltv_points: "",
      vrs_rank: "",
      vrs_points: "",
      win_rate: "",
      p1: "",
      p2: "",
      p3: "",
      p4: "",
      p5: "",
    });
    refresh();
    setShowTeamModal(false);
  };

  const newTeam = () => {
    setSelectedTeam(null);
    setTeamForm({
      team_id: "",
      name: "",
      hltv_rank: "",
      hltv_points: "",
      vrs_rank: "",
      vrs_points: "",
      win_rate: "",
      p1: "",
      p2: "",
      p3: "",
      p4: "",
      p5: "",
    });
    setShowTeamModal(true);
  };

  const fetchPlayerTopRatings = async () => {
    const playerId = Number(playerForm.player_id);
    if (!Number.isFinite(playerId) || playerId <= 0) {
      setPlayerTopxFeedback({ kind: "error", message: "Player is missing a valid HLTV id." });
      notify("Player is missing a valid HLTV id.");
      return;
    }
    setTopRatingsBusy(true);
    setPlayerTopxFeedback({ kind: "info", message: "Importing Top-X data from HLTV..." });
    try {
      const res = await api.post(`/players/${playerId}/fetch-top-ratings`, {});
      await refresh();
      const rangeText = res?.startDate && res?.endDate ? ` (${res.startDate} to ${res.endDate})` : "";
      setPlayerTopxFeedback({
        kind: "success",
        message: `Top-X import succeeded${rangeText}.`,
      });
      notify(`Top-X imported for ${playerForm.name || `player ${playerId}`}${rangeText}`);
    } catch (e) {
      setPlayerTopxFeedback({
        kind: "error",
        message: `Import failed: ${e?.message || "unknown error"}`,
      });
      notify(`Top-X import failed: ${e?.message || "unknown error"}`);
    } finally {
      setTopRatingsBusy(false);
    }
  };

  const formatBatchEta = (seconds) => {
    if (!Number.isFinite(seconds) || seconds == null || seconds < 0) return "Calculating...";
    if (seconds <= 1) return "<1s";
    if (seconds < 60) return `${Math.round(seconds)}s`;
    const minutes = Math.floor(seconds / 60);
    const rem = Math.round(seconds % 60);
    return `${minutes}m ${rem}s`;
  };

  const getBatchStartedAtMs = (status, fallback = Date.now()) => {
    const startedAt = Number(status?.started_at || status?.created_at || 0);
    return Number.isFinite(startedAt) && startedAt > 0 ? startedAt * 1000 : fallback;
  };

  const applyBatchTopRatingsStatus = (status, jobIdOverride = "") => {
    const jobId = String(jobIdOverride || status?.job_id || "");
    const processed = Number(status?.processed_players || 0);
    const total = Number(status?.total_players || 0);
    const ok = Number(status?.ok || 0);
    const failed = Number(status?.failed || 0);
    const nextStatus = String(status?.status || "queued");
    const lastError = String(status?.last_error || status?.error || "");

    setBatchTopRatingsStatus(nextStatus);
    setBatchTopRatingsJobId(jobId);
    setBatchTopRatingsProcessed(processed);
    setBatchTopRatingsTotal(total);
    setBatchTopRatingsOk(ok);
    setBatchTopRatingsFailed(failed);
    setBatchTopRatingsLastError(lastError);
    setBatchTopRatingsBusy(["queued", "running", "pausing"].includes(nextStatus));

    if (processed > 0 && total > processed) {
      const elapsedSec = Math.max(0.001, (Date.now() - getBatchStartedAtMs(status)) / 1000);
      const rate = processed / elapsedSec;
      setBatchTopRatingsEtaSeconds(rate > 0 ? (total - processed) / rate : null);
    } else if (total > 0 && processed >= total) {
      setBatchTopRatingsEtaSeconds(0);
    } else {
      setBatchTopRatingsEtaSeconds(null);
    }

    return { jobId, processed, total, ok, failed, nextStatus, lastError };
  };

  const pollBatchTopRatingsJob = async (jobId) => {
    if (!jobId || batchTopRatingsPollingRef.current) return;
    batchTopRatingsPollingRef.current = true;
    try {
      let done = false;
      while (!done) {
        const status = await api.get(`/players/fetch-top-ratings-batch/job/${jobId}`);
        const { ok, failed, nextStatus, lastError } = applyBatchTopRatingsStatus(status, jobId);

        if (nextStatus === "failed") {
          setBatchTopRatingsBusy(false);
          notify(lastError || "Top-X batch failed.");
          done = true;
          break;
        }
        if (nextStatus === "paused") {
          setBatchTopRatingsBusy(false);
          done = true;
          break;
        }
        if (nextStatus === "completed") {
          setBatchTopRatingsBusy(false);
          setBatchTopRatingsStatus("idle");
          setBatchTopRatingsEtaSeconds(null);
          await refresh();
          const rows = Array.isArray(status?.result?.results) ? status.result.results : Array.isArray(status?.results) ? status.results : [];
          const failedRows = rows.filter((row) => row?.status !== "ok");
          const failedPreview = failedRows
            .slice(0, 3)
            .map((row) => row?.player_name || `player ${row?.player_id}`)
            .join(", ");
          const failedSuffix = failedPreview ? ` | Failed: ${failedPreview}${failedRows.length > 3 ? "..." : ""}` : "";
          notify(`Top-X batch finished: ok ${ok} failed ${failed}${failedSuffix}`);
          done = true;
          break;
        }

        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (e) {
      setBatchTopRatingsBusy(false);
      setBatchTopRatingsStatus("failed");
      setBatchTopRatingsLastError(String(e?.message || "Failed to poll Top-X batch status."));
      notify(`Top-X batch failed: ${e?.message || "unknown error"}`);
    } finally {
      batchTopRatingsPollingRef.current = false;
    }
  };

  const playerHasCompleteTopRatings = (player) => {
    if (!Number(player?.last_topx_import_at)) return false;
    return TOP_RATING_TIERS.every((tier) => {
      const rating = Number(player?.[`rating_top${tier}`]);
      const maps = Number(player?.[`maps_top${tier}`]);
      return Number.isFinite(rating) && rating > 0 && Number.isFinite(maps) && maps > 0;
    });
  };

  const getTopRatingsBatchPlayerIds = (onlyMissing = false) =>
    (players || [])
      .filter((player) => !onlyMissing || !playerHasCompleteTopRatings(player))
      .map((player) => Number(player?.player_id))
      .filter((value) => Number.isFinite(value) && value > 0);

  const importPlayerTopRatingsBatch = async (onlyMissing = false) => {
    const playerIds = getTopRatingsBatchPlayerIds(onlyMissing);
    if (playerIds.length === 0) {
      notify(onlyMissing ? "No players are missing Top-X data." : "No players available to import.");
      return;
    }
    setBatchTopRatingsBusy(true);
    setBatchTopRatingsStatus("queued");
    setBatchTopRatingsProcessed(0);
    setBatchTopRatingsTotal(playerIds.length);
    setBatchTopRatingsOk(0);
    setBatchTopRatingsFailed(0);
    setBatchTopRatingsLastError("");
    setBatchTopRatingsEtaSeconds(null);
    try {
      const start = await api.post("/players/fetch-top-ratings-batch/start", { player_ids: playerIds });
      const jobId = String(start?.job_id || "");
      if (!jobId) {
        throw new Error("Failed to start Top-X batch job.");
      }
      setBatchTopRatingsJobId(jobId);
      await pollBatchTopRatingsJob(jobId);
    } catch (e) {
      setBatchTopRatingsStatus("failed");
      setBatchTopRatingsLastError(String(e?.message || "Failed to start Top-X batch job."));
      notify(`Top-X batch failed: ${e?.message || "unknown error"}`);
    } finally {
      setBatchTopRatingsBusy(false);
    }
  };

  const pauseBatchTopRatingsJob = async () => {
    if (!batchTopRatingsJobId) return;
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchTopRatingsJobId}/pause`, {});
      const applied = applyBatchTopRatingsStatus(status, batchTopRatingsJobId);
      if (applied.nextStatus === "pausing") {
        pollBatchTopRatingsJob(batchTopRatingsJobId);
      }
    } catch (e) {
      notify(`Failed to pause Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  const resumeBatchTopRatingsJob = async () => {
    if (!batchTopRatingsJobId) return;
    setBatchTopRatingsBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchTopRatingsJobId}/resume`, {});
      const applied = applyBatchTopRatingsStatus(status, batchTopRatingsJobId);
      if (["queued", "running", "pausing"].includes(applied.nextStatus)) {
        pollBatchTopRatingsJob(batchTopRatingsJobId);
      }
    } catch (e) {
      setBatchTopRatingsBusy(false);
      notify(`Failed to resume Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  useEffect(() => {
    let cancelled = false;

    const hydrateLatestTopRatingsJob = async () => {
      try {
        const latest = await api.get("/players/fetch-top-ratings-batch/latest");
        if (cancelled || !latest?.exists) return;
        if (latest?.status === "completed") return;
        const applied = applyBatchTopRatingsStatus(latest);
        if (["queued", "running", "pausing"].includes(applied.nextStatus)) {
          pollBatchTopRatingsJob(applied.jobId);
        }
      } catch {
        // The batch progress panel is optional on startup; failures should not block the database view.
      }
    };

    hydrateLatestTopRatingsJob();
    return () => {
      cancelled = true;
    };
  }, []);

  const openPlayerDetailsFromTeam = (playerId) => {
    const pid = Number(playerId);
    if (!Number.isFinite(pid) || pid <= 0) return;
    setDbTab("players");
    setShowTeamModal(false);
    setSelectedPlayer(pid);
    setShowPlayerModal(true);
  };

  const openTeamDetailsFromPlayer = (teamId) => {
    if (!teamId) return;
    setDbTab("teams");
    setShowPlayerModal(false);
    setSelectedTeam(teamId);
    setShowTeamModal(true);
  };

  const refreshAllRankingsToday = async () => {
    setRankingsRefreshBusy(true);
    try {
      const res = await api.post("/teams/refresh-rankings-today", {});
      await refresh();
      const hltv = res?.hltv || {};
      const vrs = res?.vrs || {};
      notify(
        `Rankings refreshed: HLTV u:${hltv.updated || 0} i:${hltv.inserted || 0} f:${hltv.failed || 0} | ` +
          `VRS u:${vrs.updated || 0} i:${vrs.inserted || 0} f:${vrs.failed || 0}`
      );
    } catch (e) {
      notify(`Rankings refresh failed: ${e?.message || "unknown error"}`);
    } finally {
      setRankingsRefreshBusy(false);
    }
  };

  const hasJsonEntries = (raw) => {
    if (!raw) return false;
    try {
      const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
      if (Array.isArray(parsed)) return parsed.length > 0;
      if (parsed && typeof parsed === "object") return Object.keys(parsed).length > 0;
      return false;
    } catch {
      return false;
    }
  };

  const hasBoostersAndRoles = (player) => hasJsonEntries(player.boosters_json) && hasJsonEntries(player.roles_json);
  const toNum = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
  const setPlayerSortByColumn = (column) => {
    const nextSort = {
      name: playerSort === "name_asc" ? "name_desc" : "name_asc",
      id: playerSort === "id_asc" ? "id_desc" : "id_asc",
      team: playerSort === "team_asc" ? "team_desc" : "team_asc",
      rating: playerSort === "rating_desc" ? "rating_asc" : "rating_desc",
      boost: playerSort === "boost_desc" ? "boost_asc" : "boost_desc",
    }[column];
    if (nextSort) setPlayerSort(nextSort);
  };
  const playerSortArrow = (column) => {
    const activeSorts = {
      name: ["name_asc", "name_desc"],
      id: ["id_asc", "id_desc"],
      team: ["team_asc", "team_desc"],
      rating: ["rating_asc", "rating_desc"],
      boost: ["boost_asc", "boost_desc"],
    }[column] || [];
    if (!activeSorts.includes(playerSort)) return "↕";
    return playerSort.endsWith("_asc") ? "↑" : "↓";
  };
  const batchTopRatingsProgressPct =
    batchTopRatingsTotal > 0 ? Math.min(100, Math.max(0, (batchTopRatingsProcessed / batchTopRatingsTotal) * 100)) : 0;
  const showBatchTopRatingsProgress = batchTopRatingsStatus !== "idle";
  const batchTopRatingsActive = ["queued", "running", "pausing"].includes(batchTopRatingsStatus);
  const batchTopRatingsResumable = ["paused", "failed"].includes(batchTopRatingsStatus);
  const missingTopRatingsCount = (players || []).filter((player) => !playerHasCompleteTopRatings(player)).length;
  const batchTopRatingsStatusLabel =
    {
      completed: "Completed",
      failed: "Failed",
      paused: "Paused",
      pausing: "Pausing",
      running: "Running",
      queued: "Queued",
    }[batchTopRatingsStatus] || "Queued";
  const playerTopxRows = useMemo(() => {
    const rows = Array.isArray(playerCurve?.bucket_rows) ? playerCurve.bucket_rows : [];
    return rows
      .map((row) => ({
        tier: Number(row?.tier),
        tierLabel: String(row?.tier_label || `Top ${Number(row?.tier)}`),
        bucketRating: Number.isFinite(Number(row?.bucket_rating)) ? Number(row.bucket_rating) : null,
        bucketDelta: Number.isFinite(Number(row?.bucket_delta)) ? Number(row.bucket_delta) : null,
        maps: Number(row?.maps || 0),
      }))
      .filter((row) => Number.isFinite(row.tier) && row.tier > 0 && row.tier < 100)
      .sort((a, b) => a.tier - b.tier);
  }, [playerCurve]);
  const playerTopxRatingAxis = useMemo(() => {
    return buildNiceStepAxis(
      playerTopxRows.map((row) => row.bucketRating),
      0.05
    );
  }, [playerTopxRows]);
  const filteredSortedPlayers = useMemo(() => {
    const q = playerSearch.trim().toLowerCase();
    const list = players.filter((p) => {
      if (!q) return true;
      const teamsText = (playerTeamLookup[p.player_id] || []).join(", ").toLowerCase();
      return (
        String(p.player_id).includes(q) ||
        String(p.name || "").toLowerCase().includes(q) ||
        teamsText.includes(q)
      );
    });

    list.sort((a, b) => {
      switch (playerSort) {
        case "name_desc":
          return String(b.name || "").localeCompare(String(a.name || ""));
        case "id_asc":
          return toNum(a.player_id) - toNum(b.player_id);
        case "id_desc":
          return toNum(b.player_id) - toNum(a.player_id);
        case "rating_desc":
          return toNum(b.rating) - toNum(a.rating);
        case "rating_asc":
          return toNum(a.rating) - toNum(b.rating);
        case "team_asc":
          return ((playerTeamLookup[a.player_id] || [])[0] || "").localeCompare(((playerTeamLookup[b.player_id] || [])[0] || ""));
        case "team_desc":
          return ((playerTeamLookup[b.player_id] || [])[0] || "").localeCompare(((playerTeamLookup[a.player_id] || [])[0] || ""));
        case "boost_asc":
          return Number(hasBoostersAndRoles(a)) - Number(hasBoostersAndRoles(b));
        case "boost_desc":
          return Number(hasBoostersAndRoles(b)) - Number(hasBoostersAndRoles(a));
        case "name_asc":
        default:
          return String(a.name || "").localeCompare(String(b.name || ""));
      }
    });

    return list;
  }, [players, playerSearch, playerSort, playerTeamLookup]);
  const filteredSortedTeams = useMemo(() => {
    const q = teamSearch.trim().toLowerCase();
    const list = teams.filter((t) => {
      if (!q) return true;
      const rosterText = [t.player1_id, t.player2_id, t.player3_id, t.player4_id, t.player5_id]
        .filter(Boolean)
        .map((pid) => playerLookup[pid] || pid)
        .join(", ")
        .toLowerCase();
      return (
        String(t.team_id).includes(q) ||
        String(t.name || "").toLowerCase().includes(q) ||
        rosterText.includes(q)
      );
    });

    list.sort((a, b) => {
      switch (teamSort) {
        case "name_desc":
          return String(b.name || "").localeCompare(String(a.name || ""));
        case "id_asc":
          return toNum(a.team_id) - toNum(b.team_id);
        case "id_desc":
          return toNum(b.team_id) - toNum(a.team_id);
        case "hltv_asc":
          return toNum(a.hltv_rank, 9999) - toNum(b.hltv_rank, 9999);
        case "hltv_desc":
          return toNum(b.hltv_rank, 9999) - toNum(a.hltv_rank, 9999);
        case "vrs_asc":
          return toNum(a.vrs_rank, 9999) - toNum(b.vrs_rank, 9999);
        case "vrs_desc":
          return toNum(b.vrs_rank, 9999) - toNum(a.vrs_rank, 9999);
        case "name_asc":
        default:
          return String(a.name || "").localeCompare(String(b.name || ""));
      }
    });

    return list;
  }, [teams, teamSearch, teamSort, playerLookup]);

  return (
    <div className="stack">
      <div className="tab-bar small">
        <button className={dbTab === "players" ? "tab active" : "tab"} onClick={() => setDbTab("players")}>
          Players
        </button>
        <button className={dbTab === "teams" ? "tab active" : "tab"} onClick={() => setDbTab("teams")}>
          Teams
        </button>
        <button className={dbTab === "matches" ? "tab active" : "tab"} onClick={() => setDbTab("matches")}>
          Matches
        </button>
      </div>

      {dbTab === "players" && <Section title="Players">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <div className="players-panel">
            <div className="grid two">
              <Input label="Search Players" value={playerSearch} onChange={setPlayerSearch} placeholder="Name, ID, or team" />
            </div>
            <div className="card sub players-batch-toolbar">
              <div className="actions" style={{ marginTop: 0 }}>
                <button
                  className="primary"
                  onClick={() => importPlayerTopRatingsBatch(false)}
                  disabled={players.length === 0 || batchTopRatingsActive}
                >
                  {batchTopRatingsActive ? `Importing ${batchTopRatingsTotal} players...` : `Import All (${players.length})`}
                </button>
                <button
                  className="secondary"
                  onClick={() => importPlayerTopRatingsBatch(true)}
                  disabled={missingTopRatingsCount === 0 || batchTopRatingsActive}
                >
                  {batchTopRatingsActive ? "Importing..." : `Import Missing (${missingTopRatingsCount})`}
                </button>
                {batchTopRatingsActive && batchTopRatingsJobId && (
                  <button className="secondary" onClick={pauseBatchTopRatingsJob} disabled={batchTopRatingsStatus === "pausing"}>
                    {batchTopRatingsStatus === "pausing" ? "Pausing..." : "Pause"}
                  </button>
                )}
                {batchTopRatingsResumable && batchTopRatingsJobId && (
                  <button className="secondary" onClick={resumeBatchTopRatingsJob} disabled={batchTopRatingsBusy}>
                    Resume
                  </button>
                )}
              </div>
            </div>
            {showBatchTopRatingsProgress && (
              <div className="card sub">
                <p className="muted">
                  Top-X progress: {batchTopRatingsProcessed.toLocaleString()} / {batchTopRatingsTotal.toLocaleString()} | ok{" "}
                  {batchTopRatingsOk} | failed {batchTopRatingsFailed}
                  {["queued", "running", "pausing"].includes(batchTopRatingsStatus) && batchTopRatingsTotal > batchTopRatingsProcessed
                    ? ` | ETA: ${formatBatchEta(batchTopRatingsEtaSeconds)}`
                    : ""}
                </p>
                <div className="progress">
                  <div className="progress-bar determinate" style={{ width: `${batchTopRatingsProgressPct}%` }} />
                </div>
                <p className="muted">
                  Status: {batchTopRatingsStatusLabel}
                </p>
                {batchTopRatingsLastError && <p className="muted">Last error: {batchTopRatingsLastError}</p>}
              </div>
            )}
            <div className="players-table-wrap">
            <table className="players-table">
              <colgroup>
                <col style={{ width: "22%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "30%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "16%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>
                    <button className="table-sort-button" onClick={() => setPlayerSortByColumn("name")}>
                      Name <span>{playerSortArrow("name")}</span>
                    </button>
                  </th>
                  <th>
                    <button className="table-sort-button" onClick={() => setPlayerSortByColumn("id")}>
                      ID <span>{playerSortArrow("id")}</span>
                    </button>
                  </th>
                  <th>
                    <button className="table-sort-button" onClick={() => setPlayerSortByColumn("team")}>
                      Team <span>{playerSortArrow("team")}</span>
                    </button>
                  </th>
                  <th>
                    <button className="table-sort-button" onClick={() => setPlayerSortByColumn("rating")}>
                      Rating <span>{playerSortArrow("rating")}</span>
                    </button>
                  </th>
                  <th title="Boosters and Roles imported">
                    <button className="table-sort-button" onClick={() => setPlayerSortByColumn("boost")}>
                      Boost/Role <span>{playerSortArrow("boost")}</span>
                    </button>
                  </th>
                </tr>
              </thead>
              <tbody>
                {filteredSortedPlayers.map((p) => (
                  <tr
                    key={p.player_id}
                    className={selectedPlayer === p.player_id ? "row-active" : ""}
                    onClick={() => {
                      setSelectedPlayer(p.player_id);
                      setShowPlayerModal(true);
                    }}
                    >
                      <td>{p.name}</td>
                      <td>{p.player_id}</td>
                      <td>{(playerTeamLookup[p.player_id] || []).join(", ") || "-"}</td>
                      <td>{Number(p.rating || 0).toFixed(2)}</td>
                      <td className="status-cell">
                        <span
                          className={hasBoostersAndRoles(p) ? "status-dot ok" : "status-dot missing"}
                        title={hasBoostersAndRoles(p) ? "Boosters and roles imported" : "Boosters and roles missing"}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            </div>
          </div>
        )}
      </Section>}

      {showPlayerModal && (
        <div className="modal-backdrop" onClick={() => setShowPlayerModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3 className="player-modal-title">{playerForm.name || "Player Details"}</h3>
              <button className="close" onClick={() => setShowPlayerModal(false)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <div className="player-summary">
                <div className="player-summary-meta">
                  <span className="player-summary-chip player-summary-id">ID {playerForm.player_id || "-"}</span>
                  <span className="player-summary-chip player-summary-rating">Rating {Number(playerForm.rating || 0).toFixed(2)}</span>
                  <div className="player-summary-teams-wrap">
                    {(playerTeamLinks[playerForm.player_id] || []).length > 0 ? (
                      (playerTeamLinks[playerForm.player_id] || []).map((team) => (
                        <button
                          key={`${playerForm.player_id}-${team.team_id}`}
                          type="button"
                          className="player-summary-chip player-summary-team-btn"
                          onClick={() => openTeamDetailsFromPlayer(team.team_id)}
                          title={`Open ${team.team_name}`}
                        >
                          {team.team_name}
                        </button>
                      ))
                    ) : (
                      <span className="player-summary-chip player-summary-teams">-</span>
                    )}
                  </div>
                </div>
              </div>
              <div className="tab-bar small">
                <button className={playerTab === "info" ? "tab active" : "tab"} onClick={() => setPlayerTab("info")}>
                  Ratings
                </button>
                <button className={playerTab === "topxGraph" ? "tab active" : "tab"} onClick={() => setPlayerTab("topxGraph")}>
                  Top X Graph
                </button>
                <button className={playerTab === "boosters" ? "tab active" : "tab"} onClick={() => setPlayerTab("boosters")}>
                  Boosters
                </button>
                <button className={playerTab === "roles" ? "tab active" : "tab"} onClick={() => setPlayerTab("roles")}>
                  Roles
                </button>
              </div>
              {playerTab === "info" && (
                <div className="stack">
                  <div className="actions" style={{ marginTop: 0 }}>
                    <button
                      className="primary button-with-spinner"
                      onClick={fetchPlayerTopRatings}
                      disabled={!playerForm.player_id || topRatingsBusy}
                    >
                      {topRatingsBusy && <span className="button-spinner" aria-hidden="true" />}
                      <span>{topRatingsBusy ? "Importing..." : "Import Top-X Data"}</span>
                    </button>
                  </div>
                  {playerTopxFeedback?.message && (
                    <p className={`inline-status ${playerTopxFeedback.kind || "info"}`}>{playerTopxFeedback.message}</p>
                  )}
                  <p className="muted">Last Top-X import: {formatTopxImportedAt(playerForm.last_topx_import_at)}</p>
                  <table>
                    <thead>
                      <tr>
                        <th>Metric</th>
                        {TOP_RATING_TIERS.map((tier) => (
                          <th key={`head-${tier}`}>{`Top ${tier}`}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      <tr>
                        <td>Rating</td>
                        {TOP_RATING_TIERS.map((tier) => {
                          const ratingRaw = playerForm[`rating_top${tier}`];
                          const rating =
                            ratingRaw === "" || ratingRaw === null || ratingRaw === undefined ? NaN : Number(ratingRaw);
                          return <td key={`rating-${tier}`}>{Number.isFinite(rating) ? rating.toFixed(2) : "-"}</td>;
                        })}
                      </tr>
                      <tr>
                        <td>Maps Played</td>
                        {TOP_RATING_TIERS.map((tier) => {
                          const mapsRaw = playerForm[`maps_top${tier}`];
                          const maps = mapsRaw === "" || mapsRaw === null || mapsRaw === undefined ? NaN : Number(mapsRaw);
                          return <td key={`maps-${tier}`}>{Number.isFinite(maps) ? Math.round(maps) : "-"}</td>;
                        })}
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}
              {playerTab === "topxGraph" && (
                <div className="stack">
                  {playerCurveLoading && <p className="muted">Loading Top-X graph...</p>}
                  {playerCurveError && <p className="error">{playerCurveError}</p>}
                  {!playerCurveLoading && !playerCurveError && playerTopxRows.length === 0 && (
                    <p className="muted">No adjusted Top-X bucket data available yet.</p>
                  )}
                  {playerTopxRows.length > 0 && (
                    <>
                      <p className="muted">
                        Overall rating: {Number(playerCurve?.base_rating || playerForm.rating || 0).toFixed(3)} | Sample maps:{" "}
                        {Math.round(Number(playerCurve?.sample_maps || 0))}
                      </p>
                      <div className="value-chart-wrap">
                        <ResponsiveContainer width="100%" height={320}>
                          <ComposedChart data={playerTopxRows} margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                            <CartesianGrid stroke="#284061" strokeDasharray="3 3" />
                            <XAxis
                              dataKey="tierLabel"
                              tick={{ fill: "#9fc5ff", fontSize: 12 }}
                              axisLine={{ stroke: "#365a89" }}
                              tickLine={{ stroke: "#365a89" }}
                            />
                            <YAxis
                              tick={{ fill: "#9fc5ff", fontSize: 12 }}
                              axisLine={{ stroke: "#365a89" }}
                              tickLine={{ stroke: "#365a89" }}
                              domain={playerTopxRatingAxis.domain}
                              ticks={playerTopxRatingAxis.ticks}
                              tickFormatter={(v) => Number(v).toFixed(2)}
                            />
                            <Tooltip
                              contentStyle={{ background: "#0e1f3f", border: "1px solid #2f5ca5", borderRadius: 10, color: "#dcecff" }}
                              formatter={(value, name) => {
                                if (name === "Adjusted Rating") return [Number(value).toFixed(3), "Adjusted Rating"];
                                return [value, name];
                              }}
                              labelFormatter={(_, payload) => {
                                const row = payload?.[0]?.payload || {};
                                const delta = Number(row.bucketDelta);
                                const deltaText = Number.isFinite(delta) ? `${delta >= 0 ? "+" : ""}${delta.toFixed(3)}` : "N/A";
                                return `${row.tierLabel || "-"} | Delta ${deltaText} | Maps ${Math.round(Number(row.maps || 0))}`;
                              }}
                            />
                            <Legend wrapperStyle={{ color: "#9fc5ff" }} />
                            <Line
                              type="linear"
                              dataKey="bucketRating"
                              name="Adjusted Rating"
                              stroke="#22d3ee"
                              strokeWidth={2.2}
                              dot={{ r: 4, fill: "#22d3ee", strokeWidth: 0 }}
                              connectNulls={false}
                              isAnimationActive={false}
                            />
                          </ComposedChart>
                        </ResponsiveContainer>
                      </div>
                      <table>
                        <thead>
                          <tr>
                            <th>Bucket</th>
                            <th>Adjusted Rating</th>
                            <th>Delta vs Overall</th>
                            <th>Maps</th>
                          </tr>
                        </thead>
                        <tbody>
                          {playerTopxRows.map((row) => (
                            <tr key={`topx-row-${row.tier}`}>
                              <td>{row.tierLabel}</td>
                              <td>{Number.isFinite(row.bucketRating) ? row.bucketRating.toFixed(3) : "-"}</td>
                              <td>
                                {Number.isFinite(row.bucketDelta)
                                  ? `${row.bucketDelta >= 0 ? "+" : ""}${row.bucketDelta.toFixed(3)}`
                                  : "-"}
                              </td>
                              <td>{Math.round(Number(row.maps || 0))}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </>
                  )}
                </div>
              )}
              {playerTab === "boosters" && (
                <div className="booster-tier-grid">
                  {Object.entries(boosterNames).map(([id, label]) => {
                    const value = Number(boosterForm[id]);
                    const average = Number(boosterAverages[id]);
                    const hasValue = Number.isFinite(value);
                    const hasAverage = Number.isFinite(average);
                    const percentText = hasValue ? `${Math.round(value * 100)}%` : "N/A";
                    let deltaText = "vs avg: N/A";
                    let deltaClass = "";
                    if (hasValue && hasAverage) {
                      const deltaPct = Math.round((value - average) * 100);
                      if (deltaPct > 0) {
                        deltaText = `+${deltaPct}% vs avg`;
                        deltaClass = "better";
                      } else if (deltaPct < 0) {
                        deltaText = `${deltaPct}% vs avg`;
                        deltaClass = "worse";
                      } else {
                        deltaText = "same as avg";
                        deltaClass = "equal";
                      }
                    }
                    return (
                      <div key={id} className="booster-tier-card">
                        <div className="booster-tier-label">{label}</div>
                        <div className="booster-tier-value">{percentText}</div>
                        <div className={`booster-tier-delta ${deltaClass}`}>{deltaText}</div>
                      </div>
                    );
                  })}
                </div>
              )}
              {playerTab === "roles" && (
                <div className="role-tier-grid">
                  {(() => {
                    const normalizeRate = (v) => {
                      const n = Number(v);
                      if (!Number.isFinite(n)) return null;
                      if (n < 0) return 0;
                      if (n > 1) return n / 100;
                      return n;
                    };

                    const roleMetrics = Object.entries(roleNames).map(([id, label]) => {
                      const majorRate = normalizeRate(roleForm[id]?.major ?? roleForm[id]?.major_win_pct);
                      const minorRate = normalizeRate(roleForm[id]?.minor ?? roleForm[id]?.minor_win_pct);
                      const hasRates = majorRate !== null && minorRate !== null;
                      const expected = hasRates
                        ? 5 * majorRate + 2 * minorRate - 2 * (1 - majorRate - minorRate)
                        : null;
                      return {
                        id,
                        label,
                        hasRates,
                        majorRate,
                        minorRate,
                        expected,
                      };
                    });

                    const top3 = roleMetrics
                      .filter((r) => Number.isFinite(r.expected))
                      .sort((a, b) => b.expected - a.expected)
                      .slice(0, 3);
                    const rankById = {};
                    top3.forEach((r, idx) => {
                      rankById[r.id] = idx + 1;
                    });

                    return roleMetrics.map((r) => {
                      const majorText = r.hasRates ? `${Math.round(r.majorRate * 100)}%` : "N/A";
                      const minorText = r.hasRates ? `${Math.round(r.minorRate * 100)}%` : "N/A";
                      const expectedText = Number.isFinite(r.expected) ? r.expected.toFixed(2) : "N/A";
                      const rank = rankById[r.id] || null;
                      const medalClass = rank === 1 ? "gold" : rank === 2 ? "silver" : rank === 3 ? "bronze" : "";

                      return (
                        <div key={r.id} className={`role-tier-card ${medalClass}`}>
                          <div className="role-tier-label">{r.label}</div>
                          <div className="role-tier-split">
                            <span>Major: {majorText}</span>
                            <span>Minor: {minorText}</span>
                          </div>
                          <div className="role-tier-expected">{expectedText} pts/game</div>
                        </div>
                      );
                    });
                  })()}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {showTeamModal && (
        <div className="modal-backdrop" onClick={() => setShowTeamModal(false)}>
          <div className="modal team-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3 className="player-modal-title">{teamForm.name || "Team"}</h3>
              <button className="close" onClick={() => setShowTeamModal(false)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <section className="team-detail-block">
                <h4 className="team-detail-heading">Roster</h4>
                <div className="team-roster-grid">
                  {[teamForm.p1, teamForm.p2, teamForm.p3, teamForm.p4, teamForm.p5].map((pid, idx) => (
                    <button
                      type="button"
                      className="team-player-tile"
                      key={`team-player-${idx + 1}`}
                      onClick={() => openPlayerDetailsFromTeam(pid)}
                      disabled={!Number(pid)}
                      title={Number(pid) ? `Open ${playerLookup[Number(pid)] || "player"}` : "No player"}
                    >
                      <div className="team-player-slot">Player {idx + 1}</div>
                      <div className="team-player-name">{playerLookup[Number(pid)] || "-"}</div>
                    </button>
                  ))}
                </div>
              </section>

              <section className="team-detail-block">
                <h4 className="team-detail-heading">Rankings</h4>
                <div className="team-stats-grid">
                  <div className="team-stat-card">
                    <div className="team-stat-label">HLTV Rank</div>
                    <div className="team-stat-value">{teamForm.hltv_rank || "-"}</div>
                  </div>
                  <div className="team-stat-card">
                    <div className="team-stat-label">HLTV Points</div>
                    <div className="team-stat-value">{teamForm.hltv_points || "-"}</div>
                  </div>
                  <div className="team-stat-card">
                    <div className="team-stat-label">VRS Rank</div>
                    <div className="team-stat-value">{teamForm.vrs_rank || "-"}</div>
                  </div>
                  <div className="team-stat-card">
                    <div className="team-stat-label">VRS Points</div>
                    <div className="team-stat-value">{teamForm.vrs_points || "-"}</div>
                  </div>
                </div>
              </section>
            </div>
            <div className="actions">
              <button className="danger" onClick={deleteTeam} disabled={!selectedTeam}>
                Delete Team
              </button>
              <button className="secondary" onClick={() => setShowTeamModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {dbTab === "teams" && <Section title="Teams">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <>
            <div className="teams-controls">
              <div className="teams-toolbar">
                <button className="primary" onClick={refreshAllRankingsToday} disabled={rankingsRefreshBusy || teams.length === 0}>
                  {rankingsRefreshBusy ? "Refreshing Rankings..." : "Refresh Rankings (HLTV + VRS)"}
                </button>
                <span className="teams-meta">{filteredSortedTeams.length} teams shown</span>
              </div>
              <div className="grid two teams-filters">
                <Input label="Search Teams" value={teamSearch} onChange={setTeamSearch} placeholder="Name, ID, or player" />
                <Select
                  label="Sort Teams"
                  value={teamSort}
                  onChange={setTeamSort}
                  options={[
                    { value: "name_asc", label: "Name A-Z" },
                    { value: "name_desc", label: "Name Z-A" },
                    { value: "id_asc", label: "ID low-high" },
                    { value: "id_desc", label: "ID high-low" },
                    { value: "hltv_asc", label: "HLTV rank low-high" },
                    { value: "hltv_desc", label: "HLTV rank high-low" },
                    { value: "vrs_asc", label: "VRS rank low-high" },
                    { value: "vrs_desc", label: "VRS rank high-low" },
                  ]}
                />
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>HLTV Rank</th>
                  <th>HLTV Points</th>
                  <th>VRS Rank</th>
                  <th>VRS Points</th>
                  <th>Players</th>
                </tr>
              </thead>
              <tbody>
                  {filteredSortedTeams.map((t) => (
                    <tr
                      key={t.team_id}
                      className={selectedTeam === t.team_id ? "row-active" : ""}
                      onClick={() => {
                        setSelectedTeam(t.team_id);
                        setShowTeamModal(true);
                      }}
                    >
                    <td>{t.name}</td>
                    <td>{t.team_id}</td>
                    <td>{t.hltv_rank}</td>
                    <td>{t.hltv_points ?? "-"}</td>
                    <td>{t.vrs_rank}</td>
                    <td>{t.vrs_points ?? "-"}</td>
                    <td>
                      {[t.player1_id, t.player2_id, t.player3_id, t.player4_id, t.player5_id]
                        .filter(Boolean)
                        .map((pid) => playerLookup[pid] || pid)
                        .join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </Section>}
      {dbTab === "matches" && (
        <Section title="Matches">
          <MatchesDataPanel notify={notify} />
        </Section>
      )}

    </div>
  );
}

function AdminTab({ refresh, notify }) {
  const [dataTab, setDataTab] = useState("trigger");
  const [triggerJson, setTriggerJson] = useState("");
  const [triggerUpdatedPlayers, setTriggerUpdatedPlayers] = useState([]);
  const [importResult, setImportResult] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [wipeBusy, setWipeBusy] = useState(false);
  const [fitBusy, setFitBusy] = useState(false);
  const [fitRows, setFitRows] = useState([{ rankA: "", oddsA: "", rankB: "", oddsB: "" }]);

  const importTriggers = async () => {
    if (!triggerJson.trim()) {
      setImportResult("Paste triggerRates JSON first.");
      return;
    }
    setImportBusy(true);
    setImportResult("");
    setTriggerUpdatedPlayers([]);
    try {
      const res = await api.post("/admin/import-trigger-rates", { trigger_json: triggerJson });
      const msg = `Updated players: ${res.updated_players ?? 0}`;
      setImportResult(msg);
      setTriggerUpdatedPlayers(res.updated_players_info || []);
      setTriggerJson("");
      notify("Trigger rates imported");
      refresh();
    } finally {
      setImportBusy(false);
    }
  };

  const wipeDb = async () => {
    setWipeBusy(true);
    await api.post("/admin/wipe", {});
    notify("Database wiped");
    setWipeBusy(false);
    refresh();
  };

  const fitWinrate = async () => {
    const samples = fitRows
      .map((r) => ({
        rank_a: Number(r.rankA),
        rank_b: Number(r.rankB),
        odds_a: Number(r.oddsA),
      }))
      .filter(
        (s) =>
          Number.isFinite(s.rank_a) &&
          s.rank_a > 0 &&
          Number.isFinite(s.rank_b) &&
          s.rank_b > 0 &&
          Number.isFinite(s.odds_a) &&
          s.odds_a > 0
      );
    if (samples.length < 2) {
      setImportResult("Need at least 2 valid rows (rankA, oddsA, rankB, oddsB). Using oddsA to imply P(A).");
      return;
    }
    setFitBusy(true);
    setImportResult("");
    try {
      const res = await api.post("/admin/fit-winrate", { samples });
      setImportResult(`Fit saved. a_offset=${res.a_offset?.toFixed?.(4)} b_slope=${res.b_slope?.toFixed?.(4)} (n=${res.n_samples})`);
      notify("Winrate parameters updated");
      refresh();
    } finally {
      setFitBusy(false);
    }
  };

  return (
    <div className="stack">
      <Section title="Data Management Tools">
        <div className="tab-bar small">
          <button className={dataTab === "trigger" ? "tab active" : "tab"} onClick={() => setDataTab("trigger")}>
            Trigger Rates
          </button>
          <button className={dataTab === "fit" ? "tab active" : "tab"} onClick={() => setDataTab("fit")}>
            Winrate Fit
          </button>
          <button className={dataTab === "maintenance" ? "tab active" : "tab"} onClick={() => setDataTab("maintenance")}>
            Maintenance
          </button>
        </div>

        {dataTab === "trigger" && (
          <div className="stack">
            <label className="field">
              <span>Trigger Rates JSON (playerTriggerRates)</span>
              <textarea
                rows={14}
                value={triggerJson}
                onChange={(e) => setTriggerJson(e.target.value)}
                placeholder="Paste the triggerRates JSON here"
              />
            </label>
            <div className="actions">
              <button className="primary" onClick={importTriggers} disabled={importBusy}>
                {importBusy ? "Importing..." : "Import Trigger Rates"}
              </button>
            </div>
            {triggerUpdatedPlayers.length > 0 && (
              <div className="card sub">
                <h4>Updated Players ({triggerUpdatedPlayers.length})</h4>
                <ul>
                  {triggerUpdatedPlayers.map((p) => (
                    <li key={p.player_id}>
                      {p.name} (ID {p.player_id})
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {dataTab === "fit" && (
          <div className="stack">
            <div className="card sub">
              <h4>Winrate Fit Samples</h4>
              <table>
                <thead>
                  <tr>
                    <th>Rank A</th>
                    <th>Odds A</th>
                    <th>Rank B</th>
                    <th>Odds B</th>
                  </tr>
                </thead>
                <tbody>
                  {fitRows.map((row, idx) => (
                    <tr key={idx}>
                      <td>
                        <input
                          value={row.rankA}
                          onChange={(e) =>
                            setFitRows((prev) =>
                              prev.map((r, i) => (i === idx ? { ...r, rankA: e.target.value } : r))
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          value={row.oddsA}
                          onChange={(e) =>
                            setFitRows((prev) =>
                              prev.map((r, i) => (i === idx ? { ...r, oddsA: e.target.value } : r))
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          value={row.rankB}
                          onChange={(e) =>
                            setFitRows((prev) =>
                              prev.map((r, i) => (i === idx ? { ...r, rankB: e.target.value } : r))
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          value={row.oddsB}
                          onChange={(e) =>
                            setFitRows((prev) =>
                              prev.map((r, i) => (i === idx ? { ...r, oddsB: e.target.value } : r))
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="actions">
                <button
                  className="secondary"
                  onClick={() => setFitRows((prev) => [...prev, { rankA: "", oddsA: "", rankB: "", oddsB: "" }])}
                >
                  Add Row
                </button>
              </div>
            </div>
            <div className="actions">
              <button className="primary" onClick={fitWinrate} disabled={fitBusy}>
                {fitBusy ? "Fitting..." : "Fit Winrate Params"}
              </button>
            </div>
          </div>
        )}

        {dataTab === "maintenance" && (
          <div className="stack">
            <button className="danger" onClick={wipeDb} disabled={wipeBusy}>
              {wipeBusy ? "Wiping..." : "Wipe Database"}
            </button>
            <p className="muted">Deletes all players and teams (schema is kept).</p>
          </div>
        )}

        {importResult && <p className="muted">{importResult}</p>}
      </Section>
    </div>
  );
}

function BoosterCalculatorTab() {
  const [playerId, setPlayerId] = useState("");
  const [majorPct, setMajorPct] = useState("0.30");
  const [minorPct, setMinorPct] = useState("0.20");
  const [winProb, setWinProb] = useState("0.50");
  const [opponentRanks, setOpponentRanks] = useState("5,10,20,30,50");
  const [boosterRates, setBoosterRates] = useState("0.12,0.10,0.09,0.08,0.07");
  const [matches, setMatches] = useState("5");
  const [expectedGames, setExpectedGames] = useState("4.2");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);

  const run = async () => {
    setBusy(true);
    setError("");
    try {
      const rates = boosterRates
        .split(",")
        .map((x) => x.trim())
        .filter((x) => x.length > 0)
        .map((x) => Number(x))
        .filter((x) => Number.isFinite(x));
      const oppRanks = opponentRanks
        .split(",")
        .map((x) => x.trim())
        .filter((x) => x.length > 0)
        .map((x) => Number(x))
        .filter((x) => Number.isFinite(x))
        .map((x) => Math.round(x));
      const matchCount = Number(matches);
      if (!Number.isFinite(Number(playerId)) || String(playerId).trim() === "") {
        setError("Player ID is required.");
        setResult(null);
        return;
      }
      if (!Number.isFinite(matchCount) || matchCount < 1) {
        setError("Match count must be at least 1.");
        setResult(null);
        return;
      }
      if (oppRanks.length < matchCount) {
        setError(`Provide at least ${matchCount} opponent ranks.`);
        setResult(null);
        return;
      }

      const payload = {
        player_id: Number(playerId),
        major_pct: Number(majorPct),
        minor_pct: Number(minorPct),
        win_prob: Number(winProb),
        opponent_ranks: oppRanks,
        booster_rates: rates,
        matches: matchCount,
        expected_games: expectedGames === "" ? undefined : Number(expectedGames),
      };

      const res = await api.post("/admin/booster-calc", payload);
      if (res?.detail) {
        setError(String(res.detail));
        setResult(null);
      } else {
        setResult(res);
      }
    } catch (e) {
      setError("Failed to run booster calculation.");
      setResult(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Booster Calculator">
      <div className="stack">
        <p className="muted">
          Uses the player&apos;s stored overall rating for each match. Opponent rank is recorded here but does not adjust rating.
        </p>
        <div className="grid three">
          <Input label="Player ID" value={playerId} onChange={setPlayerId} />
          <Input label="Role Major %" value={majorPct} onChange={setMajorPct} />
          <Input label="Role Minor %" value={minorPct} onChange={setMinorPct} />
          <Input label="Win Probability (0-1)" value={winProb} onChange={setWinProb} />
          <Input label="# Matches" value={matches} onChange={setMatches} />
          <Input label="Expected Games (optional)" value={expectedGames} onChange={setExpectedGames} />
        </div>
        <label className="field">
          <span>Opponent ranks per match (comma-separated)</span>
          <input
            value={opponentRanks}
            onChange={(e) => setOpponentRanks(e.target.value)}
            placeholder="e.g. 5,10,20,30,50"
          />
        </label>
        <label className="field">
          <span>Booster rates per match (comma-separated)</span>
          <input
            value={boosterRates}
            onChange={(e) => setBoosterRates(e.target.value)}
            placeholder="e.g. 0.12,0.10,0.09,0.08,0.07"
          />
        </label>
        <div className="actions">
          <button className="primary" onClick={run} disabled={busy}>
            {busy ? "Calculating..." : "Calculate"}
          </button>
        </div>
        {error && <p className="error">{error}</p>}
        {result && (
          <div className="card sub">
            <p className="muted">
              Base per match: Rating {result.rating_points?.toFixed?.(2)} | Role {result.role_points?.toFixed?.(2)} | Win{" "}
              {result.win_points?.toFixed?.(2)}
            </p>
            {result.expected_games !== undefined && (
              <p className="muted">
                Expected total ({result.expected_games?.toFixed?.(2)} games): {result.expected_total_points?.toFixed?.(2)} (booster{" "}
                {result.expected_booster_points?.toFixed?.(2)})
              </p>
            )}
            <table>
              <thead>
                <tr>
                  <th>Match</th>
                  <th>Opp Rank</th>
                  <th>Match Rating</th>
                  <th>Rating</th>
                  <th>Role</th>
                  <th>Win</th>
                  <th>Booster</th>
                  <th>Total</th>
                </tr>
              </thead>
              <tbody>
                {(result.per_match || []).map((m) => (
                  <tr key={m.match_number}>
                    <td>{m.match_number}</td>
                    <td>{m.opponent_rank ?? "-"}</td>
                    <td>{m.match_rating?.toFixed?.(3) ?? "-"}</td>
                    <td>{m.rating_points?.toFixed?.(2)}</td>
                    <td>{m.role_points?.toFixed?.(2)}</td>
                    <td>{m.win_points?.toFixed?.(2)}</td>
                    <td>{m.booster_points?.toFixed?.(2)}</td>
                    <td>{m.total_points?.toFixed?.(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </Section>
  );
}

function SwissTab({ teams, teamLookup, players, onOpenPlayer }) {
  const [swissTab, setSwissTab] = useState("group");
  const [events, setEvents] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [eventTeamNames, setEventTeamNames] = useState(new Set());
  const [selectedTeamIds, setSelectedTeamIds] = useState([]);
  const [boMode, setBoMode] = useState("elim_qual");
  const [simCount, setSimCount] = useState("200");
  const [simResults, setSimResults] = useState(null);
  const [simUpdatedAt, setSimUpdatedAt] = useState("");

  const normalizeTeamName = (name) => String(name || "").trim().toLowerCase();

  const loadStoredSimulation = async () => {
    const data = await api.get("/simulate/latest");
    if (!data?.exists) return;
    const payload = data.payload || {};
    setSelectedTeamIds(Array.isArray(payload.team_ids) ? payload.team_ids.map((x) => Number(x)) : []);
    setBoMode(payload.bo3_mode || "elim_qual");
    setSimCount(String(payload.n_sims || 200));
    setSimResults(data.results || null);
    setSimUpdatedAt(data.updated_at || "");
  };

  const loadEventsForSwiss = async () => {
    const data = await api.get("/events/");
    if (data?.detail) return;

    const allEvents = Array.isArray(data.events) ? data.events : [];
    setEvents(allEvents);

    const active = data.active_event_id;
    const fallback = allEvents.length > 0 ? allEvents[0].event_id : "";
    const nextSelected = active ?? fallback;
    setSelectedEventId(nextSelected === "" ? "" : String(nextSelected));
  };

  const loadEventTeams = async (eventId) => {
    if (!eventId) {
      setEventTeamNames(new Set());
      return;
    }
    const data = await api.get(`/events/${eventId}`);
    if (data?.detail) return;

    const names = new Set(
      (data.teams || []).map((t) => normalizeTeamName(t.team_name))
    );
    setEventTeamNames(names);
  };

  useEffect(() => {
    loadStoredSimulation();
    loadEventsForSwiss();
  }, []);

  useEffect(() => {
    loadEventTeams(selectedEventId);
  }, [selectedEventId]);

  const filteredTeams = useMemo(() => {
    if (!selectedEventId) return [];
    if (!eventTeamNames || eventTeamNames.size === 0) return [];
    return teams.filter((t) => eventTeamNames.has(normalizeTeamName(t.name)));
  }, [teams, selectedEventId, eventTeamNames]);

  useEffect(() => {
    const allowed = new Set(filteredTeams.map((t) => t.team_id));
    setSelectedTeamIds((prev) => prev.filter((id) => allowed.has(id)));

    const resultTeamIds = Object.keys(simResults || {}).map((k) => Number(k));
    const hasOutOfEventResults = resultTeamIds.some((id) => !allowed.has(id));
    if (hasOutOfEventResults) {
      setSimResults(null);
      setSimUpdatedAt("");
    }
  }, [filteredTeams]);

  const resetStoredSimulation = async () => {
    await api.delete("/simulate/latest");
    setSelectedTeamIds([]);
    setBoMode("elim_qual");
    setSimCount("200");
    setSimResults(null);
    setSimUpdatedAt("");
  };

  return (
    <div className="stack">
      <Section title="Swiss Event">
        <div className="grid three">
          <Select
            label="Event"
            value={selectedEventId}
            onChange={setSelectedEventId}
            options={
              events.length > 0
                ? events.map((e) => ({ value: String(e.event_id), label: `Event ${e.event_id}` }))
                : [{ value: "", label: "No events imported" }]
            }
          />
          <div className="field">
            <span>Teams In Event</span>
            <div className="pill">{filteredTeams.length}</div>
          </div>
          <div className="field">
            <span>Selection</span>
            <div className="pill">{selectedTeamIds.length} selected</div>
          </div>
        </div>
        {events.length === 0 && (
          <p className="muted">Import an event in the Events tab first.</p>
        )}
      </Section>

      <div className="tab-bar small">
        <button className={swissTab === "group" ? "tab active" : "tab"} onClick={() => setSwissTab("group")}>
          Group Stage
        </button>
        <button className={swissTab === "top5" ? "tab active" : "tab"} onClick={() => setSwissTab("top5")}>
          Top 5 Teams
        </button>
        <button className={swissTab === "value" ? "tab active" : "tab"} onClick={() => setSwissTab("value")}>
          Player Value
        </button>
        <button className={swissTab === "single" ? "tab active" : "tab"} onClick={() => setSwissTab("single")}>
          Bracket Simulator
        </button>
      </div>
      {swissTab === "group" && (
        <GroupStageTab
          teams={filteredTeams}
          teamLookup={teamLookup}
          selected={selectedTeamIds}
          setSelected={setSelectedTeamIds}
          bo={boMode}
          setBo={setBoMode}
          sims={simCount}
          setSims={setSimCount}
          results={simResults}
          setResults={(data) => {
            setSimResults(data);
            setSimUpdatedAt(new Date().toISOString());
          }}
          simUpdatedAt={simUpdatedAt}
          onResetSimulation={resetStoredSimulation}
          onOpenPlayer={onOpenPlayer}
        />
      )}
      {swissTab === "top5" && (
        <TopTeamsTab
          teamLookup={teamLookup}
          selected={selectedTeamIds}
          bo={boMode}
          sims={simCount}
          results={simResults}
          onOpenPlayer={onOpenPlayer}
        />
      )}
      {swissTab === "value" && <SwissPlayerValueTab results={simResults} players={players} />}
      {swissTab === "single" && <BracketTab teams={filteredTeams} teamLookup={teamLookup} />}
    </div>
  );
}

export default function App() {
  const [active, setActive] = useState("view");
  const [openPlayerId, setOpenPlayerId] = useState(null);
  const [players, setPlayers] = useState([]);
  const [teams, setTeams] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [p, t] = await Promise.all([api.get("/players/"), api.get("/teams/")]);
      setPlayers(p);
      setTeams(t);
    } catch (e) {
      setError("Backend unavailable. Make sure FastAPI is running.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const teamLookup = useMemo(() => {
    const map = {};
    teams.forEach((t) => (map[t.team_id] = t.name));
    return map;
  }, [teams]);

  const playerLookup = useMemo(() => {
    const map = {};
    players.forEach((p) => (map[p.player_id] = p.name));
    return map;
  }, [players]);

  const notify = (msg) => {
    setToast(msg);
    setTimeout(() => setToast(""), 2000);
  };
  const handleOpenPlayerFromAnywhere = (playerId) => {
    const pid = Number(playerId);
    if (!Number.isFinite(pid) || pid <= 0) return;
    setActive("view");
    setOpenPlayerId(pid);
  };

  const sortTeams = (list, key) => {
    const arr = [...(list || [])];
    switch (key) {
      case "ev_asc":
        arr.sort((a, b) => a.total_ev - b.total_ev);
        break;
      case "cost_asc":
        arr.sort((a, b) => a.cost - b.cost);
        break;
      case "cost_desc":
        arr.sort((a, b) => b.cost - a.cost);
        break;
      case "cpp_desc":
        arr.sort((a, b) => b.total_ev / (b.cost || 1) - a.total_ev / (a.cost || 1));
        break;
      case "ev_desc":
      default:
        arr.sort((a, b) => b.total_ev - a.total_ev);
    }
    return arr;
  };

  const applyFilters = (teams, includeSet, excludeSet) => {
    if (!teams) return [];
    return teams.filter((team) => {
      const ids = team.players.map((p) => p.player_id);
      if (includeSet && includeSet.size > 0) {
        for (const pid of includeSet) {
          if (!ids.includes(pid)) return false;
        }
      }
      if (excludeSet && excludeSet.size > 0) {
        for (const pid of excludeSet) {
          if (ids.includes(pid)) return false;
        }
      }
      return true;
    });
  };

  const contentMap = {
    view: (
      <DatabaseTab
        players={players}
        teams={teams}
        loading={loading}
        error={error}
        refresh={load}
        notify={notify}
        openPlayerId={openPlayerId}
        onOpenPlayerHandled={() => setOpenPlayerId(null)}
      />
    ),
    events: <EventsTab refreshData={load} notify={notify} players={players} />,
    sim: <SwissTab teams={teams} teamLookup={teamLookup} players={players} onOpenPlayer={handleOpenPlayerFromAnywhere} />,
    playoff: (
      <PlayoffTab
        teams={teams}
        teamLookup={teamLookup}
        players={players}
        sortTeams={sortTeams}
        applyFilters={applyFilters}
        onOpenPlayer={handleOpenPlayerFromAnywhere}
      />
    ),
    admin: <AdminTab refresh={load} notify={notify} />,
  };

  return (
    <div className="layout">
      <nav className="tab-bar">
        {tabs.map((t) => (
          <TabButton key={t.key} active={t.key === active} onClick={() => setActive(t.key)}>
            {t.label}
          </TabButton>
        ))}
      </nav>

      {toast && <div className="toast">{toast}</div>}
      <main className="content">{contentMap[active]}</main>
    </div>
  );
}

