import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
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
  { key: "modelLab", label: "Model Lab" },
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

const requestJson = async (path, init = {}, timeoutMs = 30000) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`http://127.0.0.1:8000${path}`, { ...init, signal: controller.signal });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new Error("Backend did not respond in time. Restart FastAPI and try again.");
    }
    throw e;
  } finally {
    clearTimeout(timeout);
  }
  const data = await parseJsonSafe(res);
  if (!res.ok) {
    const detail = data?.detail || `HTTP ${res.status}`;
    throw new Error(String(detail));
  }
  return data;
};

const api = window.api || {
  get: (path, timeoutMs) => requestJson(path, {}, timeoutMs),
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

const sortDirectionFor = (sortValue, ascValue, descValue) => {
  if (sortValue === ascValue) return "asc";
  if (sortValue === descValue) return "desc";
  return "";
};

const nextSortValue = (sortValue, ascValue, descValue, defaultDirection = "asc") => {
  if (sortValue === ascValue) return descValue;
  if (sortValue === descValue) return ascValue;
  return defaultDirection === "desc" ? descValue : ascValue;
};

const SortHeader = ({ children, sortValue, asc, desc, onChange, defaultDirection = "asc", title }) => {
  const direction = sortDirectionFor(sortValue, asc, desc);
  const arrow = direction === "asc" ? "↑" : direction === "desc" ? "↓" : "↕";
  return (
    <th title={title}>
      <button
        type="button"
        className="table-sort-button"
        onClick={() => onChange(nextSortValue(sortValue, asc, desc, defaultDirection))}
        aria-sort={direction === "asc" ? "ascending" : direction === "desc" ? "descending" : "none"}
      >
        {children} <span>{arrow}</span>
      </button>
    </th>
  );
};

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

  min = Math.floor(min / stepValue) * stepValue;
  max = Math.ceil(max / stepValue) * stepValue;
  if (min >= Math.min(...finite)) {
    min -= stepValue;
  }
  if (max <= Math.max(...finite)) {
    max += stepValue;
  }
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
        Number(comps?.total_points_without_booster),
        Number(comps?.rating_points_total) + Number(comps?.win_points_total) + Number(comps?.role_points_total),
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
        raw_booster: Number(comps?.booster_points_total || comps?.booster || 0),
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

const boosterOptionsForBreakdownRow = (row) => {
  const options = Array.isArray(row?.booster_options) && row.booster_options.length > 0
    ? row.booster_options
    : [
        {
          booster_id: row?.booster_id,
          booster_name: row?.booster_name,
          booster_trigger_rate: row?.booster_trigger_rate,
          booster_points: row?.booster_points,
        },
      ];
  return options
    .map((option) => {
      const boosterId = Number(option?.booster_id);
      const points = Number(option?.booster_points || 0);
      if (!Number.isFinite(boosterId) || boosterId < 0 || boosterId > 30 || points <= 0) return null;
      return {
        boosterId,
        boosterName: option.booster_name || `Booster ${boosterId}`,
        triggerRate: Number(option.booster_trigger_rate || 0),
        points,
      };
    })
    .filter(Boolean)
    .sort((a, b) => b.points - a.points);
};

const assignRosterBoostersFromBreakdowns = (rosterBreakdowns) => {
  const slots = [];
  Object.entries(rosterBreakdowns || {}).forEach(([pidRaw, rows]) => {
    const playerId = Number(pidRaw);
    if (!Number.isFinite(playerId)) return;
    (rows || []).forEach((row, rowIdx) => {
      const options = boosterOptionsForBreakdownRow(row);
      if (options.length === 0) return;
      slots.push({
        key: `${playerId}:${rowIdx}`,
        playerId,
        rowIdx,
        matchNumber: Number(row?.match_number || 0),
        options,
      });
    });
  });

  let states = new Map([[0, { score: 0, choices: [] }]]);
  slots.forEach((slot) => {
    const nextStates = new Map(states);
    states.forEach((state, mask) => {
      slot.options.forEach((option) => {
        const bit = 1 << option.boosterId;
        if (mask & bit) return;
        const nextMask = mask | bit;
        const nextScore = state.score + option.points;
        const existing = nextStates.get(nextMask);
        if (!existing || nextScore > existing.score + 1e-9) {
          nextStates.set(nextMask, {
            score: nextScore,
            choices: [
              ...state.choices,
              {
                key: slot.key,
                playerId: slot.playerId,
                rowIdx: slot.rowIdx,
                matchNumber: slot.matchNumber,
                boosterId: option.boosterId,
                boosterName: option.boosterName,
                triggerRate: option.triggerRate,
                points: option.points,
              },
            ],
          });
        }
      });
    });
    states = nextStates;
  });

  let best = { score: 0, choices: [] };
  states.forEach((state) => {
    if (state.score > best.score + 1e-9) best = state;
  });
  return best.choices;
};

const rosterAssignedBoosterPlayer = (player, rosterPlayers) => {
  const pid = Number(player?.player_id);
  const roster = Array.isArray(rosterPlayers) ? rosterPlayers : [];
  if (!Number.isFinite(pid) || !Array.isArray(player?.point_breakdown) || player.point_breakdown.length === 0) {
    return player;
  }

  const rosterBreakdowns = {};
  roster.forEach((rosterPlayer) => {
    const rosterPid = Number(rosterPlayer?.player_id);
    if (Number.isFinite(rosterPid)) rosterBreakdowns[rosterPid] = rosterPlayer?.point_breakdown || [];
  });
  const assignedRows = new Map(assignRosterBoostersFromBreakdowns(rosterBreakdowns).map((assignment) => [assignment.key, assignment]));

  let assignedBoosterTotal = 0;
  const pointBreakdown = player.point_breakdown.map((row, rowIdx) => {
    const key = `${pid}:${rowIdx}`;
    const assigned = assignedRows.get(key);
    const rating = Number(row.rating_points || 0);
    const win = Number(row.win_points || 0);
    const role = Number(row.role_points || 0);
    const assignedBooster = assigned ? Number(assigned.points || 0) : 0;
    assignedBoosterTotal += assignedBooster;
    return {
      ...row,
      raw_booster_points: Number(row.booster_points || 0),
      raw_booster_name: row.booster_name,
      raw_booster_trigger_rate: Number(row.booster_trigger_rate || 0),
      booster_points: assignedBooster,
      booster_id: assigned ? assigned.boosterId : row.booster_id,
      booster_name: assigned ? assigned.boosterName : "Unassigned",
      booster_trigger_rate: assigned ? Number(assigned.triggerRate || 0) : 0,
      booster_assigned: Boolean(assigned),
      total_points: rating + win + role,
    };
  });

  return {
    ...player,
    booster: assignedBoosterTotal,
    booster_ev: assignedBoosterTotal,
    point_breakdown: pointBreakdown,
    roster_boosters_assigned: true,
  };
};

const aggregatePlayoffBoosterUsage = (player, rosterPlayers, outcomes) => {
  const pid = Number(player?.player_id);
  const rosterIds = (rosterPlayers || []).map((p) => Number(p?.player_id)).filter((id) => Number.isFinite(id));
  const rows = [];
  if (!Number.isFinite(pid) || rosterIds.length === 0 || !Array.isArray(outcomes) || outcomes.length === 0) return rows;

  const slotBuckets = new Map();
  outcomes.forEach((outcome) => {
    const probability = Number(outcome?.probability || 0);
    if (!Number.isFinite(probability) || probability <= 0) return;
    const allBreakdowns = outcome?.player_breakdown || {};
    const rosterBreakdowns = {};
    rosterIds.forEach((rosterPid) => {
      rosterBreakdowns[rosterPid] = allBreakdowns[String(rosterPid)] || [];
    });
    assignRosterBoostersFromBreakdowns(rosterBreakdowns)
      .filter((assignment) => assignment.playerId === pid)
      .forEach((assignment) => {
        if (!assignment.matchNumber) return;
        const slotKey = String(assignment.matchNumber);
        const slot = slotBuckets.get(slotKey) || {
          match_number: assignment.matchNumber,
          probability: 0,
          expected_points: 0,
          boosters: new Map(),
        };
        slot.probability += probability;
        slot.expected_points += probability * Number(assignment.points || 0);
        const booster = slot.boosters.get(assignment.boosterId) || {
          booster_id: assignment.boosterId,
          booster: assignment.boosterName,
          probability: 0,
          expected_points: 0,
        };
        booster.probability += probability;
        booster.expected_points += probability * Number(assignment.points || 0);
        slot.boosters.set(assignment.boosterId, booster);
        slotBuckets.set(slotKey, slot);
      });
  });

  Array.from(slotBuckets.values())
    .sort((a, b) => Number(a.match_number) - Number(b.match_number))
    .forEach((slot) => {
      const boosters = Array.from(slot.boosters.values())
        .sort((a, b) => Number(b.probability) - Number(a.probability))
        .map((booster) => ({
          ...booster,
          usage_probability: booster.probability,
          average_points: slot.probability > 0 ? booster.expected_points / slot.probability : 0,
        }));
      rows.push({
        match_number: slot.match_number,
        slot_probability: slot.probability,
        expected_points: slot.expected_points,
        average_points: slot.probability > 0 ? slot.expected_points / slot.probability : 0,
        boosters,
      });
    });
  return rows;
};

function PriceVsPointsPanel({ title, rows, slope, intercept, showTable = true, onPointClick = null }) {
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
      case "player_id_asc":
        out.sort((a, b) => Number(a.player_id) - Number(b.player_id));
        break;
      case "player_id_desc":
        out.sort((a, b) => Number(b.player_id) - Number(a.player_id));
        break;
      case "points_asc":
        out.sort((a, b) => a.points - b.points);
        break;
      case "points_desc":
        out.sort((a, b) => b.points - a.points);
        break;
      case "price_asc":
        out.sort((a, b) => a.price - b.price);
        break;
      case "price_desc":
        out.sort((a, b) => b.price - a.price);
        break;
      case "on_line_asc":
        out.sort((a, b) => a.on_line - b.on_line);
        break;
      case "on_line_desc":
        out.sort((a, b) => b.on_line - a.on_line);
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
      case "name_desc":
        out.sort((a, b) => String(b.name || "").localeCompare(String(a.name || "")));
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
                <Scatter
                  name="Players"
                  dataKey="points"
                  fill="#4fc3ff"
                  onClick={(payload) => {
                    const row = payload?.payload || payload;
                    if (row && onPointClick) onPointClick(row);
                  }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          <p className="muted">Trend line (average): points = {intercept.toFixed(2)} + {slope.toFixed(4)} x price</p>
          {showTable && (
            <>
              <div className="grid two">
                <Input label="Search Players" value={search} onChange={setSearch} placeholder="name, player id, or team id" />
              </div>
              <p className="muted">
                Showing {filteredRows.length} of {rows.length} players
              </p>
              <table>
                <thead>
                  <tr>
                    <SortHeader sortValue={sortBy} asc="name_asc" desc="name_desc" onChange={setSortBy}>Player</SortHeader>
                    <SortHeader sortValue={sortBy} asc="player_id_asc" desc="player_id_desc" onChange={setSortBy}>Player ID</SortHeader>
                    <SortHeader sortValue={sortBy} asc="price_asc" desc="price_desc" onChange={setSortBy}>Price</SortHeader>
                    <SortHeader sortValue={sortBy} asc="points_asc" desc="points_desc" defaultDirection="desc" onChange={setSortBy}>Points</SortHeader>
                    <SortHeader sortValue={sortBy} asc="on_line_asc" desc="on_line_desc" defaultDirection="desc" onChange={setSortBy}>On Line</SortHeader>
                    <SortHeader sortValue={sortBy} asc="distance_asc" desc="distance_desc" defaultDirection="desc" onChange={setSortBy}>Distance</SortHeader>
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
            </>
          )}
        </div>
      )}
    </div>
  );
}

function PointSourcesModal({ player, teamLookup, onClose }) {
  if (!player) return null;
  const hasConcreteBreakdown = Array.isArray(player.point_breakdown) && player.point_breakdown.length > 0;
  const sourceValue = (row, key) => {
    if (key === "total") {
      const total = ["rating", "win", "role"].reduce((sum, part) => sum + sourceValue(row, part), 0);
      if (row?.components_available === false && Number.isFinite(Number(row?.points))) return Number(row.points);
      return total;
    }
    const candidates = {
      rating: [row?.rating, row?.rating_ev, row?.rating_points_total],
      win: [row?.win, row?.win_ev, row?.win_points_total],
      role: [row?.role, row?.role_ev, row?.role_points_total],
      booster: [row?.booster, row?.booster_ev, row?.booster_points_total],
    }[key] || [];
    const value = candidates.map((v) => Number(v)).find((v) => Number.isFinite(v));
    return Number.isFinite(value) ? value : 0;
  };

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h3>{player.name || `Player ${player.player_id}`} Point Sources</h3>
          <button className="close" onClick={onClose}>
            &times;
          </button>
        </header>
        <div className="modal-body">
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>Points</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Rating</td><td>{sourceValue(player, "rating").toFixed(2)}</td></tr>
              <tr><td>Win</td><td>{sourceValue(player, "win").toFixed(2)}</td></tr>
              <tr><td>Role</td><td>{sourceValue(player, "role").toFixed(2)}</td></tr>
              <tr><td>Booster</td><td>{hasConcreteBreakdown ? sourceValue(player, "booster").toFixed(2) : "Unknown"}</td></tr>
              <tr>
                <td><strong>Total</strong></td>
                <td><strong>{sourceValue(player, "total").toFixed(2)}</strong></td>
              </tr>
            </tbody>
          </table>
          {hasConcreteBreakdown && (
            <div className="stack">
              <h4>Match Sources</h4>
              <table>
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Opponent</th>
                    <th>Rating</th>
                    <th>Win</th>
                    <th>Role</th>
                    <th>Booster</th>
                    <th>Total</th>
                  </tr>
                </thead>
                <tbody>
                  {player.point_breakdown.map((row, idx) => {
                    const opponentId = Number(row.opponent_team_id || 0);
                    const matchLabel = row.match_number ? `M${row.match_number} ${row.match_type || ""}` : row.match_type || "Adjustment";
                    const opponentLabel = opponentId > 0 ? `${teamLookup[opponentId] || opponentId} (#${row.opponent_rank ?? "-"})` : row.note || "-";
                    const roleName = row.role_id == null ? "Stored role" : roleLabel(row.role_id);
                    const boosterName = row.booster_name || (row.booster_slot ? `Booster slot ${row.booster_slot}` : "None");
                    return (
                      <tr key={`${row.match_number || "adj"}-${idx}`}>
                        <td>{matchLabel}</td>
                        <td>{opponentLabel}</td>
                        <td>
                          {Number(row.rating_points || 0).toFixed(2)}
                          {row.rating_used != null && <div className="muted">rating {Number(row.rating_used || 0).toFixed(2)}</div>}
                        </td>
                        <td>
                          {Number(row.win_points || 0).toFixed(2)}
                          <div className="muted">{row.did_win ? "Win" : "Loss"} {Number((row.win_probability || 0) * 100).toFixed(1)}%</div>
                        </td>
                        <td>
                          {Number(row.role_points || 0).toFixed(2)}
                          <div className="muted">
                            {roleName} major {Number((row.role_major_pct || 0) * 100).toFixed(1)}%, minor {Number((row.role_minor_pct || 0) * 100).toFixed(1)}%
                          </div>
                        </td>
                        <td>
                          {Number(row.booster_points || 0).toFixed(2)}
                          <div className="muted">
                            {boosterName} {Number((row.booster_trigger_rate || 0) * 100).toFixed(1)}%
                          </div>
                        </td>
                        <td>{Number(row.total_points || 0).toFixed(2)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          {!hasConcreteBreakdown && Array.isArray(player.booster_odds) && player.booster_odds.length > 0 && (
            <div className="stack">
              <h4>Booster Odds By Match Slot</h4>
              <table>
                <thead>
                  <tr>
                    <th>Booster</th>
                    <th>Match</th>
                    <th>Record</th>
                    <th>Format</th>
                    <th>Slot %</th>
                    <th>Trigger %</th>
                    <th>EV</th>
                  </tr>
                </thead>
                <tbody>
                  {player.booster_odds.map((assignment, idx) => (
                    <tr key={`${assignment.booster_id || "booster"}-${assignment.match_number || idx}-${idx}`}>
                      <td>{assignment.booster || `Booster ${assignment.booster_id}`}</td>
                      <td>{assignment.match_number || "-"}</td>
                      <td>{assignment.record || "-"}</td>
                      <td>{assignment.match_format || "-"}</td>
                      <td>{(Number(assignment.slot_probability || 0) * 100).toFixed(1)}%</td>
                      <td>{(Number(assignment.adjusted_trigger_probability || 0) * 100).toFixed(1)}%</td>
                      <td>{Number(assignment.expected_points || 0).toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {Array.isArray(player.playoff_booster_usage) && player.playoff_booster_usage.length > 0 && (
            <div className="stack">
              <h4>Projected Booster Usage</h4>
              <table>
                <thead>
                  <tr>
                    <th>Match</th>
                    <th>Reach %</th>
                    <th>Boosters</th>
                    <th>Avg Points If Reached</th>
                    <th>EV</th>
                  </tr>
                </thead>
                <tbody>
                  {player.playoff_booster_usage.map((slot) => (
                    <tr key={`playoff-booster-${slot.match_number}`}>
                      <td>M{slot.match_number}</td>
                      <td>{(Number(slot.slot_probability || 0) * 100).toFixed(1)}%</td>
                      <td>
                        {(slot.boosters || []).map((booster, idx) => (
                          <div key={`${slot.match_number}-${booster.booster_id}-${idx}`}>
                            {booster.booster} {(Number(booster.usage_probability || 0) * 100).toFixed(1)}%
                          </div>
                        ))}
                      </td>
                      <td>{Number(slot.average_points || 0).toFixed(2)}</td>
                      <td>{Number(slot.expected_points || 0).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {player.components_available === false && (
            <p className="muted">
              This row was generated before source components were saved. Re-run the relevant simulation to see the full source split.
            </p>
          )}
        </div>
      </div>
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
  const [sourcePlayerBreakdown, setSourcePlayerBreakdown] = useState(null);
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
  const openSourceBreakdown = (row) => {
    if (!row) return;
    setSourcePlayerBreakdown({
      ...row,
      points: Number(row.points ?? row.total ?? row.total_points ?? 0),
      rating: Number(row.rating ?? row.rating_points_total ?? 0),
      win: Number(row.win ?? row.win_points_total ?? 0),
      role: Number(row.role ?? row.role_points_total ?? 0),
      booster: Number(row.booster ?? row.booster_points_total ?? 0),
      components_available: true,
    });
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
      bo3_mode: "elim_qual",
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
          <div className="field">
            <span>Match Format</span>
            <div className="pill">CS2 Swiss: BO3 on qualification/elimination</div>
          </div>
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
                          <button
                            className="inline-link-btn"
                            onClick={() =>
                              openSourceBreakdown({
                                player_id: Number(pid),
                                name: playerLookup[Number(pid)] || pid,
                                team_id: Number(tid),
                                ...comps,
                              })
                            }
                          >
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
      {sourcePlayerBreakdown && (
        <PointSourcesModal
          player={sourcePlayerBreakdown}
          teamLookup={teamLookup}
          onClose={() => setSourcePlayerBreakdown(null)}
        />
      )}
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
  const [sourcePlayerBreakdown, setSourcePlayerBreakdown] = useState(null);
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
  const openSourceBreakdown = (row) => {
    if (!row) return;
    setSourcePlayerBreakdown({
      ...row,
      points: Number(row.points ?? row.total_ev ?? row.mode_score ?? 0),
      rating: Number(row.rating ?? row.rating_ev ?? 0),
      win: Number(row.win ?? row.win_ev ?? 0),
      role: Number(row.role ?? row.role_ev ?? 0),
      booster: Number(row.booster ?? row.booster_ev ?? 0),
      components_available: true,
    });
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
              <div className="grid two top5-controls">
                <Input label="Search Combos" value={comboSearch} onChange={setComboSearch} placeholder="Player/team name or id" />
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
                {Array.isArray(team.booster_assignments) && team.booster_assignments.length > 0 && (
                  <p className="muted">
                    Booster EV {Number(team.booster_ev || 0).toFixed(2)} | Avg/player{" "}
                    {Number(team.average_booster_ev_per_player || 0).toFixed(2)}
                  </p>
                )}
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
                          <button
                            className="inline-link-btn"
                            onClick={() =>
                              openSourceBreakdown({
                                ...p,
                                booster_odds: (team.booster_assignments || []).filter(
                                  (assignment) => Number(assignment.player_id) === Number(p.player_id)
                                ),
                              })
                            }
                          >
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
                {Array.isArray(team.booster_assignments) && team.booster_assignments.length > 0 && (
                  <table>
                    <thead>
                      <tr>
                        <th>Booster</th>
                        <th>Player</th>
                        <th>Match</th>
                        <th>Record</th>
                        <th>Format</th>
                        <th>Slot %</th>
                        <th>Trigger %</th>
                        <th>EV</th>
                      </tr>
                    </thead>
                    <tbody>
                      {team.booster_assignments.map((assignment, assignmentIdx) => (
                        <tr key={`booster-assignment-${idx}-${assignment.booster_id}-${assignmentIdx}`}>
                          <td>{assignment.booster}</td>
                          <td>{assignment.player}</td>
                          <td>{assignment.match_number}</td>
                          <td>{assignment.record}</td>
                          <td>{assignment.match_format}</td>
                          <td>{(Number(assignment.slot_probability || 0) * 100).toFixed(1)}%</td>
                          <td>{(Number(assignment.adjusted_trigger_probability || 0) * 100).toFixed(1)}%</td>
                          <td>{Number(assignment.expected_points || 0).toFixed(3)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
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
                  <SortHeader sortValue={sortKey} asc="ev_asc" desc="ev_desc" defaultDirection="desc" onChange={setSortKey}>EV</SortHeader>
                  <SortHeader sortValue={sortKey} asc="cost_asc" desc="cost_desc" onChange={setSortKey}>Cost</SortHeader>
                  <SortHeader sortValue={sortKey} asc="cpp_asc" desc="cpp_desc" defaultDirection="desc" onChange={setSortKey}>Value</SortHeader>
                  <th>Players</th>
                </tr>
              </thead>
              <tbody>
                {pageTeams.map((team, idx) => (
                  <tr key={idx + page * 200}>
                    <td>{idx + 1 + page * 200}</td>
                    <td>{team.total_ev.toFixed(2)}</td>
                    <td>{team.cost}</td>
                    <td>{(team.total_ev / (team.cost || 1)).toFixed(4)}</td>
                    <td>
                      {(team.players || []).map((p, playerIdx) => (
                        <span key={`${idx}-${p.player_id}`}>
                          {playerIdx > 0 && ", "}
                          <button
                            className="inline-link-btn"
                            onClick={() =>
                              openSourceBreakdown({
                                ...p,
                                booster_odds: (team.booster_assignments || []).filter(
                                  (assignment) => Number(assignment.player_id) === Number(p.player_id)
                                ),
                              })
                            }
                          >
                            {p.name}
                          </button>
                          {` (${teamLookup[p.team_id] || p.team_id}, ${roleLabel(p.role_name)})`}
                        </span>
                      ))}
                    </td>
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
        {sourcePlayerBreakdown && (
          <PointSourcesModal
            player={sourcePlayerBreakdown}
            teamLookup={teamLookup}
            onClose={() => setSourcePlayerBreakdown(null)}
          />
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
  const [sharedComboCount, setSharedComboCount] = useState(0);
  const [sharedCombosUpdatedAt, setSharedCombosUpdatedAt] = useState("");
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
  const [playoffTopSubtab, setPlayoffTopSubtab] = useState("average");
  const [playoffBestMode, setPlayoffBestMode] = useState("average");
  const [completedBracket, setCompletedBracket] = useState({
    qf: ["", "", "", ""],
    sf: ["", ""],
    final: "",
    third: "",
  });
  const [completedBracketResult, setCompletedBracketResult] = useState(null);
  const [completedBracketMessage, setCompletedBracketMessage] = useState("");
  const [completedBracketUpdatedAt, setCompletedBracketUpdatedAt] = useState("");
  const [showCompletedBracketGraph, setShowCompletedBracketGraph] = useState(false);
  const [completedPlayerBreakdown, setCompletedPlayerBreakdown] = useState(null);
  const [processedSims, setProcessedSims] = useState(0);
  const [totalSims, setTotalSims] = useState(0);
  const [processedCombos, setProcessedCombos] = useState(0);
  const [totalCombos, setTotalCombos] = useState(0);
  const [topEtaSeconds, setTopEtaSeconds] = useState(null);
  const [comboPhase, setComboPhase] = useState("");
  const [completedEtaSeconds, setCompletedEtaSeconds] = useState(null);
  const [etaSeconds, setEtaSeconds] = useState(null);
  const [runMessage, setRunMessage] = useState("");
  const playoffPollingRef = useRef(false);
  const normalizeTeamName = (name) => String(name || "").trim().toLowerCase();
  const playerLookup = useMemo(() => {
    const m = {};
    players.forEach((p) => (m[p.player_id] = p.name));
    return m;
  }, [players]);
  const playerById = useMemo(() => {
    const m = {};
    players.forEach((p) => {
      m[Number(p.player_id)] = p;
    });
    return m;
  }, [players]);
  const playerTeamById = useMemo(() => {
    const m = {};
    teams.forEach((team) => {
      [team.player1_id, team.player2_id, team.player3_id, team.player4_id, team.player5_id].forEach((pid) => {
        const id = Number(pid);
        if (Number.isFinite(id) && id > 0) m[id] = Number(team.team_id);
      });
    });
    return m;
  }, [teams]);
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
  const playoffTeamOptions = useMemo(
    () => [
      { value: "", label: "Select team" },
      ...filteredTeams.map((t) => ({ value: String(t.team_id), label: `${t.name} (${t.team_id})` })),
    ],
    [filteredTeams]
  );
  const teamInitials = (teamId) => {
    const name = teamLookup[Number(teamId)] || "";
    const parts = String(name).trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
    return String(name || "?").slice(0, 2).toUpperCase();
  };
  const BracketTeamRow = ({ slotIndex, placeholder, muted = false }) => {
    const selectedTeamId = slotIndex !== null && slotIndex !== undefined ? slots[slotIndex] : "";
    const hasTeam = Boolean(selectedTeamId);
    return (
      <div className={`playoff-team-row ${muted ? "muted" : ""}`}>
        <span className={`playoff-team-badge ${hasTeam ? "" : "empty"}`}>{hasTeam ? teamInitials(selectedTeamId) : "?"}</span>
        {slotIndex !== null && slotIndex !== undefined ? (
          <select value={selectedTeamId} onChange={(e) => setSlot(slotIndex, e.target.value)} disabled={busy}>
            {playoffTeamOptions.map((option) => (
              <option key={option.value || "empty"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : (
          <span className="playoff-team-tbd">{placeholder || "TBD"}</span>
        )}
      </div>
    );
  };
  const CompletedBracketTeamRow = ({ teamId, selected, onSelect, placeholder = "TBD", muted = false }) => {
    const hasTeam = Boolean(teamId);
    return (
      <button
        type="button"
        className={`playoff-team-row completed-pick ${muted ? "muted" : ""} ${selected ? "active" : ""}`}
        onClick={() => hasTeam && onSelect(String(teamId))}
        disabled={!hasTeam}
      >
        <span className={`playoff-team-badge ${hasTeam ? "" : "empty"}`}>{hasTeam ? teamInitials(teamId) : "?"}</span>
        <span>{hasTeam ? teamLookup[Number(teamId)] || `Team ${teamId}` : placeholder}</span>
      </button>
    );
  };
  const BracketMatchCard = ({ title, meta = "BO3", rows, className = "" }) => (
    <div className={`playoff-match-card ${className}`}>
      <div className="playoff-match-head">
        <strong>{title}</strong>
        <span>{meta}</span>
      </div>
      <div className="playoff-match-teams">{rows}</div>
    </div>
  );
  const setCompletedPick = (round, index, value) => {
    setCompletedBracket((prev) => {
      const next = { ...prev, qf: [...prev.qf], sf: [...prev.sf] };
      if (round === "qf") {
        next.qf[index] = value;
        if (index < 2) next.sf[0] = "";
        else next.sf[1] = "";
        next.final = "";
        next.third = "";
      } else if (round === "sf") {
        next.sf[index] = value;
        next.final = "";
        next.third = "";
      } else if (round === "final") {
        next.final = value;
      } else if (round === "third") {
        next.third = value;
      }
      return next;
    });
    setCompletedBracketResult(null);
    setCompletedBracketMessage("");
  };
  const hydrateCompletedBracketFromBracket = (bracket) => {
    if (!bracket) return;
    const quarters = bracket.quarters || [];
    const semis = bracket.semis || [];
    const finals = bracket.final || [];
    const thirdPlace = bracket.third_place || [];
    const qf = quarters.slice(0, 4).map((row) => (row?.winner ? String(row.winner) : ""));
    const sf = semis.slice(0, 2).map((row) => (row?.winner ? String(row.winner) : ""));
    const finalWinner = finals[0]?.winner ? String(finals[0].winner) : "";
    const thirdWinner = thirdPlace[0]?.winner ? String(thirdPlace[0].winner) : "";
    if (qf.some(Boolean) || sf.some(Boolean) || finalWinner || thirdWinner) {
      setCompletedBracket({
        qf: [...qf, "", "", "", ""].slice(0, 4),
        sf: [...sf, ""].slice(0, 2),
        final: finalWinner,
        third: thirdWinner,
      });
    }
  };
  const openScoringBreakdown = (row) => {
    if (!row) return;
    const pid = Number(row.player_id);
    setCompletedPlayerBreakdown({
      ...row,
      player_id: Number.isFinite(pid) ? pid : row.player_id,
      name: row.name || playerLookup[pid] || `Player ${row.player_id || ""}`,
      team_id: Number(row.team_id || playerTeamById[pid] || 0),
      points: Number(row.points ?? row.total_ev ?? row.mode_score ?? 0),
    });
  };
  const openProjectedRosterBreakdown = (player, rosterPlayers) => {
    openScoringBreakdown({
      ...player,
      playoff_booster_usage: aggregatePlayoffBoosterUsage(player, rosterPlayers || [], results?.outcomes || []),
    });
  };
  const completedBracketDerived = useMemo(() => {
    const isPickedFrom = (value, ids) => Boolean(value) && ids.some((id) => String(id) === String(value));
    const qfPairs = [
      [slots[0], slots[1]],
      [slots[2], slots[3]],
      [slots[4], slots[5]],
      [slots[6], slots[7]],
    ];
    const sfPairs = [
      [completedBracket.qf[0], completedBracket.qf[1]],
      [completedBracket.qf[2], completedBracket.qf[3]],
    ];
    const finalPair = [completedBracket.sf[0], completedBracket.sf[1]];
    const thirdPair = [
      sfPairs[0].find((id) => id && String(id) !== String(completedBracket.sf[0])) || "",
      sfPairs[1].find((id) => id && String(id) !== String(completedBracket.sf[1])) || "",
    ];
    const complete =
      completedBracket.qf.every((pick, idx) => isPickedFrom(pick, qfPairs[idx])) &&
      completedBracket.sf.every((pick, idx) => isPickedFrom(pick, sfPairs[idx])) &&
      isPickedFrom(completedBracket.final, finalPair) &&
      (!hasThirdPlaceDecider || isPickedFrom(completedBracket.third, thirdPair));
    return { qfPairs, sfPairs, finalPair, thirdPair, complete };
  }, [slots, completedBracket, hasThirdPlaceDecider]);
  const runCompletedBracket = async () => {
    setBusy(true);
    setCompletedBracketMessage("");
    setCompletedBracketResult(null);
    setCompletedBracketUpdatedAt("");
    setProcessedCombos(0);
    setTotalCombos(0);
    setCompletedEtaSeconds(null);
    try {
      const start = await api.post("/playoff/best-team/bracket-from-latest/start", {
        qf_winners: completedBracket.qf.map((id) => Number(id)),
        sf_winners: completedBracket.sf.map((id) => Number(id)),
        final_winner: Number(completedBracket.final),
        third_place_winner: hasThirdPlaceDecider ? Number(completedBracket.third) : 0,
        include_player_ids: Array.from(effectiveAppliedFilters.include),
        exclude_player_ids: Array.from(effectiveAppliedFilters.exclude),
      });
      if (start?.detail || start?.error) {
        setCompletedBracketMessage(String(start.detail || start.error));
        return;
      }
      const jobId = start?.job_id;
      if (!jobId) {
        setCompletedBracketMessage("Failed to start completed bracket job.");
        return;
      }
      if (start?.reused) {
        setCompletedBracketMessage("A completed bracket optimizer is already running. Reusing that job.");
      }
      let data = null;
      let done = false;
      const startedAtMs = Date.now();
      while (!done) {
        const status = await api.get(`/playoff/best-team/bracket-from-latest/job/${jobId}`);
        if (status?.detail || status?.error) {
          setCompletedBracketMessage(String(status.detail || status.error));
          return;
        }
        const processed = Number(status.processed_combinations || 0);
        const total = Number(status.total_combinations || 0);
        setProcessedCombos(processed);
        setTotalCombos(total);
        if (processed > 0 && total > processed) {
          const elapsedSec = Math.max(0.001, (Date.now() - startedAtMs) / 1000);
          const rate = processed / elapsedSec;
          if (rate > 0) setCompletedEtaSeconds((total - processed) / rate);
        } else if (total > 0 && processed >= total) {
          setCompletedEtaSeconds(0);
        }
        if (status.status === "failed") {
          setCompletedBracketMessage(status.error || "Completed bracket evaluation failed.");
          return;
        }
        if (status.status === "completed") {
          data = status.result || {};
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
      if (data?.detail || data?.error) {
        setCompletedBracketMessage(String(data.detail || data.error));
        return;
      }
      const outcomeCount = Number(data?.outcomes_count || 0);
      const playerValueCount = Array.isArray(data?.player_values) ? data.player_values.length : 0;
      if (outcomeCount <= 0 || playerValueCount === 0) {
        setCompletedBracketMessage(
          "Completed bracket result had no stored outcome/player values. Re-run Playoff Bracket, then try this bracket again."
        );
        return;
      }
      setCompletedBracketResult(data);
      setCompletedBracketUpdatedAt(new Date().toISOString());
    } catch (e) {
      setCompletedBracketMessage(e?.message || "Failed to evaluate completed bracket.");
    } finally {
      setBusy(false);
      setComboPhase("");
    }
  };
  const playoffBestModeLabel = {
    average: "Best Average Value",
    single_outcome: "Highest Single-Outcome Ceiling",
    most_outcomes: "Best In Most Outcomes",
  }[playoffBestMode] || "Best Average Value";
  const playoffTopSubtabs = [
    { key: "average", label: "Average Player Value" },
    { key: "single_outcome", label: "Best Single Outcome" },
    { key: "most_outcomes", label: "Most Winning Outcomes" },
    { key: "completed", label: "Completed Bracket" },
  ];
  const setPlayoffTopMode = (key) => {
    setPlayoffTopSubtab(key);
    if (key !== "completed") {
      setPlayoffBestMode(key);
      setTopMessage("");
    }
  };
  const playoffTeamMetric = (team) => {
    if (playoffBestMode === "single_outcome") return Number(team?.ceiling_points || 0);
    if (playoffBestMode === "most_outcomes") return Number(team?.outcome_wins || 0);
    return Number(team?.average_ev ?? team?.total_ev ?? 0);
  };
  const playoffPlayerModeScore = (player) => {
    if (playoffBestMode === "single_outcome") return Number(player?.ceiling_score ?? player?.mode_score ?? player?.total_ev ?? 0);
    return Number(player?.mode_score ?? player?.total_ev ?? 0);
  };
  const playoffOutcomeCount = Number(results?.outcomes_count || (hasThirdPlaceDecider ? 256 : 128));
  const formatOutcomeWins = (value) => {
    const n = Number(value || 0);
    if (!Number.isFinite(n)) return "0";
    return Math.abs(n - Math.round(n)) < 1e-9 ? String(Math.round(n)) : n.toFixed(1);
  };
  const playoffTeamMetricLabel = (team) => {
    if (playoffBestMode === "single_outcome") {
      return `Ceiling ${Number(team?.ceiling_points || 0).toFixed(2)} | Outcome prob ${(
        Number(team?.ceiling_probability || 0) * 100
      ).toFixed(1)}%`;
    }
    if (playoffBestMode === "most_outcomes") {
      return `Outcome wins ${formatOutcomeWins(team?.outcome_wins)} / ${playoffOutcomeCount.toLocaleString()} | Win prob ${(
        Number(team?.outcome_win_probability || 0) * 100
      ).toFixed(1)}%`;
    }
    return `EV ${Number(team?.total_ev || 0).toFixed(2)}`;
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
    hydrateCompletedBracketFromBracket(data.results?.bracket);
    setUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  const loadLatestCompletedBracket = async () => {
    const data = await api.get("/playoff/best-team/bracket-from-latest/latest");
    if (!data?.exists) return;
    const payload = data.payload || {};
    const savedQf = (payload.qf_winners || []).slice(0, 4).map((id) => String(id || ""));
    const savedSf = (payload.sf_winners || []).slice(0, 2).map((id) => String(id || ""));
    setCompletedBracket({
      qf: [...savedQf, "", "", "", ""].slice(0, 4),
      sf: [...savedSf, ""].slice(0, 2),
      final: payload.final_winner ? String(payload.final_winner) : "",
      third: payload.third_place_winner ? String(payload.third_place_winner) : "",
    });
    setCompletedBracketUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  const loadLatestSharedCombinations = async () => {
    const data = await api.get("/playoff/best-team/from-latest/latest");
    if (!data?.exists) return;
    setBaseTeams([]);
    setSharedComboCount(Number(data.total_teams || 0));
    setSharedCombosUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  useEffect(() => {
    loadEventsForPlayoff();
    loadLatestPlayoff();
    loadLatestCompletedBracket();
    loadLatestSharedCombinations();
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

  const runSharedCombinations = async () => {
    setBusy(true);
    setTopMessage("");
    setAllTeams(null);
    setBaseTeams(null);
    setSharedComboCount(0);
    setPage(0);
    setProcessedCombos(0);
    setTotalCombos(0);
    setTopEtaSeconds(null);
    setComboPhase("queued");
    try {
      const start = await api.post("/playoff/best-team/from-latest/start", {
        include_player_ids: Array.from(effectiveAppliedFilters.include),
        exclude_player_ids: Array.from(effectiveAppliedFilters.exclude),
        mode: "most_outcomes",
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
        setTopMessage("Failed to start combination generation job.");
        return;
      }
      if (start?.reused) {
        setTopMessage("A combination job is already running. Reusing that job.");
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
        setComboPhase(String(status.phase || status.status || ""));
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
          const latest = await api.get("/playoff/best-team/from-latest/latest");
          setBaseTeams([]);
          setSharedComboCount(Number(latest?.total_teams || 0));
          setSharedCombosUpdatedAt(latest?.updated_at ? new Date(Number(latest.updated_at) * 1000).toISOString() : new Date().toISOString());
          await querySharedCombinations(0);
          setTopMessage("");
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 200));
      }
    } finally {
      setBusy(false);
    }
  };

  const resetStoredPlayoff = async () => {
    await api.delete("/playoff/latest");
    setLatestPayload(null);
    setResults(null);
    setUpdatedAt("");
    setTopTeams(null);
    setAllTeams(null);
    setBaseTeams(null);
    setSharedCombosUpdatedAt("");
    setSharedComboCount(0);
    setCompletedBracketResult(null);
    setCompletedBracketMessage("");
    setCompletedBracketUpdatedAt("");
    setShowCompletedBracketGraph(false);
    setCompletedPlayerBreakdown(null);
    setFilteredCount(0);
    setProcessedCombos(0);
    setTotalCombos(0);
    setTopEtaSeconds(null);
    setComboPhase("");
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
    querySharedCombinations(page);
  }, [baseTeams, effectiveAppliedFilters, comboSearch, sortKey, playoffBestMode]);

  useEffect(() => {
    if (!baseTeams) return;
    if (playoffTopSubtab === "completed" && !completedBracketDerived.complete) {
      setCompletedBracketResult(null);
      return;
    }
    querySharedCombinations(0);
  }, [playoffTopSubtab, completedBracket, completedBracketDerived.complete, hasThirdPlaceDecider]);

  const querySharedCombinations = async (nextPage = 0) => {
    if (!baseTeams) return;
    try {
      const endpoint = playoffTopSubtab === "completed"
        ? "/playoff/best-team/from-latest/completed-query"
        : "/playoff/best-team/from-latest/query";
      const body = {
        mode: playoffBestMode,
        include_player_ids: Array.from(effectiveAppliedFilters.include),
        exclude_player_ids: Array.from(effectiveAppliedFilters.exclude),
        search: comboSearch,
        sort: sortKey,
        page: nextPage,
        page_size: 200,
      };
      if (playoffTopSubtab === "completed") {
        if (!completedBracketDerived.complete) return;
        body.qf_winners = completedBracket.qf.map((id) => Number(id));
        body.sf_winners = completedBracket.sf.map((id) => Number(id));
        body.final_winner = Number(completedBracket.final);
        body.third_place_winner = hasThirdPlaceDecider ? Number(completedBracket.third) : 0;
      }
      const data = await api.post(endpoint, body);
      setTopTeams(data.top_teams || []);
      setAllTeams(data.page_teams || []);
      setFilteredCount(Number(data.filtered_count || 0));
      setSharedComboCount(Number(data.total_teams || sharedComboCount || 0));
      setPage(Number(data.page || nextPage || 0));
      if (playoffTopSubtab === "completed") {
        setCompletedBracketResult(data);
      }
    } catch (e) {
      setTopMessage(e?.message || "Failed to load saved combinations.");
    }
  };

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
    const sorted = [...filtered].sort((a, b) => {
      if (sortKey === "cost_asc") return Number(a.cost || 0) - Number(b.cost || 0);
      if (sortKey === "cost_desc") return Number(b.cost || 0) - Number(a.cost || 0);
      if (sortKey === "cpp_asc") return playoffTeamMetric(a) / (Number(a.cost || 1)) - playoffTeamMetric(b) / (Number(b.cost || 1));
      if (sortKey === "cpp_desc") return playoffTeamMetric(b) / (Number(b.cost || 1)) - playoffTeamMetric(a) / (Number(a.cost || 1));
      if (sortKey === "ev_asc") return playoffTeamMetric(a) - playoffTeamMetric(b);
      return playoffTeamMetric(b) - playoffTeamMetric(a);
    });
    setAllTeams(sorted);
    setTopTeams(sorted.slice(0, 10));
    setFilteredCount(sorted.length);
    setPage(0);
  };
  const playoffPlayerValueData = useMemo(
    () => buildPlayerValueRowsFromSimulation(results, players),
    [results, players]
  );
  const completedBracketSharedResult = useMemo(() => {
    if (!results || !baseTeams || !completedBracketDerived.complete) return null;
    const qf = completedBracket.qf.map((id) => Number(id));
    const sf = completedBracket.sf.map((id) => Number(id));
    const finalWinner = Number(completedBracket.final);
    const thirdWinner = Number(completedBracket.third || 0);
    const selectedOutcome = (results.outcomes || []).find((outcome) => {
      const bracket = outcome?.bracket || {};
      const quarters = bracket.quarters || [];
      const semis = bracket.semis || [];
      const finals = bracket.final || [];
      const third = bracket.third_place || [];
      if (quarters.length < 4 || semis.length < 2 || finals.length < 1) return false;
      if (quarters.slice(0, 4).some((row, idx) => Number(row?.winner || 0) !== qf[idx])) return false;
      if (semis.slice(0, 2).some((row, idx) => Number(row?.winner || 0) !== sf[idx])) return false;
      if (Number(finals[0]?.winner || 0) !== finalWinner) return false;
      if (third.length > 0) return thirdWinner > 0 && Number(third[0]?.winner || 0) === thirdWinner;
      return thirdWinner <= 0;
    });
    if (!selectedOutcome) return null;

    const scoresByPid = {};
    Object.entries(selectedOutcome.players || {}).forEach(([pid, score]) => {
      scoresByPid[Number(pid)] = Number(score || 0);
    });
    const componentsByPid = {};
    Object.entries(selectedOutcome.player_components || {}).forEach(([pid, comps]) => {
      componentsByPid[Number(pid)] = comps || {};
    });
    const playerValues = Object.entries(scoresByPid)
      .map(([pidRaw, score]) => {
        const pid = Number(pidRaw);
        const p = playerById[pid] || {};
        const comps = componentsByPid[pid] || {};
        return {
          player_id: pid,
          name: p.name || playerLookup[pid] || `Player ${pid}`,
          team_id: Number(playerTeamById[pid] || 0),
          price: Number(p.price || 0),
          points: Number(score || 0),
          rating: Number(comps.rating || 0),
          win: Number(comps.win || 0),
          role: Number(comps.role || 0),
          booster: Number(comps.booster || 0),
          components_available: Boolean(componentsByPid[pid]),
        };
      })
      .sort((a, b) => b.points - a.points);

    let teamsForBracket = applyFilters(baseTeams || [], effectiveAppliedFilters.include, effectiveAppliedFilters.exclude);
    const q = comboSearch.trim().toLowerCase();
    if (q) {
      teamsForBracket = teamsForBracket.filter((team) =>
        (team.players || []).some((p) => {
          const name = String(p.name || "").toLowerCase();
          const teamName = String(teamLookup[p.team_id] || "").toLowerCase();
          return name.includes(q) || teamName.includes(q) || String(p.player_id).includes(q) || String(p.team_id).includes(q);
        })
      );
    }
    const scoredTeams = teamsForBracket
      .map((team) => {
        const playersForBracket = (team.players || []).map((p) => {
          const score = Number(scoresByPid[Number(p.player_id)] || 0);
          return { ...p, mode_score: score, total_ev: score };
        });
        const bracketScore = playersForBracket.reduce((sum, p) => sum + Number(p.mode_score || 0), 0);
        return { ...team, players: playersForBracket, total_ev: bracketScore, bracket_score: bracketScore };
      })
      .sort((a, b) => Number(b.bracket_score || 0) - Number(a.bracket_score || 0));

    return {
      bracket_probability: Number(selectedOutcome.probability || 0),
      bracket: selectedOutcome.bracket || {},
      outcomes_count: Number(results.outcomes_count || (results.outcomes || []).length || 0),
      player_values: playerValues,
      top_teams: scoredTeams.slice(0, 10),
      all_teams: scoredTeams,
      mode: "completed_bracket",
    };
  }, [
    results,
    baseTeams,
    completedBracket,
    completedBracketDerived.complete,
    effectiveAppliedFilters,
    comboSearch,
    playerById,
    playerLookup,
    playerTeamById,
    teamLookup,
    applyFilters,
  ]);
  const activeCompletedBracketResult = completedBracketResult;
  const completedBracketValueData = useMemo(() => {
    const rows = (activeCompletedBracketResult?.player_values || [])
      .map((row) => ({
        player_id: Number(row.player_id),
        name: row.name || `Player ${row.player_id}`,
        team_id: Number(row.team_id || 0),
        price: Number(row.price || 0),
        points: Number(row.points || 0),
        rating: Number(row.rating || 0),
        win: Number(row.win || 0),
        role: Number(row.role || 0),
        booster: Number(row.booster || 0),
      }))
      .filter((row) => Number.isFinite(row.price) && Number.isFinite(row.points))
      .sort((a, b) => b.points - a.points);
    if (rows.length === 0) return { rows: [], slope: 0, intercept: 0 };
    const xMean = rows.reduce((sum, row) => sum + row.price, 0) / rows.length;
    const yMean = rows.reduce((sum, row) => sum + row.points, 0) / rows.length;
    const num = rows.reduce((sum, row) => sum + (row.price - xMean) * (row.points - yMean), 0);
    const den = rows.reduce((sum, row) => sum + (row.price - xMean) ** 2, 0);
    const slope = den > 0 ? num / den : 0;
    const intercept = yMean - slope * xMean;
    return {
      rows: rows.map((row) => {
        const onLine = intercept + slope * row.price;
        return { ...row, on_line: onLine, distance: row.points - onLine };
      }),
      slope,
      intercept,
    };
  }, [activeCompletedBracketResult?.player_values]);
  const completedBreakdownValue = (row, key) => {
    if (key === "total") {
      const total = ["rating", "win", "role"].reduce((sum, part) => sum + completedBreakdownValue(row, part), 0);
      if (row?.components_available === false && Number.isFinite(Number(row?.points))) return Number(row.points);
      return total;
    }
    const candidates = {
      rating: [row?.rating, row?.rating_ev],
      win: [row?.win, row?.win_ev],
      role: [row?.role, row?.role_ev],
      booster: [row?.booster, row?.booster_ev],
    }[key] || [];
    const value = candidates.map((v) => Number(v)).find((v) => Number.isFinite(v));
    return Number.isFinite(value) ? value : 0;
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
            <div className="playoff-bracket-shell">
              <div className="playoff-bracket-column qf">
                <h3>Quarter-finals</h3>
                {[0, 2, 4, 6].map((slotStart, matchIdx) => (
                  <BracketMatchCard
                    key={`qf-${matchIdx}`}
                    title={`QF ${matchIdx + 1}`}
                    className="connector-out"
                    rows={
                      <>
                        <BracketTeamRow slotIndex={slotStart} />
                        <BracketTeamRow slotIndex={slotStart + 1} />
                      </>
                    }
                  />
                ))}
              </div>
              <div className="playoff-bracket-column sf">
                <h3>Semi-finals</h3>
                <BracketMatchCard
                  title="SF 1"
                  className="connector-in connector-out"
                  rows={
                    <>
                      <BracketTeamRow placeholder="Winner QF 1" muted />
                      <BracketTeamRow placeholder="Winner QF 2" muted />
                    </>
                  }
                />
                <BracketMatchCard
                  title="SF 2"
                  className="connector-in connector-out"
                  rows={
                    <>
                      <BracketTeamRow placeholder="Winner QF 3" muted />
                      <BracketTeamRow placeholder="Winner QF 4" muted />
                    </>
                  }
                />
              </div>
              <div className="playoff-bracket-column final">
                <h3>Grand final</h3>
                <BracketMatchCard
                  title="Final"
                  meta="BO5"
                  className="connector-in"
                  rows={
                    <>
                      <BracketTeamRow placeholder="Winner SF 1" muted />
                      <BracketTeamRow placeholder="Winner SF 2" muted />
                    </>
                  }
                />
              </div>
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
                  <button className="primary" onClick={runSharedCombinations} disabled={busy || !results}>
                    {busy ? "Running Combinations..." : "Run Combinations"}
                  </button>
                  {sharedCombosUpdatedAt && <p className="muted">Combinations stored: {new Date(sharedCombosUpdatedAt).toLocaleString()}</p>}
                </div>
                {busy && (
                  <div className="card sub">
                    {comboPhase === "saving" ? (
                      <>
                        <p className="muted">
                          Saving combinations: {processedCombos.toLocaleString()} / {totalCombos.toLocaleString()}
                        </p>
                        <p className="muted">This stores the generated roster universe for the tabs below.</p>
                      </>
                    ) : totalCombos > 0 ? (
                      <>
                        <p className="muted">
                          Processing combinations: {processedCombos.toLocaleString()} / {totalCombos.toLocaleString()}
                        </p>
                        <p className="muted">ETA: {formatEta(topEtaSeconds)}</p>
                      </>
                    ) : (
                      <p className="muted">Starting combination generator...</p>
                    )}
                    <div className="progress">
                      <div
                        className={`progress-bar ${totalCombos > 0 ? "determinate" : ""}`}
                        style={totalCombos > 0 ? { width: `${Math.min(100, (processedCombos / totalCombos) * 100)}%` } : undefined}
                      />
                    </div>
                  </div>
                )}
                <div className="tab-bar small">
                  {playoffTopSubtabs.map((tab) => (
                    <button
                      key={tab.key}
                      className={playoffTopSubtab === tab.key ? "tab active" : "tab"}
                      onClick={() => setPlayoffTopMode(tab.key)}
                    >
                      {tab.label}
                    </button>
                  ))}
                </div>
                <div className="card sub">
                  <h3>{playoffTopSubtab === "completed" ? "Completed Bracket Filters" : `Top Teams (Filtered) - ${playoffBestModeLabel}`}</h3>
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
                    <div className="grid two top5-controls">
                      <Input label="Search Combos" value={comboSearch} onChange={setComboSearch} placeholder="Player/team name or id" />
                      <div className="field top5-counter">
                        <span>Filtered / Stored</span>
                        <p className="muted">{filteredCount} / {sharedComboCount}</p>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </>
        )}

        {playoffTab === "top5" && playoffTopSubtab === "completed" && (
          <div className="stack">
            {!results && (
              <div className="card sub">
                <p className="muted">Run Playoff Bracket in the Bracket Stage tab first.</p>
              </div>
            )}
            {results && (
              <>
                <div className="playoff-bracket-shell">
                  <div className="playoff-bracket-column qf">
                    <h3>Quarter-finals</h3>
                    {completedBracketDerived.qfPairs.map((pair, idx) => (
                      <BracketMatchCard
                        key={`completed-qf-${idx}`}
                        title={`QF ${idx + 1}`}
                        className="connector-out"
                        rows={
                          <>
                            {pair.map((teamId) => (
                              <CompletedBracketTeamRow
                                key={`completed-qf-${idx}-${teamId || "empty"}`}
                                teamId={teamId}
                                selected={Boolean(teamId) && String(completedBracket.qf[idx]) === String(teamId)}
                                onSelect={(value) => setCompletedPick("qf", idx, value)}
                              />
                            ))}
                          </>
                        }
                      />
                    ))}
                  </div>
                  <div className="playoff-bracket-column sf">
                    <h3>Semi-finals</h3>
                    {completedBracketDerived.sfPairs.map((pair, idx) => (
                      <BracketMatchCard
                        key={`completed-sf-${idx}`}
                        title={`SF ${idx + 1}`}
                        className="connector-in connector-out"
                        rows={
                          <>
                            {pair.map((teamId, rowIdx) => (
                              <CompletedBracketTeamRow
                                key={`completed-sf-${idx}-${rowIdx}-${teamId || "empty"}`}
                                teamId={teamId}
                                placeholder={`Winner QF ${idx * 2 + rowIdx + 1}`}
                                muted={!teamId}
                                selected={Boolean(teamId) && String(completedBracket.sf[idx]) === String(teamId)}
                                onSelect={(value) => setCompletedPick("sf", idx, value)}
                              />
                            ))}
                          </>
                        }
                      />
                    ))}
                    {hasThirdPlaceDecider && (
                      <BracketMatchCard
                        title="Third-place"
                        className="connector-in"
                        rows={
                          <>
                            {completedBracketDerived.thirdPair.map((teamId, idx) => (
                              <CompletedBracketTeamRow
                                key={`completed-third-${idx}-${teamId || "empty"}`}
                                teamId={teamId}
                                placeholder="SF loser"
                                muted={!teamId}
                                selected={Boolean(teamId) && String(completedBracket.third) === String(teamId)}
                                onSelect={(value) => setCompletedPick("third", 0, value)}
                              />
                            ))}
                          </>
                        }
                      />
                    )}
                  </div>
                  <div className="playoff-bracket-column final">
                    <h3>Grand final</h3>
                    <BracketMatchCard
                      title="Final"
                      meta="BO5"
                      className="connector-in"
                      rows={
                        <>
                          {completedBracketDerived.finalPair.map((teamId, idx) => (
                            <CompletedBracketTeamRow
                              key={`completed-final-${idx}-${teamId || "empty"}`}
                              teamId={teamId}
                              placeholder={`Winner SF ${idx + 1}`}
                              muted={!teamId}
                              selected={Boolean(teamId) && String(completedBracket.final) === String(teamId)}
                              onSelect={(value) => setCompletedPick("final", 0, value)}
                            />
                          ))}
                        </>
                      }
                    />
                  </div>
                </div>
                <div className="actions">
                  {!baseTeams && <p className="muted">Run Combinations once above to score this bracket.</p>}
                  {baseTeams && !completedBracketDerived.complete && <p className="muted">Complete the bracket to score the saved combinations.</p>}
                </div>
                {completedBracketMessage && (
                  <div className="card sub">
                    <p className="muted">{completedBracketMessage}</p>
                  </div>
                )}
                {activeCompletedBracketResult && (
                  <div className="stack">
                    <div className="card sub">
                    <h3>
                      Bracket probability {(Number(activeCompletedBracketResult.bracket_probability || 0) * 100).toFixed(2)}%
                    </h3>
                    <p className="muted">
                      Matched 1 of {Number(activeCompletedBracketResult.outcomes_count || 0).toLocaleString()} stored outcomes.
                    </p>
                    <div className="actions">
                      <button className="secondary" onClick={() => setShowCompletedBracketGraph((prev) => !prev)}>
                        {showCompletedBracketGraph ? "Hide Player Value Graph" : "Show Player Value Graph"}
                      </button>
                    </div>
                    </div>
                    {showCompletedBracketGraph && completedBracketValueData.rows.length > 0 && (
                      <PriceVsPointsPanel
                        title="Player Price vs Points (Completed Bracket)"
                        rows={completedBracketValueData.rows}
                        slope={completedBracketValueData.slope}
                        intercept={completedBracketValueData.intercept}
                        showTable={false}
                        onPointClick={setCompletedPlayerBreakdown}
                      />
                    )}
                    {(activeCompletedBracketResult.top_teams || [])[0] && (
                      <div className="card sub">
                        <h3>Best Team</h3>
                        {(() => {
                          const team = (activeCompletedBracketResult.top_teams || [])[0];
                          return (
                            <>
                              <h4>
                                Bracket score {Number(team.total_ev || 0).toFixed(2)} | Cost {team.cost}
                              </h4>
                              <table>
                                <thead>
                                  <tr>
                                    <th>Player</th>
                                    <th>Team</th>
                                    <th>Assigned Role</th>
                                    <th>Cost</th>
                                    <th>Bracket Score</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {(team.players || []).map((p) => (
                                    <tr key={p.player_id}>
                                      <td>
                                        <button
                                          className="inline-link-btn"
                                          onClick={() => setCompletedPlayerBreakdown(rosterAssignedBoosterPlayer(p, team.players || []))}
                                        >
                                          {p.name}
                                        </button>
                                      </td>
                                      <td>{teamLookup[p.team_id] || p.team_id}</td>
                                      <td>{roleLabel(p.role_name)}</td>
                                      <td>{p.price}</td>
                                      <td>{Number(p.mode_score ?? p.total_ev ?? 0).toFixed(2)}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </>
                          );
                        })()}
                      </div>
                    )}
                    <div className="card sub">
                      <h3>Player Values</h3>
                      <table>
                        <thead>
                          <tr>
                            <th>Player</th>
                            <th>Team</th>
                            <th>Cost</th>
                            <th>Bracket Score</th>
                            <th>Value +/-</th>
                          </tr>
                        </thead>
                        <tbody>
                          {completedBracketValueData.rows.map((row) => (
                            <tr key={row.player_id}>
                              <td>
                                <button className="inline-link-btn" onClick={() => setCompletedPlayerBreakdown(row)}>
                                  {row.name}
                                </button>
                              </td>
                              <td>{teamLookup[row.team_id] || row.team_id}</td>
                              <td>{Number(row.price || 0).toLocaleString()}</td>
                              <td>{Number(row.points || 0).toFixed(2)}</td>
                              <td>{Number(row.distance || 0).toFixed(2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                    <div className="card sub">
                      <h3>Top Teams</h3>
                      {(activeCompletedBracketResult.top_teams || []).length === 0 && (
                        <p className="muted">No valid fantasy team matched the budget, per-team, and role constraints.</p>
                      )}
                      {(activeCompletedBracketResult.top_teams || []).map((team, idx) => (
                      <div key={`completed-team-${idx}`} className="card sub">
                        <h4>
                          #{idx + 1} Bracket score {Number(team.total_ev || 0).toFixed(2)} | Cost {team.cost}
                        </h4>
                        <table>
                          <thead>
                            <tr>
                              <th>Player</th>
                              <th>Team</th>
                              <th>Assigned Role</th>
                              <th>Cost</th>
                              <th>Bracket Score</th>
                            </tr>
                          </thead>
                          <tbody>
                            {(team.players || []).map((p) => (
                              <tr key={p.player_id}>
                                <td>
                                  <button
                                    className="inline-link-btn"
                                    onClick={() => setCompletedPlayerBreakdown(rosterAssignedBoosterPlayer(p, team.players || []))}
                                  >
                                    {p.name}
                                  </button>
                                </td>
                                <td>{teamLookup[p.team_id] || p.team_id}</td>
                                <td>{roleLabel(p.role_name)}</td>
                                <td>{p.price}</td>
                                <td>{Number(p.mode_score ?? p.total_ev ?? 0).toFixed(2)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    ))}
                    </div>
                  </div>
                )}
              </>
            )}
          </div>
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
                          <button
                            className="inline-link-btn"
                            onClick={() =>
                              openScoringBreakdown({
                                player_id: Number(pid),
                                name: playerLookup[Number(pid)] || pid,
                                team_id: Number(tid),
                                points: Number(comps.total_points_without_booster ?? comps.total_points ?? 0),
                                rating: Number(comps.rating_points_total || 0),
                                win: Number(comps.win_points_total || 0),
                                role: Number(comps.role_points_total || 0),
                                booster: Number(comps.booster_points_total || 0),
                                components_available: true,
                                point_breakdown: comps.point_breakdown || [],
                              })
                            }
                          >
                            {playerLookup[Number(pid)] || pid}
                          </button>
                        </td>
                        <td>{Number(comps.total_points_without_booster ?? comps.total_points ?? 0).toFixed(2)}</td>
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
      {playoffTab === "top5" && playoffTopSubtab !== "completed" && topTeams && topTeams.length > 0 && (
        <div className="card sub">
          <h3>Top Teams</h3>
          {topTeams.map((team, idx) => (
            <div key={idx} className="card sub">
              <h4>
                #{idx + 1} {playoffTeamMetricLabel(team)} | Cost {team.cost}
              </h4>
              <table>
                <thead>
                  <tr>
                    <th>Player</th>
                    <th>Team</th>
                    <th>Assigned Role</th>
                    <th>Cost</th>
                    <th>Total EV</th>
                    <th>Mode Score</th>
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
                        <button
                          className="inline-link-btn"
                          onClick={() => openProjectedRosterBreakdown(p, team.players || [])}
                        >
                          {p.name}
                        </button>
                      </td>
                      <td>{teamLookup[p.team_id] || p.team_id}</td>
                      <td>{roleLabel(p.role_name)}</td>
                      <td>{p.price}</td>
                      <td>{p.total_ev.toFixed(2)}</td>
                      <td>{playoffPlayerModeScore(p).toFixed(2)}</td>
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
      {playoffTab === "top5" && playoffTopSubtab !== "completed" && allTeams && allTeams.length > 0 && (
        <div className="card sub">
          <h3>All Filtered Teams ({filteredCount})</h3>
          <div className="actions">
            <button className="secondary" onClick={() => querySharedCombinations(Math.max(0, page - 1))} disabled={page === 0}>
              Prev 200
            </button>
            <button
              className="secondary"
              onClick={() => querySharedCombinations((page + 1) * 200 < filteredCount ? page + 1 : page)}
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
                <SortHeader sortValue={sortKey} asc="ev_asc" desc="ev_desc" defaultDirection="desc" onChange={setSortKey}>Mode Score</SortHeader>
                <SortHeader sortValue={sortKey} asc="cost_asc" desc="cost_desc" onChange={setSortKey}>Cost</SortHeader>
                <SortHeader sortValue={sortKey} asc="cpp_asc" desc="cpp_desc" defaultDirection="desc" onChange={setSortKey}>Value</SortHeader>
                <th>Players</th>
              </tr>
            </thead>
            <tbody>
              {allTeams.map((team, idx) => (
                <tr key={idx + page * 200}>
                  <td>{idx + 1 + page * 200}</td>
                  <td>{playoffTeamMetric(team).toFixed(2)}</td>
                  <td>{team.cost}</td>
                  <td>{(playoffTeamMetric(team) / (team.cost || 1)).toFixed(4)}</td>
                  <td>
                    {(team.players || []).map((p, playerIdx) => (
                      <span key={`${idx}-${p.player_id}`}>
                        {playerIdx > 0 && ", "}
                        <button
                          className="inline-link-btn"
                          onClick={() => openProjectedRosterBreakdown(p, team.players || [])}
                        >
                          {p.name}
                        </button>
                        {` (${teamLookup[p.team_id] || p.team_id}, ${roleLabel(p.role_name)})`}
                      </span>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {playoffTab === "top5" && playoffTopSubtab !== "completed" && baseTeams && filteredCount === 0 && (
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
      {playoffTab === "top5" && playoffTopSubtab !== "completed" && topMessage && (
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
      {completedPlayerBreakdown && (
        <div className="modal-backdrop" onClick={() => setCompletedPlayerBreakdown(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>{completedPlayerBreakdown.name || `Player ${completedPlayerBreakdown.player_id}`} Point Sources</h3>
              <button className="close" onClick={() => setCompletedPlayerBreakdown(null)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Points</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>Rating</td>
                    <td>{completedBreakdownValue(completedPlayerBreakdown, "rating").toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>Win</td>
                    <td>{completedBreakdownValue(completedPlayerBreakdown, "win").toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>Role</td>
                    <td>{completedBreakdownValue(completedPlayerBreakdown, "role").toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td>Booster</td>
                    <td>{completedBreakdownValue(completedPlayerBreakdown, "booster").toFixed(2)}</td>
                  </tr>
                  <tr>
                    <td><strong>Total</strong></td>
                    <td><strong>{completedBreakdownValue(completedPlayerBreakdown, "total").toFixed(2)}</strong></td>
                  </tr>
                </tbody>
              </table>
              {Array.isArray(completedPlayerBreakdown.point_breakdown) && completedPlayerBreakdown.point_breakdown.length > 0 && (
                <div className="stack">
                  <h4>Match Sources</h4>
                  <table>
                    <thead>
                      <tr>
                        <th>Match</th>
                        <th>Opponent</th>
                        <th>Rating</th>
                        <th>Win</th>
                        <th>Role</th>
                        <th>Booster</th>
                        <th>Total</th>
                      </tr>
                    </thead>
                    <tbody>
                      {completedPlayerBreakdown.point_breakdown.map((row, idx) => {
                        const opponentId = Number(row.opponent_team_id || 0);
                        const matchLabel = row.match_number ? `M${row.match_number} ${row.match_type || ""}` : row.match_type || "Adjustment";
                        const opponentLabel = opponentId > 0 ? `${teamLookup[opponentId] || opponentId} (#${row.opponent_rank ?? "-"})` : row.note || "-";
                        const roleName = row.role_id == null ? "Stored role" : roleLabel(row.role_id);
                        const boosterName = row.booster_name || (row.booster_slot ? `Booster slot ${row.booster_slot}` : "None");
                        return (
                          <tr key={`${row.match_number || "adj"}-${idx}`}>
                            <td>{matchLabel}</td>
                            <td>{opponentLabel}</td>
                            <td>
                              {Number(row.rating_points || 0).toFixed(2)}
                              {row.rating_used != null && <div className="muted">rating {Number(row.rating_used || 0).toFixed(2)}</div>}
                            </td>
                            <td>
                              {Number(row.win_points || 0).toFixed(2)}
                              <div className="muted">{row.did_win ? "Win" : "Loss"} {Number((row.win_probability || 0) * 100).toFixed(1)}%</div>
                            </td>
                            <td>
                              {Number(row.role_points || 0).toFixed(2)}
                              <div className="muted">
                                {roleName} major {Number((row.role_major_pct || 0) * 100).toFixed(1)}%, minor {Number((row.role_minor_pct || 0) * 100).toFixed(1)}%
                              </div>
                            </td>
                            <td>
                              {Number(row.booster_points || 0).toFixed(2)}
                              <div className="muted">
                                {boosterName} {Number((row.booster_trigger_rate || 0) * 100).toFixed(1)}%
                                {row.booster_assigned === false && row.raw_booster_name && (
                                  <div>raw: {row.raw_booster_name} {(Number(row.raw_booster_trigger_rate || 0) * 100).toFixed(1)}%</div>
                                )}
                              </div>
                            </td>
                            <td>{Number(row.total_points || 0).toFixed(2)}</td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              {completedPlayerBreakdown.components_available === false && (
                  <p className="muted">
                    This stored bracket was generated before source components were saved. Re-run the playoff bracket to see rating, win, role, and booster split accurately.
                  </p>
              )}
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
  const [recentResultsImportMode, setRecentResultsImportMode] = useState("until_date");
  const [recentResultsPages, setRecentResultsPages] = useState("3");
  const [recentResultsUntilYear, setRecentResultsUntilYear] = useState("");
  const [recentResultsUntilMonth, setRecentResultsUntilMonth] = useState("");
  const [recentResultsUntilDay, setRecentResultsUntilDay] = useState("");
  const [recentResultsMaxHltvRank, setRecentResultsMaxHltvRank] = useState("0");
  const [recentResultsRankFilterMode, setRecentResultsRankFilterMode] = useState("both");
  const [recentResultsOffset, setRecentResultsOffset] = useState(0);
  const [recentResultsImporting, setRecentResultsImporting] = useState(false);
  const [recentResultsImportProcessed, setRecentResultsImportProcessed] = useState(0);
  const [recentResultsImportTotal, setRecentResultsImportTotal] = useState(0);
  const [recentResultsImportEtaSeconds, setRecentResultsImportEtaSeconds] = useState(null);
  const [recentResultsImportPhase, setRecentResultsImportPhase] = useState("");
  const [recentResultsImportCurrent, setRecentResultsImportCurrent] = useState("");
  const [recentResultsImportJobId, setRecentResultsImportJobId] = useState("");
  const [recentResultsImportStatus, setRecentResultsImportStatus] = useState("");
  const [recentResultsRankingDatesDone, setRecentResultsRankingDatesDone] = useState(0);
  const [recentResultsRankingDatesTotal, setRecentResultsRankingDatesTotal] = useState(0);
  const [selectedMatchUrl, setSelectedMatchUrl] = useState("");
  const [selectedMatchRow, setSelectedMatchRow] = useState(null);
  const [showMatchModal, setShowMatchModal] = useState(false);
  const initialLoadRef = useRef(false);
  const recentResultsImportPollingRef = useRef(false);
  const recentResultsImportEtaRef = useRef({
    phase: "",
    total: 0,
    lastProcessed: 0,
    lastAt: 0,
    rate: null,
  });
  const RECENT_RESULTS_IMPORT_JOB_ID_KEY = "hltv_results_import_job_id";
  const RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY = "hltv_results_import_job_started_at";
  const recentResultsImportProgressPct =
    recentResultsImportTotal > 0
      ? Math.min(100, Math.max(0, (recentResultsImportProcessed / recentResultsImportTotal) * 100))
      : 0;
  const recentResultsImportIndeterminate = recentResultsImporting && recentResultsImportTotal <= 0;
  const recentResultsImportActive = ["queued", "running", "pausing"].includes(recentResultsImportStatus);
  const recentResultsImportResumable = ["paused", "failed"].includes(recentResultsImportStatus);
  const recentResultsBusy = recentResultsLoading || recentResultsImportActive;

  const formatRecentResultsEta = (seconds) => {
    if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return "-";
    const s = Math.max(0, Math.round(seconds));
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const r = s % 60;
    if (h > 0) return `${h}h ${m}m ${r}s`;
    if (m > 0) return `${m}m ${r}s`;
    return `${r}s`;
  };
  const formatRecentResultsImportPhase = (phase) => {
    const labels = {
      queued: "Queued",
      fetching_results: "Fetching result pages",
      filtering_by_rank: "Filtering by HLTV rank",
      fetching_match_details: "Fetching match details",
      storing_results: "Saving matches",
      deduping_results: "Removing duplicates",
      enriching_points: "Finding rankings",
      completed: "Completed",
      failed: "Failed",
    };
    return labels[phase] || "Importing";
  };

  const updateRecentResultsEta = (phase, processed, total) => {
    const now = Date.now();
    const state = recentResultsImportEtaRef.current;
    const changedScope = state.phase !== phase || state.total !== total || processed < state.lastProcessed;
    if (changedScope) {
      recentResultsImportEtaRef.current = {
        phase,
        total,
        lastProcessed: processed,
        lastAt: now,
        rate: null,
      };
      setRecentResultsImportEtaSeconds(total > 0 && processed >= total ? 0 : null);
      return;
    }

    const delta = processed - state.lastProcessed;
    const elapsedSec = Math.max(0.001, (now - state.lastAt) / 1000);
    if (delta > 0 && total > processed) {
      const instantRate = delta / elapsedSec;
      const nextRate = state.rate == null ? instantRate : state.rate * 0.65 + instantRate * 0.35;
      recentResultsImportEtaRef.current = {
        ...state,
        lastProcessed: processed,
        lastAt: now,
        rate: nextRate,
      };
      setRecentResultsImportEtaSeconds(nextRate > 0 ? (total - processed) / nextRate : null);
    } else if (total > 0 && processed >= total) {
      recentResultsImportEtaRef.current = {
        ...state,
        lastProcessed: processed,
        lastAt: now,
      };
      setRecentResultsImportEtaSeconds(0);
    } else if (state.rate == null) {
      setRecentResultsImportEtaSeconds(null);
    }
  };

  const buildRecentResultsUntilDate = () => {
    const year = String(recentResultsUntilYear || "").trim();
    const month = String(recentResultsUntilMonth || "").trim().padStart(2, "0");
    const day = String(recentResultsUntilDay || "").trim().padStart(2, "0");
    if (!/^\d{4}$/.test(year) || !/^\d{2}$/.test(month) || !/^\d{2}$/.test(day)) return "";
    const date = new Date(`${year}-${month}-${day}T00:00:00Z`);
    if (
      Number.isNaN(date.getTime()) ||
      date.getUTCFullYear() !== Number(year) ||
      date.getUTCMonth() + 1 !== Number(month) ||
      date.getUTCDate() !== Number(day)
    ) {
      return "";
    }
    return `${year}-${month}-${day}`;
  };

  const clearRecentResultsImportStorage = () => {
    localStorage.removeItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    localStorage.removeItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY);
    setRecentResultsImportJobId("");
  };

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

  const pollRecentResultsImportJob = async (jobId, startedAtMs) => {
    if (!jobId || recentResultsImportPollingRef.current) return;
    recentResultsImportPollingRef.current = true;
    setRecentResultsImporting(true);
    try {
      let done = false;
      while (!done) {
        const status = await api.get(`/events/hltv-results/import/job/${jobId}`);
        if (status?.detail) {
          setRecentResultsError(String(status.detail));
          clearRecentResultsImportStorage();
          return;
        }

        const processed = Number(status.processed_units || 0);
        const total = Number(status.total_units || 0);
        setRecentResultsImportProcessed(processed);
        setRecentResultsImportTotal(total);
        const phase = String(status.phase || status.status || "");
        setRecentResultsImportPhase(phase);
        setRecentResultsImportCurrent(String(status.current || ""));
        setRecentResultsImportStatus(String(status.status || ""));
        setRecentResultsImportJobId(String(status.job_id || jobId));
        setRecentResultsRankingDatesDone(Number(status.ranking_dates_done || 0));
        setRecentResultsRankingDatesTotal(Number(status.ranking_dates_total || 0));

        updateRecentResultsEta(phase, processed, total);

        if (status.status === "failed") {
          setRecentResultsError(status.error || "Failed to import/store HLTV results.");
          done = true;
          break;
        }
        if (status.status === "paused") {
          done = true;
          break;
        }
        if (status.status === "completed") {
          const res = status.result || {};
          if (notify) {
            notify(
              `Imported HLTV results: ${res.kept ?? res.fetched ?? 0} kept, ${res.rank_filtered_out || 0} rank-filtered, ${res.date_filtered_out || 0} date-filtered, ${res.inserted || 0} inserted, ${res.updated || 0} updated`
            );
          }
          clearRecentResultsImportStorage();
          await loadStoredRecentResults(0);
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 500));
      }
    } catch (e) {
      setRecentResultsError(e?.message || "Failed to import/store HLTV results.");
      clearRecentResultsImportStorage();
    } finally {
      recentResultsImportPollingRef.current = false;
      setRecentResultsImporting(false);
    }
  };

  const importRecentResultsToDb = async () => {
    const importMode = recentResultsImportMode === "max_pages" ? "max_pages" : "until_date";
    const untilDate = buildRecentResultsUntilDate();
    const pages = Math.max(1, Number(recentResultsPages) || 1);
    const maxHltvRank = Math.max(0, Math.min(500, Number(recentResultsMaxHltvRank) || 0));
    if (importMode === "until_date" && !untilDate) {
      setRecentResultsError("Enter a valid Import Back To Date before starting date mode.");
      return;
    }
    setRecentResultsImporting(true);
    setRecentResultsImportProcessed(0);
    setRecentResultsImportTotal(importMode === "until_date" ? 0 : pages);
    setRecentResultsImportEtaSeconds(null);
    recentResultsImportEtaRef.current = { phase: "", total: 0, lastProcessed: 0, lastAt: 0, rate: null };
    setRecentResultsImportPhase("queued");
    setRecentResultsImportCurrent("");
    setRecentResultsRankingDatesDone(0);
    setRecentResultsRankingDatesTotal(0);
    setRecentResultsError("");
    try {
      const start = await api.post("/events/hltv-results/import/start", {
        import_mode: importMode,
        pages: importMode === "max_pages" ? pages : 1,
        start_offset: 0,
        page_stride: 100,
        per_page_limit: 100,
        until_date: importMode === "until_date" ? untilDate : "",
        max_hltv_rank: maxHltvRank,
        rank_filter_mode: recentResultsRankFilterMode,
      });
      if (start?.detail) {
        setRecentResultsError(String(start.detail));
        return;
      }
      const jobId = start?.job_id;
      if (!jobId) {
        setRecentResultsError("Failed to start HLTV results import job.");
        return;
      }
      const startedAt = Date.now();
      setRecentResultsImportJobId(String(jobId));
      setRecentResultsImportStatus("queued");
      localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY, String(jobId));
      localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY, String(startedAt));
      await pollRecentResultsImportJob(jobId, startedAt);
    } catch (e) {
      setRecentResultsError(e?.message || "Failed to import/store HLTV results.");
      clearRecentResultsImportStorage();
    } finally {
      if (!recentResultsImportPollingRef.current) setRecentResultsImporting(false);
    }
  };

  const pauseRecentResultsImportJob = async () => {
    const jobId = recentResultsImportJobId || localStorage.getItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    if (!jobId) return;
    setRecentResultsImportStatus("pausing");
    setRecentResultsImportCurrent("Pausing after current request");
    try {
      const status = await api.post(`/events/hltv-results/import/job/${jobId}/pause`, {});
      if (status?.detail) {
        setRecentResultsError(String(status.detail));
        return;
      }
      setRecentResultsImportStatus(String(status.status || "pausing"));
      setRecentResultsImportPhase(String(status.phase || status.status || ""));
      setRecentResultsImportCurrent(String(status.current || ""));
      setRecentResultsRankingDatesDone(Number(status.ranking_dates_done || 0));
      setRecentResultsRankingDatesTotal(Number(status.ranking_dates_total || 0));
    } catch (e) {
      setRecentResultsError(e?.message || "Failed to pause HLTV results import.");
    }
  };

  const resumeRecentResultsImportJob = async () => {
    const jobId = recentResultsImportJobId || localStorage.getItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    if (!jobId) return;
    setRecentResultsImporting(true);
    setRecentResultsImportStatus("queued");
    recentResultsImportEtaRef.current = { phase: "", total: 0, lastProcessed: 0, lastAt: 0, rate: null };
    setRecentResultsImportEtaSeconds(null);
    setRecentResultsError("");
    try {
      const status = await api.post(`/events/hltv-results/import/job/${jobId}/resume`, {});
      if (status?.detail) {
        setRecentResultsError(String(status.detail));
        return;
      }
      const startedAt = Date.now();
      localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY, String(jobId));
      localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY, String(startedAt));
      await pollRecentResultsImportJob(jobId, startedAt);
    } catch (e) {
      setRecentResultsError(e?.message || "Failed to resume HLTV results import.");
    } finally {
      if (!recentResultsImportPollingRef.current) setRecentResultsImporting(false);
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

  const openMatchModal = async (row) => {
    const url = String(row?.match_url || "").trim();
    if (!url) return;
    setSelectedMatchUrl(url);
    setSelectedMatchRow(row || null);
    setShowMatchModal(true);
    try {
      const details = await api.get(`/events/hltv-results/match-details?match_url=${encodeURIComponent(url)}`);
      const maps = Array.isArray(details?.maps) ? details.maps : [];
      const stored = details?.stored || {};
      const nextRow = {
        ...(row || {}),
        ...(stored || {}),
        maps_json: JSON.stringify(maps),
      };
      setSelectedMatchRow(nextRow);
      setRecentResults((prev) => prev.map((item) => (String(item?.match_url || "") === url ? nextRow : item)));
    } catch {
      // Keep the stored row visible if live detail refresh fails.
    }
  };

  useEffect(() => {
    if (initialLoadRef.current) return;
    initialLoadRef.current = true;
    loadStoredRecentResults(0);
    const jobId = localStorage.getItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    if (jobId) {
      const startedAt = Number(localStorage.getItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY) || Date.now());
      pollRecentResultsImportJob(jobId, startedAt);
    } else {
      api
        .get("/events/hltv-results/import/latest")
        .then((latest) => {
          if (!latest?.exists) return;
          const status = String(latest.status || "");
          if (!["queued", "running", "pausing", "paused", "failed"].includes(status)) return;
          const latestJobId = String(latest.job_id || "");
          if (!latestJobId) return;
          setRecentResultsImportJobId(latestJobId);
          setRecentResultsImportStatus(status);
          setRecentResultsImportProcessed(Number(latest.processed_units || 0));
          setRecentResultsImportTotal(Number(latest.total_units || 0));
          setRecentResultsImportPhase(String(latest.phase || status));
          setRecentResultsImportCurrent(String(latest.current || ""));
          setRecentResultsRankingDatesDone(Number(latest.ranking_dates_done || 0));
          setRecentResultsRankingDatesTotal(Number(latest.ranking_dates_total || 0));
          const startedAt = Date.now();
          localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY, latestJobId);
          localStorage.setItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY, String(startedAt));
          if (["queued", "running", "pausing"].includes(status)) pollRecentResultsImportJob(latestJobId, startedAt);
        })
        .catch(() => {});
    }
  }, []);

  return (
    <div className="stack">
      <div className="grid four">
        <Select
          label="Import Mode"
          value={recentResultsImportMode}
          onChange={setRecentResultsImportMode}
          options={[
            { value: "until_date", label: "Until date" },
            { value: "max_pages", label: "Max pages" },
          ]}
        />
        {recentResultsImportMode === "until_date" ? (
          <label className="field">
            <span>Import Back To Date</span>
            <div className="date-input-group">
              <input
                value={recentResultsUntilYear}
                onChange={(e) => setRecentResultsUntilYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
                placeholder="YYYY"
                inputMode="numeric"
              />
              <input
                value={recentResultsUntilMonth}
                onChange={(e) => setRecentResultsUntilMonth(e.target.value.replace(/\D/g, "").slice(0, 2))}
                onBlur={() => setRecentResultsUntilMonth((value) => (value ? String(value).padStart(2, "0") : ""))}
                placeholder="MM"
                inputMode="numeric"
              />
              <input
                value={recentResultsUntilDay}
                onChange={(e) => setRecentResultsUntilDay(e.target.value.replace(/\D/g, "").slice(0, 2))}
                onBlur={() => setRecentResultsUntilDay((value) => (value ? String(value).padStart(2, "0") : ""))}
                placeholder="DD"
                inputMode="numeric"
              />
            </div>
          </label>
        ) : (
          <Input
            label="Pages"
            value={recentResultsPages}
            onChange={setRecentResultsPages}
            placeholder="e.g. 30"
          />
        )}
        <Input
          label="Max HLTV Rank"
          value={recentResultsMaxHltvRank}
          onChange={setRecentResultsMaxHltvRank}
          placeholder="0 = all ranks"
        />
        <Select
          label="Rank Filter"
          value={recentResultsRankFilterMode}
          onChange={setRecentResultsRankFilterMode}
          options={[
            { value: "both", label: "Both teams" },
            { value: "either", label: "Either team" },
          ]}
        />
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button className="primary" onClick={importRecentResultsToDb} disabled={recentResultsBusy || recentResultsImportResumable}>
          {recentResultsImporting ? "Importing..." : "Import HLTV Results To SQL"}
        </button>
        {recentResultsImportActive && recentResultsImportJobId && (
          <button className="secondary" onClick={pauseRecentResultsImportJob} disabled={recentResultsImportStatus === "pausing"}>
            {recentResultsImportStatus === "pausing" ? "Pausing..." : "Pause"}
          </button>
        )}
        {recentResultsImportResumable && recentResultsImportJobId && (
          <button className="secondary" onClick={resumeRecentResultsImportJob}>
            Resume
          </button>
        )}
        <button className="danger" onClick={clearStoredResults} disabled={recentResultsBusy}>
          {recentResultsLoading ? "Deleting..." : "Delete All Stored Matches"}
        </button>
        <button className="secondary" onClick={loadStoredRecentResults} disabled={recentResultsBusy}>
          {recentResultsLoading ? "Loading..." : "Reload Stored Results"}
        </button>
        <span className="muted">{recentResults.length} loaded</span>
      </div>
      {recentResultsImporting && (
        <div className="card sub">
          <p className="muted">
            {formatRecentResultsImportPhase(recentResultsImportPhase)}:{" "}
            {recentResultsImportTotal > 0
              ? `${recentResultsImportProcessed.toLocaleString()} / ${recentResultsImportTotal.toLocaleString()} | ETA: ${formatRecentResultsEta(recentResultsImportEtaSeconds)}`
              : `${recentResultsImportProcessed.toLocaleString()} pages fetched`}
          </p>
          <div className="progress">
            <div
              className={`progress-bar ${recentResultsImportIndeterminate ? "" : "determinate"}`}
              style={recentResultsImportIndeterminate ? undefined : { width: `${recentResultsImportProgressPct}%` }}
            />
          </div>
          {recentResultsRankingDatesTotal > 0 && (
            <p className="muted">
              Ranking dates checked: {recentResultsRankingDatesDone.toLocaleString()} /{" "}
              {recentResultsRankingDatesTotal.toLocaleString()}
            </p>
          )}
          {recentResultsImportCurrent && <p className="muted">{recentResultsImportCurrent}</p>}
        </div>
      )}
      {!recentResultsImporting && recentResultsImportResumable && recentResultsImportJobId && (
        <div className="card sub">
          <p className="muted">
            {formatRecentResultsImportPhase(recentResultsImportPhase)}: {recentResultsImportCurrent || "Import can be resumed."}
          </p>
        </div>
      )}
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(Math.max(0, recentResultsOffset - 100))}
          disabled={recentResultsBusy || recentResultsOffset <= 0}
        >
          Prev 100
        </button>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(recentResultsOffset + 100)}
          disabled={recentResultsBusy || recentResults.length < 100}
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
  const [mapStatsJobStatus, setMapStatsJobStatus] = useState("idle");
  const [mapStatsJobId, setMapStatsJobId] = useState("");
  const [mapStatsJobProcessed, setMapStatsJobProcessed] = useState(0);
  const [mapStatsJobTotal, setMapStatsJobTotal] = useState(0);
  const [mapStatsJobOk, setMapStatsJobOk] = useState(0);
  const [mapStatsJobFailed, setMapStatsJobFailed] = useState(0);
  const [mapStatsJobLastError, setMapStatsJobLastError] = useState("");
  const [mapStatsJobEtaSeconds, setMapStatsJobEtaSeconds] = useState(null);
  const mapStatsJobPollingRef = useRef(false);
  const [teamForm, setTeamForm] = useState({
    team_id: "",
    hltv_team_id: "",
    name: "",
    hltv_rank: "",
    hltv_points: "",
    vrs_rank: "",
    vrs_points: "",
    win_rate: "",
    map_stats_json: "",
    map_stats_imported_at: "",
    map_stats_source_url: "",
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
          hltv_team_id: t.hltv_team_id ?? "",
          name: t.name || "",
          hltv_rank: t.hltv_rank || "",
          hltv_points: t.hltv_points || "",
          vrs_rank: t.vrs_rank || "",
          vrs_points: t.vrs_points || "",
          win_rate: t.win_rate || "",
          map_stats_json: t.map_stats_json || "",
          map_stats_imported_at: t.map_stats_imported_at || "",
          map_stats_source_url: t.map_stats_source_url || "",
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
      hltv_team_id: teamForm.hltv_team_id === "" ? undefined : Number(teamForm.hltv_team_id),
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
      hltv_team_id: "",
      name: "",
      hltv_rank: "",
      hltv_points: "",
      vrs_rank: "",
      vrs_points: "",
      win_rate: "",
      map_stats_json: "",
      map_stats_imported_at: "",
      map_stats_source_url: "",
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
      hltv_team_id: "",
      name: "",
      hltv_rank: "",
      hltv_points: "",
      vrs_rank: "",
      vrs_points: "",
      win_rate: "",
      map_stats_json: "",
      map_stats_imported_at: "",
      map_stats_source_url: "",
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
    setBatchTopRatingsBusy(["queued", "running", "pausing", "canceling"].includes(nextStatus));

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
        if (nextStatus === "canceled") {
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
    setBatchTopRatingsStatus("pausing");
    setBatchTopRatingsBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchTopRatingsJobId}/pause`, {});
      const applied = applyBatchTopRatingsStatus(status, batchTopRatingsJobId);
      if (["pausing", "running", "queued"].includes(applied.nextStatus)) {
        pollBatchTopRatingsJob(batchTopRatingsJobId);
      }
    } catch (e) {
      setBatchTopRatingsStatus("running");
      notify(`Failed to pause Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  const cancelBatchTopRatingsJob = async () => {
    if (!batchTopRatingsJobId) return;
    setBatchTopRatingsStatus("canceling");
    setBatchTopRatingsBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchTopRatingsJobId}/cancel`, {});
      const applied = applyBatchTopRatingsStatus(status, batchTopRatingsJobId);
      if (["canceling", "running", "queued", "pausing"].includes(applied.nextStatus)) {
        pollBatchTopRatingsJob(batchTopRatingsJobId);
      }
    } catch (e) {
      setBatchTopRatingsBusy(false);
      notify(`Failed to cancel Top-X batch: ${e?.message || "unknown error"}`);
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
        if (["queued", "running", "pausing", "canceling"].includes(applied.nextStatus)) {
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

  const applyMapStatsJobStatus = (status, jobIdOverride = "") => {
    const jobId = String(jobIdOverride || status?.job_id || "");
    const processed = Number(status?.processed_teams || 0);
    const total = Number(status?.total_teams || 0);
    const ok = Number(status?.ok || 0);
    const failed = Number(status?.failed || 0);
    const nextStatus = String(status?.status || "queued");
    const lastError = String(status?.last_error || status?.error || "");

    setMapStatsJobStatus(nextStatus);
    setMapStatsJobId(jobId);
    setMapStatsJobProcessed(processed);
    setMapStatsJobTotal(total);
    setMapStatsJobOk(ok);
    setMapStatsJobFailed(failed);
    setMapStatsJobLastError(lastError);

    const startedAtMs = getBatchStartedAtMs(status);
    if (processed > 0 && total > processed && ["queued", "running", "pausing", "canceling"].includes(nextStatus)) {
      const elapsedSeconds = Math.max(1, (Date.now() - startedAtMs) / 1000);
      const rate = processed / elapsedSeconds;
      setMapStatsJobEtaSeconds(rate > 0 ? (total - processed) / rate : null);
    } else if (total > 0 && processed >= total) {
      setMapStatsJobEtaSeconds(0);
    } else {
      setMapStatsJobEtaSeconds(null);
    }

    return { jobId, processed, total, ok, failed, nextStatus, lastError };
  };

  const pollMapStatsJob = async (jobId) => {
    if (!jobId || mapStatsJobPollingRef.current) return;
    mapStatsJobPollingRef.current = true;
    const refreshOpenTeamMapStats = async () => {
      if (!selectedTeam) return;
      try {
        const refreshedTeam = await api.get(`/teams/${selectedTeam}`);
        setTeamForm((prev) => ({
          ...prev,
          hltv_team_id: refreshedTeam?.hltv_team_id ?? prev.hltv_team_id,
          map_stats_json: refreshedTeam?.map_stats_json || "",
          map_stats_imported_at: refreshedTeam?.map_stats_imported_at || "",
          map_stats_source_url: refreshedTeam?.map_stats_source_url || "",
        }));
      } catch {
        // The main list refresh still covers this; the direct modal refresh is just for immediacy.
      }
    };
    try {
      let done = false;
      while (!done) {
        const status = await api.get(`/teams/map-stats-import/job/${jobId}`);
        const { ok, failed, nextStatus, lastError } = applyMapStatsJobStatus(status, jobId);

        if (nextStatus === "completed") {
          notify(`Map stats imported: ${ok} ok, ${failed} failed`);
          await refresh();
          await refreshOpenTeamMapStats();
          done = true;
          break;
        }
        if (nextStatus === "failed") {
          notify(lastError || "Map stats import failed.");
          done = true;
          break;
        }
        if (["paused", "canceled"].includes(nextStatus)) {
          await refresh();
          await refreshOpenTeamMapStats();
          done = true;
          break;
        }

        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    } catch (e) {
      setMapStatsJobStatus("failed");
      setMapStatsJobLastError(String(e?.message || "Failed to poll map stats job."));
      notify(`Map stats import failed: ${e?.message || "unknown error"}`);
    } finally {
      mapStatsJobPollingRef.current = false;
    }
  };

  const startMapStatsImportJob = async (missingOnly = false, teamIds = []) => {
    setMapStatsJobStatus("queued");
    setMapStatsJobProcessed(0);
    setMapStatsJobTotal(0);
    setMapStatsJobOk(0);
    setMapStatsJobFailed(0);
    setMapStatsJobLastError("");
    setMapStatsJobEtaSeconds(null);
    try {
      const start = await api.post("/teams/map-stats-import/start", { missing_only: missingOnly, team_ids: teamIds });
      const jobId = String(start?.job_id || "");
      if (!jobId) throw new Error("Failed to start map stats import job.");
      setMapStatsJobId(jobId);
      await pollMapStatsJob(jobId);
    } catch (e) {
      setMapStatsJobStatus("failed");
      setMapStatsJobLastError(String(e?.message || "Failed to start map stats import job."));
      notify(`Map stats import failed: ${e?.message || "unknown error"}`);
    }
  };

  const pauseMapStatsJob = async () => {
    if (!mapStatsJobId) return;
    setMapStatsJobStatus("pausing");
    try {
      const status = await api.post(`/teams/map-stats-import/job/${mapStatsJobId}/pause`, {});
      const applied = applyMapStatsJobStatus(status, mapStatsJobId);
      if (["pausing", "running", "queued"].includes(applied.nextStatus)) {
        pollMapStatsJob(mapStatsJobId);
      }
    } catch (e) {
      setMapStatsJobStatus("running");
      notify(`Failed to pause map stats import: ${e?.message || "unknown error"}`);
    }
  };

  const cancelMapStatsJob = async () => {
    if (!mapStatsJobId) return;
    setMapStatsJobStatus("canceling");
    try {
      const status = await api.post(`/teams/map-stats-import/job/${mapStatsJobId}/cancel`, {});
      const applied = applyMapStatsJobStatus(status, mapStatsJobId);
      if (["canceling", "running", "queued", "pausing"].includes(applied.nextStatus)) {
        pollMapStatsJob(mapStatsJobId);
      }
    } catch (e) {
      notify(`Failed to cancel map stats import: ${e?.message || "unknown error"}`);
    }
  };

  const resumeMapStatsJob = async () => {
    if (!mapStatsJobId) return;
    try {
      const status = await api.post(`/teams/map-stats-import/job/${mapStatsJobId}/resume`, {});
      const applied = applyMapStatsJobStatus(status, mapStatsJobId);
      if (["queued", "running", "pausing"].includes(applied.nextStatus)) {
        pollMapStatsJob(mapStatsJobId);
      }
    } catch (e) {
      notify(`Failed to resume map stats import: ${e?.message || "unknown error"}`);
    }
  };

  useEffect(() => {
    let cancelled = false;

    const hydrateLatestMapStatsJob = async () => {
      try {
        const latest = await api.get("/teams/map-stats-import/latest");
        if (cancelled || !latest?.exists) return;
        if (latest?.status === "completed") return;
        const applied = applyMapStatsJobStatus(latest);
        if (["queued", "running", "pausing", "canceling"].includes(applied.nextStatus)) {
          pollMapStatsJob(applied.jobId);
        }
      } catch {
        // The map-stats progress panel is optional on startup.
      }
    };

    hydrateLatestMapStatsJob();
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

  const refreshSelectedTeamMapStats = async () => {
    if (!selectedTeam) return;
    await startMapStatsImportJob(false, [selectedTeam]);
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
  const currentMapPool = ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Overpass"];
  const canonicalMapName = (value) => {
    const raw = String(value || "").trim();
    const key = raw.toLowerCase().replace(/_/g, " ");
    if (key === "dust 2" || key === "dust2" || key === "de dust2") return "Dust2";
    const found = currentMapPool.find((map) => map.toLowerCase() === key);
    return found || raw;
  };
  const formatTeamPct = (value, digits = 1) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "-";
  };
  const teamMapStatsRows = useMemo(() => {
    try {
      const parsed = JSON.parse(String(teamForm.map_stats_json || "[]"));
      if (!Array.isArray(parsed)) return [];
      const byMap = {};
      parsed.forEach((row) => {
        const map = canonicalMapName(row?.map);
        if (map) byMap[map] = row;
      });
      return currentMapPool
        .map((map) => {
          const row = byMap[map];
          if (!row) return null;
          return {
            map,
            played: Number(row.played || 0),
            winRate: Number(row.win_rate || 0),
            pickRate: Number(row.pick_rate || 0),
            banRate: Number(row.ban_rate || 0),
          };
        })
        .filter(Boolean);
    } catch {
      return [];
    }
  }, [teamForm.map_stats_json]);
  const teamMapStatsTotalPlayed = useMemo(
    () => teamMapStatsRows.reduce((sum, row) => sum + Math.max(0, Number(row.played || 0)), 0),
    [teamMapStatsRows]
  );
  const toNum = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
  const batchTopRatingsProgressPct =
    batchTopRatingsTotal > 0 ? Math.min(100, Math.max(0, (batchTopRatingsProcessed / batchTopRatingsTotal) * 100)) : 0;
  const showBatchTopRatingsProgress = batchTopRatingsStatus !== "idle";
  const batchTopRatingsActive = ["queued", "running", "pausing", "canceling"].includes(batchTopRatingsStatus);
  const batchTopRatingsResumable = ["paused", "failed"].includes(batchTopRatingsStatus);
  const mapStatsJobProgressPct =
    mapStatsJobTotal > 0 ? Math.min(100, Math.max(0, (mapStatsJobProcessed / mapStatsJobTotal) * 100)) : 0;
  const showMapStatsJobProgress = mapStatsJobStatus !== "idle" && mapStatsJobStatus !== "completed";
  const mapStatsJobActive = ["queued", "running", "pausing", "canceling"].includes(mapStatsJobStatus);
  const mapStatsJobResumable = ["paused", "failed"].includes(mapStatsJobStatus);
  const missingTopRatingsCount = (players || []).filter((player) => !playerHasCompleteTopRatings(player)).length;
  const batchTopRatingsStatusLabel =
    {
      completed: "Completed",
      failed: "Failed",
      canceled: "Canceled",
      canceling: "Canceling",
      paused: "Paused",
      pausing: "Pausing",
      running: "Running",
      queued: "Queued",
    }[batchTopRatingsStatus] || "Queued";
  const mapStatsJobStatusLabel =
    {
      completed: "Completed",
      failed: "Failed",
      canceled: "Canceled",
      canceling: "Canceling",
      paused: "Paused",
      pausing: "Pausing",
      running: "Running",
      queued: "Queued",
    }[mapStatsJobStatus] || "Queued";
  const playerTopxBucketRows = useMemo(() => {
    const rows = Array.isArray(playerCurve?.bucket_rows) ? playerCurve.bucket_rows : [];
    return rows
      .map((row) => ({
        tier: Number(row?.tier),
        tierLabel: String(row?.tier_label || `Top ${Number(row?.tier)}`),
        rankMidpoint: Number(row?.rank_midpoint),
        bucketRating: Number.isFinite(Number(row?.bucket_rating)) ? Number(row.bucket_rating) : null,
        bucketDelta: Number.isFinite(Number(row?.bucket_delta)) ? Number(row.bucket_delta) : null,
        rawBucketDelta: Number.isFinite(Number(row?.raw_bucket_delta)) ? Number(row.raw_bucket_delta) : null,
        shrinkageWeight: Number.isFinite(Number(row?.shrinkage_weight)) ? Number(row.shrinkage_weight) : null,
        maps: Number(row?.maps || 0),
      }))
      .filter((row) => Number.isFinite(row.tier) && row.tier > 0 && row.tier < 100)
      .sort((a, b) => a.tier - b.tier);
  }, [playerCurve]);
  const playerTopxRows = useMemo(() => {
    const rows = Array.isArray(playerCurve?.graph_rows) ? playerCurve.graph_rows : [];
    if (rows.length === 0) {
      return playerTopxBucketRows
        .map((row) => ({
          rank: row.tier,
          rankLabel: String(row.tier),
          bucketRating: row.bucketRating,
        }))
        .filter((row) => Number.isFinite(row.rank) && row.rank > 0 && row.bucketRating !== null);
    }
    return rows
      .map((row) => ({
        rank: Number(row?.rank),
        rankLabel: String(row?.rank_label || row?.rank || ""),
        bucketRating: Number.isFinite(Number(row?.bucket_rating)) ? Number(row.bucket_rating) : null,
      }))
      .filter((row) => Number.isFinite(row.rank) && row.rank > 0 && row.bucketRating !== null)
      .sort((a, b) => a.rank - b.rank);
  }, [playerCurve, playerTopxBucketRows]);
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
        case "hltv_points_asc":
          return toNum(a.hltv_points) - toNum(b.hltv_points);
        case "hltv_points_desc":
          return toNum(b.hltv_points) - toNum(a.hltv_points);
        case "vrs_asc":
          return toNum(a.vrs_rank, 9999) - toNum(b.vrs_rank, 9999);
        case "vrs_desc":
          return toNum(b.vrs_rank, 9999) - toNum(a.vrs_rank, 9999);
        case "vrs_points_asc":
          return toNum(a.vrs_points) - toNum(b.vrs_points);
        case "vrs_points_desc":
          return toNum(b.vrs_points) - toNum(a.vrs_points);
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
                {batchTopRatingsActive && batchTopRatingsJobId && (
                  <button className="danger" onClick={cancelBatchTopRatingsJob} disabled={batchTopRatingsStatus === "canceling"}>
                    {batchTopRatingsStatus === "canceling" ? "Canceling..." : "Cancel"}
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
                  {["queued", "running", "pausing", "canceling"].includes(batchTopRatingsStatus) && batchTopRatingsTotal > batchTopRatingsProcessed
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
                  <SortHeader sortValue={playerSort} asc="name_asc" desc="name_desc" onChange={setPlayerSort}>Name</SortHeader>
                  <SortHeader sortValue={playerSort} asc="id_asc" desc="id_desc" onChange={setPlayerSort}>ID</SortHeader>
                  <SortHeader sortValue={playerSort} asc="team_asc" desc="team_desc" onChange={setPlayerSort}>Team</SortHeader>
                  <SortHeader sortValue={playerSort} asc="rating_asc" desc="rating_desc" defaultDirection="desc" onChange={setPlayerSort}>Rating</SortHeader>
                  <SortHeader
                    sortValue={playerSort}
                    asc="boost_asc"
                    desc="boost_desc"
                    defaultDirection="desc"
                    onChange={setPlayerSort}
                    title="Boosters and Roles imported"
                  >
                    Boost/Role
                  </SortHeader>
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
                  {!playerCurveLoading && !playerCurveError && playerTopxBucketRows.length === 0 && (
                    <p className="muted">No adjusted Top-X bucket data available yet.</p>
                  )}
                  {playerTopxBucketRows.length > 0 && (
                    <>
                      <p className="muted">
                        Overall rating: {Number(playerCurve?.base_rating || playerForm.rating || 0).toFixed(3)} | Sample maps:{" "}
                        {Math.round(Number(playerCurve?.sample_maps || 0))} | Weight base:{" "}
                        {Math.round(Number(playerCurve?.total_maps_proxy || 0))}
                      </p>
                      <div className="value-chart-wrap">
                        <ResponsiveContainer width="100%" height={320}>
                          <ComposedChart data={playerTopxRows} margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                            <CartesianGrid stroke="#284061" strokeDasharray="3 3" />
                            <XAxis
                              type="number"
                              dataKey="rank"
                              domain={[1, 50]}
                              ticks={[1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]}
                              interval={0}
                              minTickGap={0}
                              tick={{ fill: "#9fc5ff", fontSize: 12 }}
                              axisLine={{ stroke: "#365a89" }}
                              tickLine={{ stroke: "#365a89" }}
                              tickFormatter={(v) => String(v)}
                            />
                            <YAxis
                              tick={{ fill: "#9fc5ff", fontSize: 12 }}
                              axisLine={{ stroke: "#365a89" }}
                              tickLine={{ stroke: "#365a89" }}
                              domain={playerTopxRatingAxis.domain}
                              ticks={playerTopxRatingAxis.ticks}
                              interval={0}
                              minTickGap={0}
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
                                return `Rank ${row.rankLabel || row.rank || "-"}`;
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
                            <th>Sample Weight</th>
                            <th>Maps</th>
                          </tr>
                        </thead>
                        <tbody>
                          {playerTopxBucketRows.map((row) => (
                            <tr key={`topx-row-${row.tier}`}>
                              <td>{row.tierLabel}</td>
                              <td>{Number.isFinite(row.bucketRating) ? row.bucketRating.toFixed(3) : "-"}</td>
                              <td>
                                {Number.isFinite(row.bucketDelta)
                                  ? `${row.bucketDelta >= 0 ? "+" : ""}${row.bucketDelta.toFixed(3)}`
                                  : "-"}
                              </td>
                              <td>{Number.isFinite(row.shrinkageWeight) ? `${Math.round(row.shrinkageWeight * 100)}%` : "-"}</td>
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
                <div className="grid two">
                  <Input
                    label="HLTV Team ID"
                    value={teamForm.hltv_team_id}
                    onChange={(v) => setTeamForm({ ...teamForm, hltv_team_id: v })}
                    placeholder="e.g. 12468"
                  />
                </div>
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

              <section className="team-detail-block">
                <div className="team-detail-heading-row">
                  <div>
                    <h4 className="team-detail-heading">Map Stats</h4>
                    {teamMapStatsRows.length > 0 && (
                      <p className="muted">Last 3 months: {teamMapStatsTotalPlayed.toLocaleString()} maps played</p>
                    )}
                  </div>
                  <button className="secondary" onClick={refreshSelectedTeamMapStats} disabled={!selectedTeam || mapStatsJobActive}>
                    {mapStatsJobActive ? "Importing..." : "Import Map Stats"}
                  </button>
                </div>
                {showMapStatsJobProgress && (
                  <div className="team-map-stats-progress">
                    <p className="muted">
                      {mapStatsJobProcessed.toLocaleString()} / {mapStatsJobTotal.toLocaleString()} | ok {mapStatsJobOk} | failed{" "}
                      {mapStatsJobFailed}
                      {mapStatsJobActive && mapStatsJobTotal > mapStatsJobProcessed ? ` | ETA: ${formatBatchEta(mapStatsJobEtaSeconds)}` : ""}
                    </p>
                    <div className="progress">
                      <div className="progress-bar determinate" style={{ width: `${mapStatsJobProgressPct}%` }} />
                    </div>
                    <p className="muted">Status: {mapStatsJobStatusLabel}</p>
                  </div>
                )}
                <div className="team-map-stats-grid">
                  {teamMapStatsRows.length > 0 ? (
                    teamMapStatsRows.map((row) => (
                      <div className="team-map-stat-card" key={`team-map-${row.map}`}>
                        <div className="team-map-stat-title">{row.map}</div>
                        <div className="team-map-stat-main">{formatTeamPct(row.winRate, 1)}</div>
                        <div className="team-map-stat-split">
                          <span>Pick {formatTeamPct(row.pickRate, 1)}</span>
                          <span>Ban {formatTeamPct(row.banRate, 1)}</span>
                          <span>{Number(row.played || 0)} maps</span>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="team-map-stat-empty">No current-pool map stats imported</div>
                  )}
                </div>
              </section>
            </div>
            <div className="actions">
              <button className="primary" onClick={saveTeam} disabled={!teamForm.name}>
                Save Team
              </button>
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
                <button
                  className="secondary"
                  onClick={() => startMapStatsImportJob(false)}
                  disabled={mapStatsJobActive || rankingsRefreshBusy || teams.length === 0}
                >
                  {mapStatsJobActive ? `Importing Map Stats ${mapStatsJobProcessed}/${mapStatsJobTotal}` : "Import All Map Stats"}
                </button>
                {mapStatsJobActive && mapStatsJobId && (
                  <button className="secondary" onClick={pauseMapStatsJob} disabled={mapStatsJobStatus === "pausing"}>
                    {mapStatsJobStatus === "pausing" ? "Pausing..." : "Pause"}
                  </button>
                )}
                {mapStatsJobActive && mapStatsJobId && (
                  <button className="danger" onClick={cancelMapStatsJob} disabled={mapStatsJobStatus === "canceling"}>
                    {mapStatsJobStatus === "canceling" ? "Canceling..." : "Cancel"}
                  </button>
                )}
                {mapStatsJobResumable && mapStatsJobId && (
                  <button className="secondary" onClick={resumeMapStatsJob}>
                    Resume
                  </button>
                )}
                <span className="teams-meta">{filteredSortedTeams.length} teams shown</span>
              </div>
              <div className="grid two teams-filters">
                <Input label="Search Teams" value={teamSearch} onChange={setTeamSearch} placeholder="Name, ID, or player" />
              </div>
            </div>
            {showMapStatsJobProgress && (
              <div className="card sub">
                <p className="muted">
                  Map stats progress: {mapStatsJobProcessed.toLocaleString()} / {mapStatsJobTotal.toLocaleString()} | ok {mapStatsJobOk} |
                  failed {mapStatsJobFailed}
                  {mapStatsJobActive && mapStatsJobTotal > mapStatsJobProcessed ? ` | ETA: ${formatBatchEta(mapStatsJobEtaSeconds)}` : ""}
                </p>
                <div className="progress">
                  <div className="progress-bar determinate" style={{ width: `${mapStatsJobProgressPct}%` }} />
                </div>
                <p className="muted">Status: {mapStatsJobStatusLabel}</p>
                {mapStatsJobLastError && <p className="muted">Last error: {mapStatsJobLastError}</p>}
              </div>
            )}
            <table>
              <thead>
                <tr>
                  <SortHeader sortValue={teamSort} asc="name_asc" desc="name_desc" onChange={setTeamSort}>Name</SortHeader>
                  <SortHeader sortValue={teamSort} asc="id_asc" desc="id_desc" onChange={setTeamSort}>ID</SortHeader>
                  <SortHeader sortValue={teamSort} asc="hltv_asc" desc="hltv_desc" onChange={setTeamSort}>HLTV Rank</SortHeader>
                  <SortHeader sortValue={teamSort} asc="hltv_points_asc" desc="hltv_points_desc" defaultDirection="desc" onChange={setTeamSort}>HLTV Points</SortHeader>
                  <SortHeader sortValue={teamSort} asc="vrs_asc" desc="vrs_desc" onChange={setTeamSort}>VRS Rank</SortHeader>
                  <SortHeader sortValue={teamSort} asc="vrs_points_asc" desc="vrs_points_desc" defaultDirection="desc" onChange={setTeamSort}>VRS Points</SortHeader>
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
  const [simulatorUrl, setSimulatorUrl] = useState("https://www.hltv.org/events/8914/xse-pro-league-2026#simulator");
  const [simulatorBusy, setSimulatorBusy] = useState(false);
  const [simulatorResult, setSimulatorResult] = useState(null);

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

  const inferHltvSimulatorPairing = async () => {
    if (!simulatorUrl.trim()) {
      setImportResult("Paste an HLTV event simulator URL first.");
      return;
    }
    setSimulatorBusy(true);
    setImportResult("");
    setSimulatorResult(null);
    try {
      const res = await requestJson(
        "/admin/infer-hltv-simulator-pairing",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: simulatorUrl.trim() }),
        },
        90000
      );
      setSimulatorResult(res);
      const best = res.inference?.best;
      setImportResult(
        best
          ? `Pairing probe complete: best candidate ${best.label} (${Math.round((best.score || 0) * 100)}%).`
          : "Pairing probe complete, but no matching candidate could be scored."
      );
      notify("HLTV simulator pairing probed");
    } catch (e) {
      setImportResult(e?.message || "Failed to infer HLTV simulator pairing.");
    } finally {
      setSimulatorBusy(false);
    }
  };

  return (
    <div className="stack">
      <Section title="Data Management Tools">
        <div className="tab-bar small">
          <button className={dataTab === "trigger" ? "tab active" : "tab"} onClick={() => setDataTab("trigger")}>
            Trigger Rates
          </button>
          <button className={dataTab === "simulator" ? "tab active" : "tab"} onClick={() => setDataTab("simulator")}>
            HLTV Simulator
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

        {dataTab === "simulator" && (
          <div className="stack">
            <label className="field">
              <span>HLTV Event Simulator URL</span>
              <input
                value={simulatorUrl}
                onChange={(e) => setSimulatorUrl(e.target.value)}
                placeholder="https://www.hltv.org/events/8914/xse-pro-league-2026#simulator"
              />
            </label>
            <div className="actions">
              <button className="primary" onClick={inferHltvSimulatorPairing} disabled={simulatorBusy}>
                {simulatorBusy ? "Running Probe..." : "Infer Pairing Algorithm"}
              </button>
              <button className="secondary" onClick={() => api.openExternal(simulatorUrl)} disabled={!simulatorUrl.trim()}>
                Open In Browser
              </button>
            </div>
            <p className="muted">
              Opens HLTV in the existing browser profile, clicks several first-round outcome patterns, captures the generated next-round pairings, and scores candidate pairing rules.
            </p>
            {simulatorResult && (
              <>
                <div className="card sub">
                  <h4>Pairing Probe Summary</h4>
                  <p className="muted">Status: {simulatorResult.ok === false ? "Failed" : "Complete"}</p>
                  <p className="muted">URL: {simulatorResult.url}</p>
                  {Number.isFinite(Number(simulatorResult.extracted_team_count)) && (
                    <p className="muted">
                      Teams detected: {simulatorResult.extracted_team_count} | Rank source: {simulatorResult.rank_source || "unknown"}
                    </p>
                  )}
                  {Array.isArray(simulatorResult.extracted_team_names) && simulatorResult.extracted_team_names.length > 0 && (
                    <p className="muted">Extracted names: {simulatorResult.extracted_team_names.slice(0, 24).join(", ")}</p>
                  )}
                  {simulatorResult.error && <p className="danger-text">{simulatorResult.error}</p>}
                  {simulatorResult.inference?.best && (
                    <>
                      <p className="muted">
                        Best candidate: {simulatorResult.inference.best.label} (
                        {Math.round((simulatorResult.inference.best.score || 0) * 100)}%,{" "}
                        {simulatorResult.inference.best.matched || 0}/{simulatorResult.inference.best.total || 0} pairs)
                      </p>
                    </>
                  )}
                </div>
                {(simulatorResult.inference?.candidates || []).length > 0 && (
                  <div className="card sub">
                    <h4>Candidate Scores</h4>
                    <table>
                      <thead>
                        <tr>
                          <th>Rule</th>
                          <th>Score</th>
                          <th>Matched</th>
                        </tr>
                      </thead>
                      <tbody>
                        {simulatorResult.inference.candidates.map((candidate) => (
                          <tr key={candidate.mode}>
                            <td>{candidate.label}</td>
                            <td>{Math.round((candidate.score || 0) * 100)}%</td>
                            <td>
                              {candidate.matched || 0}/{candidate.total || 0}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {(simulatorResult.scenarios || []).length > 0 && (
                  <div className="card sub">
                    <h4>Observed Scenarios</h4>
                    {(simulatorResult.scenarios || []).map((scenario) => (
                      <div key={scenario.name} className="stack compact">
                        <h5>{scenario.name}</h5>
                        {scenario.error && <p className="danger-text">{scenario.error}</p>}
                        <p className="muted">
                          Winners: {(scenario.winners || []).join(", ") || "None captured"}
                        </p>
                        <p className="muted">
                          Winner clicks: {scenario.clicks_succeeded ?? 0}/{(scenario.clicks || []).length} | Advance clicks:{" "}
                          {(scenario.advance_result?.clicked || []).join(", ") || "None"}
                        </p>
                        <p className="muted">
                          1-0: {(scenario.after?.["1:0"] || []).map((p) => `${p.team_a} vs ${p.team_b}`).join(" | ") || "None captured"}
                        </p>
                        <p className="muted">
                          0-1: {(scenario.after?.["0:1"] || []).map((p) => `${p.team_a} vs ${p.team_b}`).join(" | ") || "None captured"}
                        </p>
                        {scenario.after_snapshot?.buckets && (
                          <p className="muted">
                            Bucket teams: 1-0 [{(scenario.after_snapshot.buckets["1:0"]?.teams || []).join(", ") || "-"}] | 0-1 [
                            {(scenario.after_snapshot.buckets["0:1"]?.teams || []).join(", ") || "-"}]
                          </p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </>
            )}
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

function ModelLabTab() {
  const [trainLimit, setTrainLimit] = useState("0");
  const [testLimit, setTestLimit] = useState("0");
  const [randomSplit, setRandomSplit] = useState(false);
  const [fetchMissingMapStats, setFetchMissingMapStats] = useState(false);
  const [dbMatchCount, setDbMatchCount] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [selectedBreakdownRow, setSelectedBreakdownRow] = useState(null);

  const pct = (value, digits = 1) => {
    const n = Number(value);
    return Number.isFinite(n) ? `${(n * 100).toFixed(digits)}%` : "-";
  };
  const num = (value, digits = 3) => {
    const n = Number(value);
    return Number.isFinite(n) ? n.toFixed(digits) : "-";
  };
  const featureLabel = (feature) =>
    ({
      hltv_gap: "HLTV rank gap",
      hltv_level: "HLTV matchup level",
      hltv_gap_level: "HLTV gap x level",
      vrs_gap: "VRS rank gap",
      vrs_level: "VRS matchup level",
      vrs_gap_level: "VRS gap x level",
      map_win_gap: "Map win gap",
      pick_gap: "Pick gap",
      ban_gap: "Ban gap",
      played_pct_gap: "Played share gap",
      map_stats_available: "Map stats available",
    }[feature] || feature);
  const toPositiveInt = (value, fallback = 0) => {
    const parsed = Number.parseInt(String(value ?? ""), 10);
    return Number.isFinite(parsed) ? Math.max(0, parsed) : fallback;
  };

  useEffect(() => {
    let live = true;
    api
      .get("/events/hltv-results?limit=1&offset=0")
      .then((data) => {
        if (!live) return;
        setDbMatchCount(Number(data?.total || data?.count || 0));
      })
      .catch(() => {
        if (live) setDbMatchCount(0);
      });
    return () => {
      live = false;
    };
  }, []);

  const effectiveSlice = (startValue, limitValue) => {
    const rawStart = toPositiveInt(startValue, 0);
    const rawLimit = toPositiveInt(limitValue, 0);
    const dbTotal = Math.max(0, Number(result?.db_matches || dbMatchCount || 0));
    if (dbTotal <= 0) {
      return { start: rawStart, length: rawLimit, end: rawStart + rawLimit };
    }
    const start = Math.min(rawStart, dbTotal);
    const length = Math.min(rawLimit, Math.max(0, dbTotal - start));
    return { start, length, end: start + length };
  };

  const sliceRangeLabel = (start, end) => {
    if (end <= start) return "none";
    return `${start.toLocaleString()}-${(end - 1).toLocaleString()}`;
  };

  const timeline = useMemo(() => {
    const dbTotal = Math.max(0, Number(result?.db_matches || dbMatchCount || 0));
    const test = effectiveSlice(0, testLimit);
    const train = effectiveSlice(test.length, trainLimit);
    const total = Math.max(dbTotal, train.end, test.end, 1);
    const overlap = Math.max(0, Math.min(train.end, test.end) - Math.max(train.start, test.start));
    const segmentStyle = (start, length) => ({
      left: `${(start / total) * 100}%`,
      width: `${length > 0 ? Math.max(1.5, (length / total) * 100) : 0}%`,
    });
    return {
      total,
      overlap,
      dbTotal,
      trainStart: train.start,
      trainEnd: train.end,
      testStart: test.start,
      testEnd: test.end,
      dbStyle: segmentStyle(0, dbTotal),
      trainStyle: segmentStyle(train.start, train.length),
      testStyle: segmentStyle(test.start, test.length),
    };
  }, [trainLimit, testLimit, dbMatchCount, result?.db_matches]);

  const rankEffectLevelBands = useMemo(
    () => (result?.rank_effect_curve?.level_bands || []).filter((band) => Array.isArray(band.rows) && band.rows.length > 0),
    [result?.rank_effect_curve?.level_bands]
  );

  const RankEffectTooltip = ({ active, payload }) => {
    if (!active || !payload?.length) return null;
    const row = payload[0]?.payload || {};
    return (
      <div className="chart-tooltip">
        <strong>Gap {num(row.gap_min, 0)} to {num(row.gap_max, 0)}</strong>
        <span>Matches {Number(row.n || 0).toLocaleString()}</span>
        <span>Avg gap {num(row.avg_gap, 2)} | Avg level {num(row.avg_level, 2)}</span>
        <span>Predicted {pct(row.predicted_winrate, 2)}</span>
        <span>Actual {pct(row.actual_winrate, 2)}</span>
      </div>
    );
  };

  const run = async () => {
    setBusy(true);
    setError("");
    setResult(null);
    setSelectedBreakdownRow(null);
    try {
      const params = new URLSearchParams({
        train_limit: String(toPositiveInt(trainLimit, 0)),
        test_limit: String(toPositiveInt(testLimit, 0)),
        random_split: randomSplit ? "true" : "false",
        fetch_missing_map_stats: fetchMissingMapStats ? "true" : "false",
      });
      const data = await api.get(`/events/hltv-results/map-model-lab?${params.toString()}`);
      if (data?.detail) {
        setError(String(data.detail));
        return;
      }
      setResult(data);
    } catch (e) {
      setError(e?.message || "Failed to run model lab.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section title="Map Model Lab">
      <div className="stack">
        <div className="grid two">
          <Input label="Train Limit" value={trainLimit} onChange={setTrainLimit} />
          <Input label="Test Limit" value={testLimit} onChange={setTestLimit} />
        </div>
        <div className="actions" style={{ marginTop: 0 }}>
          <label className="checkbox-inline">
            <input type="checkbox" checked={randomSplit} onChange={(e) => setRandomSplit(e.target.checked)} disabled={busy} />
            <span>Random train/test split</span>
          </label>
          <label className="checkbox-inline">
            <input
              type="checkbox"
              checked={fetchMissingMapStats}
              onChange={(e) => setFetchMissingMapStats(e.target.checked)}
              disabled={busy}
            />
            <span>Fetch missing historical map stats</span>
          </label>
          <button className="primary" onClick={run} disabled={busy}>
            {busy ? "Running..." : "Train & Evaluate"}
          </button>
        </div>
        <div className="model-slice-card">
          <div className="model-slice-head">
            <div>
              <h3>Stored Match Timeline</h3>
              <p className="muted">Left is newest. Ordered mode tests on newest matches and trains on the next older matches.</p>
            </div>
            <span className="pill">DB only</span>
          </div>
          <div className="model-slice-track" aria-label="Training and testing slices across stored matches">
            <div className="model-slice-zero">Newest</div>
            <div className="model-slice-end">Older</div>
            <div className="model-slice-segment db" style={timeline.dbStyle}>
              DB {timeline.dbTotal.toLocaleString()}
            </div>
            <div className="model-slice-segment test" style={timeline.testStyle}>
              Test
            </div>
            <div className="model-slice-segment train" style={timeline.trainStyle}>
              Train
            </div>
          </div>
          <div className="model-slice-meta">
            <span>DB matches {timeline.dbTotal.toLocaleString()}</span>
            <span>Test rows {sliceRangeLabel(timeline.testStart, timeline.testEnd)}</span>
            <span>Train rows {sliceRangeLabel(timeline.trainStart, timeline.trainEnd)}</span>
            {randomSplit && <span className="warning-text">Random split ignores timeline order</span>}
            {timeline.overlap > 0 && <span className="warning-text">Overlap: {timeline.overlap.toLocaleString()} matches</span>}
          </div>
        </div>
        {error && <p className="error">{error}</p>}
        {result && (
          <div className="stack">
            <div className="grid four">
              <div className="card sub">
                <h3>Train</h3>
                <p className="muted">Matches {Number(result.train?.matches_loaded || 0).toLocaleString()}</p>
                <p className="muted">Maps {Number(result.train?.map_samples || 0).toLocaleString()}</p>
                <p className="muted">{result.split?.random ? "Random sample" : "Older holdout-safe rows"}</p>
              </div>
              <div className="card sub">
                <h3>Test</h3>
                <p className="muted">Matches {Number(result.test?.matches_loaded || 0).toLocaleString()}</p>
                <p className="muted">Maps {Number(result.test?.map_samples || 0).toLocaleString()}</p>
                <p className="muted">
                  {result.split?.random ? `Random seed ${Number(result.split?.random_seed || 0).toLocaleString()}` : "Newest rows"}
                </p>
              </div>
              <div className="card sub">
                <h3>With Map Data</h3>
                <p className="muted">Winner {pct(result.metrics?.winner_accuracy, 1)}</p>
                <p className="muted">Score MAE {Number(result.metrics?.score_mae || 0).toFixed(2)}</p>
                <p className="muted">Brier {Number(result.metrics?.brier || 0).toFixed(3)}</p>
                <p className="muted">
                  Historical maps kept {pct(result.input_summary?.train?.map_stats_coverage, 1)} (
                  {Number(result.input_summary?.train?.maps || 0).toLocaleString()} /{" "}
                  {Number(result.input_summary?.train?.candidate_maps || 0).toLocaleString()})
                </p>
              </div>
              <div className="card sub">
                <h3>Rank Only</h3>
                <p className="muted">Winner {pct(result.rank_only_metrics?.winner_accuracy, 1)}</p>
                <p className="muted">Score MAE {Number(result.rank_only_metrics?.score_mae || 0).toFixed(2)}</p>
                <p className="muted">Brier {Number(result.rank_only_metrics?.brier || 0).toFixed(3)}</p>
              </div>
            </div>
            {rankEffectLevelBands.length > 0 && (
              <div className="card sub">
                <h3>Rank Gap Effect</h3>
                <p className="muted">{result.rank_effect_curve.description}</p>
                <div className="rank-level-chart-grid">
                  {rankEffectLevelBands.map((band) => (
                    <div key={band.key} className="rank-level-chart">
                      <div className="rank-level-chart-head">
                        <h4>{band.label}</h4>
                        <span>{Number(band.n || 0).toLocaleString()} maps</span>
                      </div>
                      <ResponsiveContainer width="100%" height={220}>
                        <ComposedChart data={band.rows} margin={{ top: 8, right: 12, left: 0, bottom: 12 }}>
                          <CartesianGrid strokeDasharray="3 3" stroke="#233458" />
                          <XAxis
                            dataKey="gap"
                            type="number"
                            tick={{ fill: "#9eb6dd", fontSize: 11 }}
                            label={{ value: "Rank gap", position: "insideBottom", offset: -4, fill: "#9eb6dd" }}
                          />
                          <YAxis
                            domain={[0, 1]}
                            tick={{ fill: "#9eb6dd", fontSize: 11 }}
                            tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`}
                          />
                          <Tooltip content={<RankEffectTooltip />} />
                          <Legend />
                          <Line
                            type="monotone"
                            dataKey="predicted_winrate"
                            name="Predicted"
                            stroke="#38bdf8"
                            strokeWidth={2}
                            dot={{ r: 3 }}
                            connectNulls={false}
                          />
                          <Line
                            type="monotone"
                            dataKey="actual_winrate"
                            name="Actual"
                            stroke="#fbbf24"
                            strokeWidth={2}
                            dot={{ r: 3 }}
                            connectNulls={false}
                          />
                        </ComposedChart>
                      </ResponsiveContainer>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <table>
              <thead>
                <tr>
                  <th>Map</th>
                  <th>Test Maps</th>
                  <th>Score MAE</th>
                  <th>Winner</th>
                  <th>Model</th>
                  <th>Train Samples</th>
                </tr>
              </thead>
              <tbody>
                {(result.maps || []).map((row) => (
                  <tr key={row.map}>
                    <td>{row.map}</td>
                    <td>{Number(row.n || 0).toLocaleString()}</td>
                    <td>{Number(row.score_mae || 0).toFixed(2)}</td>
                    <td>{pct(row.winner_accuracy, 1)}</td>
                    <td>{row.model_scope}</td>
                    <td>{Number(row.training_samples || 0).toLocaleString()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <table>
              <thead>
                <tr>
                  <th>Date</th>
                  <th>Match</th>
                  <th>Map</th>
                  <th>Pred</th>
                  <th>Actual</th>
                  <th>Round %</th>
                  <th>Map Win %</th>
                  <th>Error</th>
                  <th>Details</th>
                </tr>
              </thead>
              <tbody>
                {(result.rows || []).map((row, idx) => (
                  <tr key={`${row.match_url || idx}-${row.map}`} onClick={() => setSelectedBreakdownRow(row)}>
                    <td>{row.match_date || "-"}</td>
                    <td>{row.team1} vs {row.team2}</td>
                    <td>{row.map}</td>
                    <td>
                      {row.predicted_score}
                      {row.predicted_winner ? ` (${row.predicted_winner})` : ""}
                    </td>
                    <td>{row.actual_score}</td>
                    <td>{pct(row.team1_round_win_probability, 1)}</td>
                    <td>{pct(row.team1_map_win_probability, 1)}</td>
                    <td>{Number(row.score_error || 0).toFixed(0)}</td>
                    <td>Open</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="card sub">
              <p className="muted">{result.method}</p>
            </div>
          </div>
        )}
        {selectedBreakdownRow && (
          <div className="modal-backdrop" onClick={() => setSelectedBreakdownRow(null)}>
            <div className="modal team-modal" onClick={(e) => e.stopPropagation()}>
              <header className="modal-header">
                <h3 className="player-modal-title">
                  {selectedBreakdownRow.team1} vs {selectedBreakdownRow.team2} | {selectedBreakdownRow.map}
                </h3>
                <button className="close" onClick={() => setSelectedBreakdownRow(null)}>
                  x
                </button>
              </header>
              <div className="modal-body">
                <div className="grid four">
                  <div className="card sub">
                    <h3>Prediction</h3>
                    <p className="muted">Score {selectedBreakdownRow.predicted_score}</p>
                    <p className="muted">Winner {selectedBreakdownRow.predicted_winner || "-"}</p>
                  </div>
                  <div className="card sub">
                    <h3>Actual</h3>
                    <p className="muted">Score {selectedBreakdownRow.actual_score}</p>
                    <p className="muted">Winner {selectedBreakdownRow.actual_winner || "-"}</p>
                  </div>
                  <div className="card sub">
                    <h3>Round Win</h3>
                    <p className="muted">{pct(selectedBreakdownRow.team1_round_win_probability, 2)}</p>
                    <p className="muted">Logit {num(selectedBreakdownRow.feature_breakdown?.logit, 3)}</p>
                  </div>
                  <div className="card sub">
                    <h3>Map Win</h3>
                    <p className="muted">{pct(selectedBreakdownRow.team1_map_win_probability, 2)}</p>
                    <p className="muted">Model {selectedBreakdownRow.model_scope || "-"}</p>
                  </div>
                </div>

                <div className="card sub">
                  <h3>Scoreline Probabilities</h3>
                  <ResponsiveContainer width="100%" height={300}>
                    <BarChart data={selectedBreakdownRow.score_distribution || []}>
                      <CartesianGrid strokeDasharray="3 3" stroke="#233458" />
                      <XAxis dataKey="score" tick={{ fill: "#9eb6dd", fontSize: 11 }} interval={0} angle={-45} textAnchor="end" height={70} />
                      <YAxis tick={{ fill: "#9eb6dd", fontSize: 12 }} tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`} />
                      <Tooltip formatter={(value) => pct(value, 2)} labelStyle={{ color: "#0f172a" }} />
                      <Bar dataKey="probability" fill="#38bdf8" />
                    </BarChart>
                  </ResponsiveContainer>
                </div>

                <table>
                  <thead>
                    <tr>
                      <th>Factor</th>
                      <th>Value</th>
                      <th>Baseline</th>
                      <th>Std</th>
                      <th>Coef</th>
                      <th>Contribution</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td>Intercept</td>
                      <td>-</td>
                      <td>-</td>
                      <td>-</td>
                      <td>-</td>
                      <td>{num(selectedBreakdownRow.feature_breakdown?.intercept, 4)}</td>
                    </tr>
                    {(selectedBreakdownRow.feature_breakdown?.features || []).map((feature) => (
                      <tr key={feature.feature}>
                        <td>{featureLabel(feature.feature)}</td>
                        <td>{num(feature.value, 4)}</td>
                        <td>{num(feature.mean, 4)}</td>
                        <td>{num(feature.standardized, 4)}</td>
                        <td>{num(feature.coefficient, 4)}</td>
                        <td>{num(feature.contribution, 4)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <button className="secondary" onClick={() => setSelectedBreakdownRow(null)}>
                  Close
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </Section>
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
    setBoMode("elim_qual");
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
          bo="elim_qual"
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
          bo="elim_qual"
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
      case "cpp_asc":
        arr.sort((a, b) => a.total_ev / (a.cost || 1) - b.total_ev / (b.cost || 1));
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
    modelLab: <ModelLabTab />,
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

