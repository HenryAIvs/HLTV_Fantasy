import React, { useEffect, useMemo, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ComposedChart,
  Legend,
  Line,
  Pie,
  PieChart,
  Rectangle,
  ResponsiveContainer,
  Scatter,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import roleBadgesUrl from "./assets/role_sprites.png";
import boosterBadgesUrl from "./assets/booster_sprites.png";

// Glyph sprite sheets: transparent-background glyphs, one per grid cell,
// centered and sized uniformly (see scratchpad process_sprites.py, which
// un-blended the original navy background and recentered each cell).
// Both sheets are in id order (row-major).
const SpriteBadge = ({ url, cols, rows, index, size, className }) => {
  const idx = Number(index);
  if (!Number.isFinite(idx) || idx < 0 || idx >= cols * rows) return null;
  const col = idx % cols;
  const row = Math.floor(idx / cols);
  // Geometry goes through CSS variables so stylesheet rules can render the
  // sprite either as a normal image (default) or as a mask filled with a flat
  // medal color (gold/silver/bronze cards) — inline background-* would win
  // over any stylesheet override.
  return (
    <span
      className={className}
      style={{
        width: size,
        height: size,
        "--sprite-url": `url(${url})`,
        "--sprite-size": `${cols * 100}% ${rows * 100}%`,
        "--sprite-pos": `${(col * 100) / (cols - 1)}% ${(row * 100) / (rows - 1)}%`,
      }}
    />
  );
};

// role_sprites.png — 4x3, role-id order: 0 crosshair (Main AWP), 1 soldier
// (Support), 2 AK-on-T (Attacker), 3 IGL helmet (Leader), 4 rising chart
// (Stathunter), 5 bullets (Entry Fragger), 6 tent+campfire (Camper), 7 CT
// eagle (Defender), 8 headshot burst (HS Machine), 9 falling chart (Noob),
// 10 three skulls (Multi Fragger), 11 UMP (Eco Friendly).
const RoleBadge = ({ roleId, size = 68 }) => (
  <SpriteBadge url={roleBadgesUrl} cols={4} rows={3} index={Number(roleId)} size={size} className="role-badge" />
);

const ROLE_NAMES = {
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

// Short round labels for the playoff EV per-round splits / reach odds.
const STAGE_SHORT = {
  round_of_32: "R32",
  round_of_16: "R16",
  quarters: "QF",
  semis: "SF",
  final: "Final",
  third_place: "3rd",
};
const STAGE_FULL = {
  round_of_32: "Round of 32",
  round_of_16: "Round of 16",
  quarters: "Quarter-final",
  semis: "Semi-final",
  final: "Final",
  third_place: "3rd place",
};

// booster_sprites.png — 5x4 (18 icons, last two cells empty), booster-id order
// EXCEPT cells 14/15: the sheet draws shot-in-the-back at cell 14 and the
// arms-raised hero at 15, which semantically are Cannon fodder (15) and
// Carry (14) respectively — hence the swap. 0 crossed pistols (Best Pistol
// Round), 1 red down arrow (Bottom of scoreboard), 2 CLUTCH, 3 green up arrow
// (Top of scoreboard), 4 trade arrows (Avenger), 5 fish hook (Bait), 6 knife
// (Rambo), 7 flash burst (Flash), 8 scales (Mister consistent), 9 grenade
// (Kobe), 10 runner (Saver), 11 helping-up (Assist), 12 robot (Aim bot),
// 13 4x skull (Quad), 16 UZI+dollars (Farmer), 17 flame (Hellcase).
const BOOSTER_BADGE_SPRITE = { 14: 15, 15: 14 };
const BoosterBadge = ({ boosterId, size = 68 }) => {
  const id = Number(boosterId);
  const idx = BOOSTER_BADGE_SPRITE[id] !== undefined ? BOOSTER_BADGE_SPRITE[id] : id;
  return <SpriteBadge url={boosterBadgesUrl} cols={5} rows={4} index={idx} size={size} className="role-badge booster-badge" />;
};

// Cached HLTV images served by the backend (see backend/services/image_cache).
// Both components fall back to an initials chip when the image isn't cached.
const ASSETS_BASE = "http://127.0.0.1:8000/assets";

const initialsOf = (name) => {
  const parts = String(name || "").trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
  return String(name || "?").slice(0, 2).toUpperCase();
};

// Stored dates are ISO (YYYY-MM-DD); the UI shows day/month/year.
const formatDMY = (iso) => {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || "").trim());
  if (m) return `${m[3]}/${m[2]}/${m[1]}`;
  return String(iso || "").trim() || "-";
};

function TeamLogo({ hltvTeamId, name, size = 22, className = "" }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [hltvTeamId]);
  const id = Number(hltvTeamId);
  if (!Number.isFinite(id) || id <= 0 || failed) {
    return (
      <span className={`img-fallback ${className}`} style={{ width: size, height: size, fontSize: Math.max(8, size * 0.36) }}>
        {initialsOf(name)}
      </span>
    );
  }
  return (
    <img
      className={`team-logo-img ${className}`}
      src={`${ASSETS_BASE}/team/${id}?v=2`}
      width={size}
      height={size}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

function PlayerPhoto({ playerId, name, size = 26, className = "" }) {
  const [failed, setFailed] = useState(false);
  useEffect(() => setFailed(false), [playerId]);
  const id = Number(playerId);
  if (!Number.isFinite(id) || id <= 0 || failed) {
    return (
      <span className={`img-fallback round ${className}`} style={{ width: size, height: size, fontSize: Math.max(8, size * 0.36) }}>
        {initialsOf(name)}
      </span>
    );
  }
  return (
    <img
      className={`player-photo-img ${className}`}
      src={`${ASSETS_BASE}/player/${id}?v=2`}
      width={size}
      height={size}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}

const tabs = [
  { key: "view", label: "Database" },
  { key: "events", label: "Events" },
  { key: "tournament", label: "Tournament" },
  { key: "devlab", label: "Dev Lab" },
  { key: "scheduling", label: "Scheduling" },
];

const ACTIVE_MAP_POOL = ["Mirage", "Inferno", "Nuke", "Ancient", "Anubis", "Dust2", "Cache"];

// Map identity colors — vibrant takes on the familiar map hues (user-tuned:
// Inferno is dark blue, per HLTV's scheme). Identity never relies on color
// alone — every bar/card is labeled with its map.
const MAP_BAR_COLORS = {
  Dust2: "#eab308", // vivid gold
  Inferno: "#3e63dd", // dark royal blue
  Nuke: "#f4694b", // hot terracotta
  Ancient: "#2fbf71", // emerald
  Anubis: "#ec4899", // hot magenta
  Mirage: "#a855f7", // electric purple
  Cache: "#38bdf8", // bright sky blue
  Overpass: "#14b8a6", // vivid teal
  Vertigo: "#818cf8", // indigo
  Train: "#94a3b8", // railyard steel
};
const MAP_BAR_FALLBACK_COLOR = "#64748b";

const parseJsonSafe = async (res) => {
  const text = await res.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { detail: text };
  }
};

const requestJson = async (path, init = {}, timeoutMs = 60000) => {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`http://127.0.0.1:8000${path}`, { ...init, signal: controller.signal });
  } catch (e) {
    if (e?.name === "AbortError") {
      throw new Error("Request timed out — the backend may still be working. Wait a moment and retry.");
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
        Number(comps?.total_points),
        Number(comps?.total),
        Number(comps?.expected_total_points),
        Number(comps?.rating_points_total) +
          Number(comps?.win_points_total) +
          Number(comps?.role_points_total) +
          Number(comps?.booster_points_total),
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
  // 2D nearest-dot hover: the scatter shapes record their pixel positions and
  // mouse-move picks the closest dot in BOTH axes (the built-in tooltip only
  // matches on x, which picks the wrong player when prices tie).
  const [hover, setHover] = useState(null);
  const wrapRef = useRef(null);
  const dotPosRef = useRef(new Map());
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

  // Reset the dot-position map during render (before the scatter shapes
  // re-register) whenever the data changes; an effect would wipe it AFTER the
  // shapes ran and leave it empty until the next re-render.
  const lastChartRowsRef = useRef(null);
  if (lastChartRowsRef.current !== chartRows) {
    lastChartRowsRef.current = chartRows;
    dotPosRef.current = new Map();
  }
  useEffect(() => {
    setHover(null);
  }, [chartRows]);

  // Plain DOM mouse tracking on the wrapper: independent of recharts' event
  // plumbing, matched against the dots' recorded svg positions (the svg sits
  // at the wrapper's 6px padding offset).
  const handleWrapMove = (e) => {
    const rect = wrapRef.current?.getBoundingClientRect();
    if (!rect) return;
    const x = e.clientX - rect.left - 6;
    const y = e.clientY - rect.top - 6;
    let bestId = null;
    let bestPos = null;
    let bestD2 = Infinity;
    dotPosRef.current.forEach((pos, id) => {
      const dx = pos.cx - x;
      const dy = pos.cy - y;
      const d2 = dx * dx + dy * dy;
      if (d2 < bestD2) {
        bestD2 = d2;
        bestId = id;
        bestPos = pos;
      }
    });
    if (bestId != null && bestD2 <= 28 * 28) {
      setHover((prev) => {
        if (prev?.id === bestId) return prev;
        const row = chartRows.find((r) => Number(r.player_id) === Number(bestId));
        return row ? { id: bestId, cx: bestPos.cx, cy: bestPos.cy, row } : null;
      });
    } else {
      setHover(null);
    }
  };

  return (
    <div className="card sub">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">Generate player valuation data first to view price vs points.</p>
      ) : (
        <div className="stack">
          <div
            className="value-chart-wrap value-scatter"
            ref={wrapRef}
            onMouseMove={handleWrapMove}
            onMouseLeave={() => setHover(null)}
            onClick={() => {
              if (hover?.row && onPointClick) onPointClick(hover.row);
            }}
          >
            <div className="value-trend-label">
              Average line: points = {intercept.toFixed(2)} + {(slope * 1000).toFixed(3)} per $1k of price
            </div>
            {hover && (
              <div
                className="value-point-tooltip"
                style={{
                  left: (wrapRef.current?.clientWidth || 9999) - hover.cx < 250 ? hover.cx - 14 : hover.cx + 14,
                  top: Math.max(8, hover.cy - 24),
                  transform:
                    (wrapRef.current?.clientWidth || 9999) - hover.cx < 250 ? "translateX(-100%)" : "none",
                }}
              >
                <div style={{ fontWeight: 700 }}>{hover.row.name}</div>
                <div>Points: {Number(hover.row.points).toFixed(2)}</div>
                <div>Average line: {Number(hover.row.trend).toFixed(2)}</div>
                <div>
                  Distance: {Number(hover.row.distance) >= 0 ? "+" : ""}
                  {Number(hover.row.distance).toFixed(2)}
                </div>
                <div>Price: ${Number(hover.row.price).toLocaleString()}</div>
              </div>
            )}
            <ResponsiveContainer width="100%" height={380}>
              <ComposedChart
                data={chartRows}
                margin={{ top: 12, right: 18, left: 14, bottom: 26 }}
                accessibilityLayer={false}
              >
                <CartesianGrid stroke="#232a34" strokeDasharray="3 3" />
                <XAxis
                  type="number"
                  dataKey="price"
                  domain={[minPrice, maxPrice]}
                  tick={{ fill: "#9fb2c9", fontSize: 12 }}
                  tickMargin={8}
                  tickFormatter={(v) => String(Math.round(Number(v) / 1000))}
                  axisLine={{ stroke: "#3a4452" }}
                  tickLine={{ stroke: "#3a4452" }}
                  name="Price"
                  label={{ value: "Price ($k)", position: "insideBottom", offset: -18, fill: "#9fb2c9", fontSize: 12.5 }}
                />
                <YAxis
                  type="number"
                  dataKey="points"
                  tick={{ fill: "#9fb2c9", fontSize: 12 }}
                  tickMargin={7}
                  axisLine={{ stroke: "#3a4452" }}
                  tickLine={{ stroke: "#3a4452" }}
                  name="Points"
                  label={{
                    value: "Expected points",
                    angle: -90,
                    position: "insideLeft",
                    offset: 2,
                    fill: "#9fb2c9",
                    fontSize: 12.5,
                    style: { textAnchor: "middle" },
                  }}
                />
                <Legend verticalAlign="top" height={30} wrapperStyle={{ color: "#9fb2c9" }} />
                <Line
                  type="linear"
                  dataKey="trend"
                  name="Average line"
                  stroke="#35a2ff"
                  strokeWidth={2}
                  dot={false}
                  activeDot={false}
                  isAnimationActive={false}
                />
                <Scatter
                  name="Players"
                  dataKey="points"
                  fill="#4fc3ff"
                  isAnimationActive={false}
                  shape={(props) => {
                    const { cx, cy, payload: pt } = props;
                    if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
                    const pid = Number(pt?.player_id);
                    if (Number.isFinite(pid)) dotPosRef.current.set(pid, { cx, cy });
                    const active = hover != null && pid === Number(hover.id);
                    return (
                      <g>
                        {active && <circle cx={cx} cy={cy} r={9.5} fill="none" stroke="#ff8a47" strokeWidth={2} />}
                        <circle cx={cx} cy={cy} r={active ? 6 : 4.5} fill={active ? "#ff8a47" : "#4fc3ff"} />
                      </g>
                    );
                  }}
                />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
          {showTable && (
            <>
              <div className="grid two">
                <Input label="Search Players" value={search} onChange={setSearch} placeholder="Player name" />
              </div>
              <table>
                <thead>
                  <tr>
                    <SortHeader sortValue={sortBy} asc="name_asc" desc="name_desc" onChange={setSortBy}>Player</SortHeader>
                    <SortHeader sortValue={sortBy} asc="price_asc" desc="price_desc" onChange={setSortBy}>Price</SortHeader>
                    <SortHeader sortValue={sortBy} asc="points_asc" desc="points_desc" defaultDirection="desc" onChange={setSortBy}>Points</SortHeader>
                    <SortHeader sortValue={sortBy} asc="distance_asc" desc="distance_desc" defaultDirection="desc" onChange={setSortBy}>Distance</SortHeader>
                    <SortHeader sortValue={sortBy} asc="on_line_asc" desc="on_line_desc" defaultDirection="desc" onChange={setSortBy}>Expected Points</SortHeader>
                  </tr>
                </thead>
                <tbody>
                  {filteredRows.map((r) => (
                    <tr key={`dist-${r.player_id}`}>
                      <td>
                        <div className="table-player-cell">
                          <PlayerPhoto playerId={Number(r.player_id)} name={r.name} size={28} />
                          <span>{r.name}</span>
                        </div>
                      </td>
                      <td>${Number(r.price).toLocaleString()}</td>
                      <td>{r.points.toFixed(2)}</td>
                      <td>{r.distance >= 0 ? "+" : ""}{r.distance.toFixed(2)}</td>
                      <td>{r.on_line.toFixed(2)}</td>
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

function GroupPlayerBreakdownModal({ player, teamLookup, onClose }) {
  if (!player) return null;
  const rating = Number(player.rating || 0);
  const win = Number(player.win || 0);
  const role = Number(player.role || 0);
  const booster = Number(player.booster || 0);
  const total = Number.isFinite(Number(player.total)) && Number(player.total) !== 0
    ? Number(player.total)
    : rating + win + role + booster;
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header">
          <h3>
            {player.name || `Player ${player.player_id}`}
            {player.team_id ? ` (${teamLookup[player.team_id] || player.team_id})` : ""} Point Sources
          </h3>
          <button className="close" onClick={onClose}>
            &times;
          </button>
        </header>
        <div className="modal-body">
          <p className="muted">
            {player.note || "Expected points across every exact group outcome, weighted by its probability."}
          </p>
          <table>
            <thead>
              <tr>
                <th>Source</th>
                <th>{player.note ? "Points" : "Expected Points"}</th>
              </tr>
            </thead>
            <tbody>
              <tr><td>Rating</td><td>{rating.toFixed(2)}</td></tr>
              <tr><td>Win</td><td>{win.toFixed(2)}</td></tr>
              <tr><td>Role</td><td>{role.toFixed(2)}</td></tr>
              <tr><td>Booster</td><td>{booster.toFixed(2)}</td></tr>
              <tr>
                <td><strong>Total</strong></td>
                <td><strong>{total.toFixed(2)}</strong></td>
              </tr>
            </tbody>
          </table>
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
  eventSeeds = null,
  eventSwissInfo = null,
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
    // Seed order: the event page's official seeding when detected, VRS ranks
    // otherwise. Bo mode comes from the event's detected format rules.
    const vrs = {};
    teams.forEach((t) => {
      if (selected.includes(t.team_id)) {
        vrs[t.team_id] = (eventSeeds && eventSeeds[String(t.team_id)]) ?? t.vrs_rank ?? 999;
      }
    });
    const body = {
      team_ids: selected,
      vrs_ranks: vrs,
      bo3_mode: bo || "elim_qual",
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
              {t.name}
            </button>
          ))}
        </div>
        <div className="grid three">
          <div className="field">
            <span>Match Format</span>
            <div className="pill">
              {{ all: "All matches Bo3", none: "All matches Bo1", elim_qual: "Bo1, deciders Bo3" }[bo] ||
                "Bo1, deciders Bo3"}
              {" · "}
              {eventSeeds ? "event seeding" : "VRS-rank seeding"}
            </div>
          </div>
          <Input label="# Sims" value={sims} onChange={setSims} />
          <div className="field">
            <span>Run</span>
            <button className="primary" onClick={run} disabled={busy || selected.length < 2}>
              {busy ? "Running..." : "Run Swiss Group Stage"}
            </button>
          </div>
        </div>
        {eventSwissInfo && (
          <p className="muted">
            From event page: {eventSwissInfo.stage_name || "Swiss"} — {eventSwissInfo.team_count} teams
            {eventSwissInfo.advance_count ? `, top ${eventSwissInfo.advance_count} advance` : ""}
            {eventSwissInfo.unmatched?.length
              ? ` · not in our DB: ${eventSwissInfo.unmatched.join(", ")}`
              : ""}
          </p>
        )}
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
                  {t.name}
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

const parseMapStatsRows = (raw) => {
  if (!raw) return [];
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
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

const jobStatusLabel = (status) =>
  ({
    completed: "Completed",
    failed: "Failed",
    canceled: "Canceled",
    canceling: "Canceling",
    paused: "Paused",
    pausing: "Pausing",
    running: "Running",
    queued: "Queued",
  }[status] || "Queued");

// Shared map-stats import job state. Owned by App so both the Maps tab (bulk
// import) and the Database team modal (single-team import) drive the same job.
function useMapStatsJob({ refresh, notify, modalRefreshRef }) {
  const [status, setStatus] = useState("idle");
  const [jobId, setJobId] = useState("");
  const [processed, setProcessed] = useState(0);
  const [total, setTotal] = useState(0);
  const [ok, setOk] = useState(0);
  const [failed, setFailed] = useState(0);
  const [lastError, setLastError] = useState("");
  const [etaSeconds, setEtaSeconds] = useState(null);
  const pollingRef = useRef(false);

  const applyStatus = (statusPayload, jobIdOverride = "") => {
    const nextJobId = String(jobIdOverride || statusPayload?.job_id || "");
    const nextProcessed = Number(statusPayload?.processed_teams || 0);
    const nextTotal = Number(statusPayload?.total_teams || 0);
    const nextOk = Number(statusPayload?.ok || 0);
    const nextFailed = Number(statusPayload?.failed || 0);
    const nextStatus = String(statusPayload?.status || "queued");
    const nextLastError = String(statusPayload?.last_error || statusPayload?.error || "");

    setStatus(nextStatus);
    setJobId(nextJobId);
    setProcessed(nextProcessed);
    setTotal(nextTotal);
    setOk(nextOk);
    setFailed(nextFailed);
    setLastError(nextLastError);

    const startedAtMs = getBatchStartedAtMs(statusPayload);
    if (nextProcessed > 0 && nextTotal > nextProcessed && ["queued", "running", "pausing", "canceling"].includes(nextStatus)) {
      const elapsedSeconds = Math.max(1, (Date.now() - startedAtMs) / 1000);
      const rate = nextProcessed / elapsedSeconds;
      setEtaSeconds(rate > 0 ? (nextTotal - nextProcessed) / rate : null);
    } else if (nextTotal > 0 && nextProcessed >= nextTotal) {
      setEtaSeconds(0);
    } else {
      setEtaSeconds(null);
    }

    return { jobId: nextJobId, ok: nextOk, failed: nextFailed, nextStatus, lastError: nextLastError };
  };

  const poll = async (pollJobId) => {
    if (!pollJobId || pollingRef.current) return;
    pollingRef.current = true;
    try {
      let done = false;
      let pollFailures = 0;
      while (!done) {
        let statusPayload;
        try {
          statusPayload = await api.get(`/teams/map-stats-import/job/${pollJobId}`, 60000);
          pollFailures = 0;
        } catch (pollError) {
          // The job keeps running server-side; only give up after repeated failures.
          pollFailures += 1;
          if (pollFailures >= 5) throw pollError;
          await new Promise((resolve) => setTimeout(resolve, 3000));
          continue;
        }
        const applied = applyStatus(statusPayload, pollJobId);

        if (applied.nextStatus === "completed") {
          notify(`Map stats imported: ${applied.ok} ok, ${applied.failed} failed`);
          await refresh();
          await modalRefreshRef?.current?.();
          done = true;
          break;
        }
        if (applied.nextStatus === "failed") {
          notify(applied.lastError || "Map stats import failed.");
          done = true;
          break;
        }
        if (["paused", "canceled"].includes(applied.nextStatus)) {
          await refresh();
          await modalRefreshRef?.current?.();
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1200));
      }
    } catch (e) {
      setStatus("failed");
      setLastError(String(e?.message || "Failed to poll map stats job."));
      notify(`Map stats import failed: ${e?.message || "unknown error"}`);
    } finally {
      pollingRef.current = false;
    }
  };

  const start = async (missingOnly = false, teamIds = []) => {
    setStatus("queued");
    setProcessed(0);
    setTotal(0);
    setOk(0);
    setFailed(0);
    setLastError("");
    setEtaSeconds(null);
    try {
      const startPayload = await api.post("/teams/map-stats-import/start", {
        missing_only: missingOnly,
        team_ids: teamIds,
      });
      const startedJobId = String(startPayload?.job_id || "");
      if (!startedJobId) throw new Error("Failed to start map stats import job.");
      setJobId(startedJobId);
      await poll(startedJobId);
    } catch (e) {
      setStatus("failed");
      setLastError(String(e?.message || "Failed to start map stats import job."));
      notify(`Map stats import failed: ${e?.message || "unknown error"}`);
    }
  };

  const pause = async () => {
    if (!jobId) return;
    setStatus("pausing");
    try {
      const statusPayload = await api.post(`/teams/map-stats-import/job/${jobId}/pause`, {});
      const applied = applyStatus(statusPayload, jobId);
      if (["pausing", "running", "queued"].includes(applied.nextStatus)) {
        poll(jobId);
      }
    } catch (e) {
      setStatus("running");
      notify(`Failed to pause map stats import: ${e?.message || "unknown error"}`);
    }
  };

  const cancel = async () => {
    if (!jobId) return;
    setStatus("canceling");
    try {
      const statusPayload = await api.post(`/teams/map-stats-import/job/${jobId}/cancel`, {});
      const applied = applyStatus(statusPayload, jobId);
      if (["canceling", "running", "queued", "pausing"].includes(applied.nextStatus)) {
        poll(jobId);
      }
    } catch (e) {
      notify(`Failed to cancel map stats import: ${e?.message || "unknown error"}`);
    }
  };

  const resume = async () => {
    if (!jobId) return;
    try {
      const statusPayload = await api.post(`/teams/map-stats-import/job/${jobId}/resume`, {});
      const applied = applyStatus(statusPayload, jobId);
      if (["queued", "running", "pausing"].includes(applied.nextStatus)) {
        poll(jobId);
      }
    } catch (e) {
      notify(`Failed to resume map stats import: ${e?.message || "unknown error"}`);
    }
  };

  useEffect(() => {
    let cancelled = false;
    const hydrateLatest = async () => {
      try {
        const latest = await api.get("/teams/map-stats-import/latest");
        if (cancelled || !latest?.exists) return;
        if (latest?.status === "completed") return;
        const applied = applyStatus(latest);
        if (["queued", "running", "pausing", "canceling"].includes(applied.nextStatus)) {
          poll(applied.jobId);
        }
      } catch {
        // The map-stats progress panel is optional on startup.
      }
    };
    hydrateLatest();
    return () => {
      cancelled = true;
    };
  }, []);

  const active = ["queued", "running", "pausing", "canceling"].includes(status);
  return {
    status,
    jobId,
    processed,
    total,
    ok,
    failed,
    lastError,
    etaSeconds,
    start,
    pause,
    cancel,
    resume,
    active,
    resumable: ["paused", "failed"].includes(status),
    show: status !== "idle" && status !== "completed",
    progressPct: total > 0 ? Math.min(100, Math.max(0, (processed / total) * 100)) : 0,
    statusLabel: jobStatusLabel(status),
  };
}

const MapStatsJobControls = ({ job, teamsAvailable }) => (
  <div className="teams-toolbar">
    <button className="secondary" onClick={() => job.start(false)} disabled={job.active || !teamsAvailable}>
      {job.active ? `Importing Map Stats ${job.processed}/${job.total}` : "Import All Map Stats"}
    </button>
    {job.active && job.jobId && (
      <button className="secondary" onClick={job.pause} disabled={job.status === "pausing"}>
        {job.status === "pausing" ? "Pausing..." : "Pause"}
      </button>
    )}
    {job.active && job.jobId && (
      <button className="danger" onClick={job.cancel} disabled={job.status === "canceling"}>
        {job.status === "canceling" ? "Canceling..." : "Cancel"}
      </button>
    )}
    {job.resumable && job.jobId && (
      <button className="secondary" onClick={job.resume}>
        Resume
      </button>
    )}
  </div>
);

const MapStatsJobProgress = ({ job }) =>
  !job.show ? null : (
    <div className="card sub">
      <p className="muted">
        Map stats progress: {job.processed.toLocaleString()} / {job.total.toLocaleString()} | ok {job.ok} | failed {job.failed}
        {job.active && job.total > job.processed ? ` | ETA: ${formatBatchEta(job.etaSeconds)}` : ""}
      </p>
      <div className="progress">
        <div className="progress-bar determinate" style={{ width: `${job.progressPct}%` }} />
      </div>
      <p className="muted">Status: {job.statusLabel}</p>
      {job.lastError && <p className="muted">Last error: {job.lastError}</p>}
    </div>
  );

const MapsBarTooltip = ({ active, payload, label }) => {
  if (!active || !payload || !payload.length) return null;
  const row = payload[0]?.payload || {};
  return (
    <div className="card sub" style={{ padding: "8px 12px", border: "1px solid #232a34" }}>
      <p style={{ margin: 0, fontWeight: 600 }}>{label}</p>
      {Number(row.played || 0) === 0 ? (
        <p className="muted" style={{ margin: 0 }}>Not played in the last 3 months</p>
      ) : (
        <p className="muted" style={{ margin: 0 }}>Maps played: {Number(row.played || 0).toLocaleString()}</p>
      )}
      {row.teams !== undefined && Number(row.played || 0) > 0 && (
        <p className="muted" style={{ margin: 0 }}>Teams with data: {row.teams}</p>
      )}
      {row.win_rate !== undefined && row.win_rate !== null && (
        <p className="muted" style={{ margin: 0 }}>Win rate: {(Number(row.win_rate) * 100).toFixed(1)}%</p>
      )}
    </div>
  );
};

// Bar with a white tick marking the team's win rate on that map: the tick sits
// at win_rate percent of the bar's own height (top of bar = 100% win rate).
const MapBarWithWinRate = (props) => {
  const { x, y, width, height, fill, payload } = props;
  const winRate = payload?.win_rate;
  const showTick = winRate !== null && winRate !== undefined && Number.isFinite(winRate) && height > 4;
  const tickY = showTick ? y + height * (1 - Math.min(1, Math.max(0, winRate))) : 0;
  return (
    <g>
      <Rectangle x={x} y={y} width={width} height={height} fill={fill} radius={[4, 4, 0, 0]} />
      {showTick && (
        <line x1={x - 3} x2={x + width + 3} y1={tickY} y2={tickY} stroke="#f8fafc" strokeWidth={2.5} strokeLinecap="round" />
      )}
    </g>
  );
};

function PredictedVetoPanel({ teams }) {
  // Greedy veto prediction from each team's stored pick/ban tendencies
  // (per-team map-profile): bans follow historical ban rates, nudged toward
  // denying the opponent's best map; picks follow pick rates and map strength.
  const [teamAId, setTeamAId] = useState("");
  const [teamBId, setTeamBId] = useState("");
  const [months, setMonths] = useState("3");
  const [bo, setBo] = useState("bo3");
  const [profiles, setProfiles] = useState({});
  const [loading, setLoading] = useState(false);

  const teamOptions = useMemo(
    () => [
      { value: "", label: "Select team" },
      ...(teams || [])
        .slice()
        .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
        .map((t) => ({ value: String(t.team_id), label: t.name || `Team ${t.team_id}` })),
    ],
    [teams]
  );
  const teamById = useMemo(() => {
    const m = {};
    (teams || []).forEach((t) => (m[String(t.team_id)] = t));
    return m;
  }, [teams]);

  useEffect(() => {
    const ids = [teamAId, teamBId].filter((id) => Number(id) > 0);
    const missing = ids.filter((id) => !profiles[`${id}:${months}`]);
    if (missing.length === 0) return undefined;
    let cancelled = false;
    setLoading(true);
    Promise.all(
      missing.map((id) =>
        api
          .get(`/teams/${id}/map-profile?months=${months}`, 60000)
          .then((d) => ({ id, d }))
          .catch(() => ({ id, d: null }))
      )
    ).then((results) => {
      if (cancelled) return;
      setProfiles((prev) => {
        const next = { ...prev };
        results.forEach(({ id, d }) => {
          if (d?.status === "ok") next[`${id}:${months}`] = d;
        });
        return next;
      });
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [teamAId, teamBId, months, profiles]);

  const profA = profiles[`${teamAId}:${months}`];
  const profB = profiles[`${teamBId}:${months}`];
  const bothSelected = Number(teamAId) > 0 && Number(teamBId) > 0 && teamAId !== teamBId;

  const prediction = useMemo(() => {
    if (!bothSelected || !profA || !profB) return null;
    const index = (prof) => {
      const m = {};
      (prof.maps || []).forEach((r) => {
        m[r.map] = r;
      });
      return m;
    };
    const ra = index(profA);
    const rb = index(profB);
    const val = (row, key, dflt = 0) => {
      const v = row?.[key];
      return v === null || v === undefined ? dflt : Number(v);
    };
    const banScore = (own, opp, m) =>
      val(own[m], "ban_rate") * 3 + val(opp[m], "win_rate", 0.5) - val(own[m], "win_rate", 0.5);
    const pickScore = (own, opp, m) =>
      val(own[m], "pick_rate") * 3 + val(own[m], "win_rate", 0.5) - val(opp[m], "win_rate", 0.5);
    const sequence = {
      bo1: ["ban", "ban", "ban", "ban", "ban", "ban"],
      bo3: ["ban", "ban", "pick", "pick", "ban", "ban"],
      bo5: ["ban", "ban", "pick", "pick", "pick", "pick"],
    }[bo];
    let pool = [...ACTIVE_MAP_POOL];
    const steps = [];
    sequence.forEach((action, i) => {
      const aTurn = i % 2 === 0;
      const own = aTurn ? ra : rb;
      const opp = aTurn ? rb : ra;
      const best = pool
        .map((m) => ({ m, s: action === "ban" ? banScore(own, opp, m) : pickScore(own, opp, m) }))
        .sort((x, y) => y.s - x.s)[0];
      if (!best) return;
      pool = pool.filter((m) => m !== best.m);
      steps.push({ teamId: aTurn ? teamAId : teamBId, action, map: best.m });
    });
    return { steps, decider: pool[0] || null };
  }, [bothSelected, profA, profB, bo, teamAId, teamBId]);

  const renderTeam = (id) => {
    const t = teamById[String(id)];
    return (
      <span className="veto-step-team">
        <TeamLogo hltvTeamId={t?.hltv_team_id} name={t?.name} size={22} />
        <span>{t?.name || "-"}</span>
      </span>
    );
  };
  const lowData =
    bothSelected &&
    profA &&
    profB &&
    (Number(profA.veto_matches || 0) < 5 || Number(profB.veto_matches || 0) < 5);

  return (
    <div className="card sub">
      <h3>Predicted Veto</h3>
      <div className="veto-controls">
        <Select label="Team A" value={teamAId} onChange={setTeamAId} options={teamOptions} />
        <Select label="Team B" value={teamBId} onChange={setTeamBId} options={teamOptions} />
        <Select
          label="Timeframe"
          value={months}
          onChange={setMonths}
          options={[
            { value: "1", label: "Last month" },
            { value: "3", label: "Last 3 months" },
            { value: "6", label: "Last 6 months" },
            { value: "12", label: "Last 12 months" },
          ]}
        />
        <Select
          label="Format"
          value={bo}
          onChange={setBo}
          options={[
            { value: "bo1", label: "Best of 1" },
            { value: "bo3", label: "Best of 3" },
            { value: "bo5", label: "Best of 5" },
          ]}
        />
      </div>
      {!bothSelected ? (
        teamAId && teamAId === teamBId ? <p className="muted">Pick two different teams.</p> : null
      ) : loading && (!profA || !profB) ? (
        <p className="muted">Loading team veto data...</p>
      ) : !prediction ? (
        <p className="muted">No stored veto data for these teams in this window.</p>
      ) : (
        <>
          <p className="muted">
            Based on {Number(profA?.veto_matches || 0)} stored vetoes for {teamById[String(teamAId)]?.name} and{" "}
            {Number(profB?.veto_matches || 0)} for {teamById[String(teamBId)]?.name}. Team A starts the veto.
            {lowData ? " Limited data — treat with caution." : ""}
          </p>
          <div className="veto-steps">
            {prediction.steps.map((s, i) => (
              <div
                className="veto-step"
                key={`veto-${i}`}
                style={{ "--map-color": MAP_BAR_COLORS[s.map] || MAP_BAR_FALLBACK_COLOR }}
              >
                <span className="veto-step-idx">{i + 1}</span>
                {renderTeam(s.teamId)}
                <span className={`veto-action ${s.action}`}>{s.action === "ban" ? "BAN" : "PICK"}</span>
                <span className="veto-step-map">{s.map}</span>
              </div>
            ))}
            {prediction.decider && (
              <div
                className="veto-step decider"
                style={{ "--map-color": MAP_BAR_COLORS[prediction.decider] || MAP_BAR_FALLBACK_COLOR }}
              >
                <span className="veto-step-idx">{prediction.steps.length + 1}</span>
                <span className="veto-step-team muted">Leftover</span>
                <span className="veto-action decider-label">DECIDER</span>
                <span className="veto-step-map">{prediction.decider}</span>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function MapsTab({ teams }) {
  const [mapsSubTab, setMapsSubTab] = useState("popularity");
  const [selectedTeamId, setSelectedTeamId] = useState("");
  // The hover tooltip can float over the donut hole; hide the center totals
  // while a slice is hovered so the two never overlap.
  const [pieHovered, setPieHovered] = useState(false);
  // Pie filters: computed live from stored match results, so the window and
  // the VRS-rank restriction are adjustable (unlike the fixed team-stats
  // scrape). Same filter semantics as the Matches browser.
  const [pieMonths, setPieMonths] = useState("3");
  const [pieRank, setPieRank] = useState("");
  const [pieDir, setPieDir] = useState("within");
  const [pieScope, setPieScope] = useState("both");
  const [pieData, setPieData] = useState(null);
  const [pieLoading, setPieLoading] = useState(false);
  useEffect(() => {
    let cancelled = false;
    // Debounce so typing a rank doesn't fire a request per keystroke.
    const timer = setTimeout(() => {
      setPieLoading(true);
      const rankParams =
        Number(pieRank) > 0
          ? `&vrs_rank=${Number(pieRank)}&vrs_scope=${encodeURIComponent(pieScope)}&vrs_dir=${encodeURIComponent(pieDir)}`
          : "";
      api
        .get(`/events/map-play-distribution?months=${pieMonths}${rankParams}`, 60000)
        .then((d) => {
          if (!cancelled && d?.status === "ok") setPieData(d);
        })
        .catch(() => {})
        .finally(() => {
          if (!cancelled) setPieLoading(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [pieMonths, pieRank, pieDir, pieScope]);

  // Team map profile: computed live from stored matches (map scores + vetoes)
  // so the window is adjustable, unlike the fixed HLTV team-page scrape.
  const [teamMonths, setTeamMonths] = useState("3");
  const [teamProfile, setTeamProfile] = useState(null);
  const [teamProfileLoading, setTeamProfileLoading] = useState(false);
  useEffect(() => {
    if (!selectedTeamId) {
      setTeamProfile(null);
      return undefined;
    }
    let cancelled = false;
    setTeamProfileLoading(true);
    api
      .get(`/teams/${selectedTeamId}/map-profile?months=${teamMonths}`, 60000)
      .then((d) => {
        if (!cancelled && d?.status === "ok") setTeamProfile(d);
      })
      .catch(() => {})
      .finally(() => {
        if (!cancelled) setTeamProfileLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedTeamId, teamMonths]);

  const teamRows = useMemo(() => {
    const rate = (value) => (value === null || value === undefined ? null : Number(value));
    const rows = (teamProfile?.maps || []).map((row) => {
      const name = String(row.map || "").trim();
      return {
        map: name,
        played: Number(row.played || 0),
        wins: Number(row.wins || 0),
        losses: Number(row.losses || 0),
        win_rate: rate(row.win_rate),
        total_rounds: Number(row.total_rounds || 0),
        pick_rate: rate(row.pick_rate),
        ban_rate: rate(row.ban_rate),
        inPool: ACTIVE_MAP_POOL.includes(name),
      };
    });
    const present = new Set(rows.map((row) => row.map));
    ACTIVE_MAP_POOL.forEach((name) => {
      if (!present.has(name)) {
        rows.push({
          map: name,
          played: 0,
          wins: 0,
          losses: 0,
          win_rate: null,
          total_rounds: 0,
          pick_rate: null,
          ban_rate: null,
          inPool: true,
        });
      }
    });
    return rows.sort((a, b) => b.played - a.played);
  }, [teamProfile]);

  const teamOptions = useMemo(
    () => [
      { value: "", label: "Select team" },
      ...(teams || [])
        .slice()
        .sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")))
        .map((t) => ({ value: String(t.team_id), label: t.name || `Team ${t.team_id}` })),
    ],
    [teams]
  );

  const formatRate = (value, digits = 1) =>
    value === null || !Number.isFinite(value) ? "-" : `${(value * 100).toFixed(digits)}%`;

  const renderMapBars = (rows) => (
    <ResponsiveContainer width="100%" height={320}>
      <BarChart
        data={rows.map((r) => ({ ...r, label: r.inPool ? r.map : `${r.map} (out)` }))}
        margin={{ top: 12, right: 18, left: 6, bottom: 12 }}
      >
        <CartesianGrid stroke="#232a34" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="label"
          interval={0}
          tick={{ fill: "#9fb2c9", fontSize: 12 }}
          axisLine={{ stroke: "#3a4452" }}
          tickLine={{ stroke: "#3a4452" }}
        />
        <YAxis
          allowDecimals={false}
          tick={{ fill: "#9fb2c9", fontSize: 12 }}
          axisLine={{ stroke: "#3a4452" }}
          tickLine={{ stroke: "#3a4452" }}
        />
        <Tooltip cursor={{ fill: "rgba(59, 130, 246, 0.08)" }} content={<MapsBarTooltip />} />
        <Bar
          dataKey="played"
          isAnimationActive={false}
          radius={[4, 4, 0, 0]}
          maxBarSize={48}
          shape={<MapBarWithWinRate />}
        >
          {rows.map((r) => (
            <Cell key={r.map} fill={r.inPool ? MAP_BAR_COLORS[r.map] || MAP_BAR_FALLBACK_COLOR : MAP_BAR_FALLBACK_COLOR} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );

  return (
    <Section title="Maps">
      <div className="tab-bar small">
        <button className={mapsSubTab === "popularity" ? "tab active" : "tab"} onClick={() => setMapsSubTab("popularity")}>
          Most Played
        </button>
        <button className={mapsSubTab === "teams" ? "tab active" : "tab"} onClick={() => setMapsSubTab("teams")}>
          Team Map Stats
        </button>
        <button className={mapsSubTab === "veto" ? "tab active" : "tab"} onClick={() => setMapsSubTab("veto")}>
          Predicted Veto
        </button>
      </div>
      <div className="stack">
        {mapsSubTab === "popularity" && (
        <div className="card sub">
          <h3>Most Played Maps</h3>
          {(() => {
            const totalPlayed = Number(pieData?.total_maps || 0);
            // Fold slivers (single show-match maps etc.) into "Other", and
            // only draw outside labels for slices wide enough to label
            // cleanly — thinner ones stay hoverable without colliding text.
            const MIN_SLICE_PCT = 0.012;
            const MIN_LABEL_PCT = 0.022;
            const major = [];
            let otherPlayed = 0;
            (pieData?.maps || [])
              .filter((r) => Number(r.played || 0) > 0)
              .forEach((r) => {
                if (totalPlayed > 0 && Number(r.played) / totalPlayed < MIN_SLICE_PCT) {
                  otherPlayed += Number(r.played);
                } else {
                  major.push(r);
                }
              });
            const pieRows = otherPlayed > 0 ? [...major, { map: "Other", played: otherPlayed }] : major;
            const chart =
              pieRows.length === 0 ? (
                <p className="muted">{pieLoading ? "Loading map data..." : "No stored matches in this window."}</p>
              ) : (
                <div className="pie-wrap" style={pieLoading ? { opacity: 0.6 } : undefined}>
                  <ResponsiveContainer width="100%" height={460}>
                    <PieChart accessibilityLayer={false}>
                      <Pie
                        data={pieRows}
                        dataKey="played"
                        nameKey="map"
                        cx="50%"
                        cy="50%"
                        outerRadius={168}
                        innerRadius={92}
                        stroke="#0a0c10"
                        strokeWidth={2}
                        isAnimationActive={false}
                        onMouseEnter={() => setPieHovered(true)}
                        onMouseLeave={() => setPieHovered(false)}
                        label={({ cx, cy, midAngle, outerRadius: or2, map, played }) => {
                          if (totalPlayed > 0 && Number(played) / totalPlayed < MIN_LABEL_PCT) return null;
                          const rad = (-midAngle * Math.PI) / 180;
                          const cos = Math.cos(rad);
                          const sin = Math.sin(rad);
                          const x = cx + (or2 + 28) * cos;
                          const y = cy + (or2 + 28) * sin;
                          // Near-vertical labels center on the line end (offset
                          // above/below it) so the line points at the text, not
                          // at one end of it.
                          const vertical = Math.abs(cos) < 0.35;
                          return (
                            <text
                              x={x}
                              y={vertical ? y + (sin > 0 ? 11 : -11) : y}
                              textAnchor={vertical ? "middle" : cos > 0 ? "start" : "end"}
                              dominantBaseline="central"
                              fill="#f2f5f9"
                              fontSize={15}
                              fontWeight={600}
                            >
                              {totalPlayed > 0 ? `${map} ${((played / totalPlayed) * 100).toFixed(1)}%` : map}
                            </text>
                          );
                        }}
                        labelLine={(props) => {
                          const { cx, cy, midAngle, outerRadius: or2 } = props;
                          const linePlayed = Number(props?.played ?? props?.payload?.played ?? 0);
                          if (totalPlayed > 0 && linePlayed / totalPlayed < MIN_LABEL_PCT) return null;
                          const rad = (-midAngle * Math.PI) / 180;
                          const sx = cx + (or2 + 3) * Math.cos(rad);
                          const sy = cy + (or2 + 3) * Math.sin(rad);
                          const ex = cx + (or2 + 20) * Math.cos(rad);
                          const ey = cy + (or2 + 20) * Math.sin(rad);
                          return <path d={`M${sx},${sy}L${ex},${ey}`} stroke="#3a4452" fill="none" />;
                        }}
                      >
                        {pieRows.map((r) => (
                          <Cell
                            key={`pie-${r.map}`}
                            fill={r.map === "Other" ? "#64748b" : MAP_BAR_COLORS[r.map] || MAP_BAR_FALLBACK_COLOR}
                          />
                        ))}
                      </Pie>
                      <Tooltip
                        contentStyle={{ background: "#14181f", border: "1px solid #3a4452", borderRadius: 10 }}
                        itemStyle={{ color: "#e9edf3" }}
                        formatter={(value, name) => [
                          `${Number(value).toLocaleString()} maps (${totalPlayed > 0 ? ((Number(value) / totalPlayed) * 100).toFixed(1) : 0}%)`,
                          name,
                        ]}
                      />
                    </PieChart>
                  </ResponsiveContainer>
                  <div className="pie-center-label" style={{ opacity: pieHovered ? 0 : 1 }}>
                    <div className="pie-center-number">{totalPlayed.toLocaleString()}</div>
                    <div className="pie-center-sub">maps played</div>
                    <div className="pie-center-sub">
                      last {Number(pieMonths)} month{Number(pieMonths) > 1 ? "s" : ""}
                    </div>
                    {Number(pieRank) > 0 && (
                      <div className="pie-center-sub">
                        {pieDir === "within" ? "inside" : "outside"} top {Number(pieRank)}
                      </div>
                    )}
                  </div>
                </div>
              );
            return (
              <div className="pie-layout">
                <div className="pie-filter-col">
                  <Select
                    label="Timeframe"
                    value={pieMonths}
                    onChange={setPieMonths}
                    options={[
                      { value: "1", label: "Last month" },
                      { value: "3", label: "Last 3 months" },
                      { value: "6", label: "Last 6 months" },
                      { value: "12", label: "Last 12 months" },
                    ]}
                  />
                  <Input label="VRS Rank" value={pieRank} onChange={setPieRank} placeholder="e.g. 50" />
                  <Select
                    label="Direction"
                    value={pieDir}
                    onChange={setPieDir}
                    options={[
                      { value: "within", label: "Inside top N" },
                      { value: "outside", label: "Outside top N" },
                    ]}
                  />
                  <Select
                    label="Teams"
                    value={pieScope}
                    onChange={setPieScope}
                    options={[
                      { value: "both", label: "Both teams" },
                      { value: "either", label: "At least one team" },
                    ]}
                  />
                </div>
                {chart}
              </div>
            );
          })()}
        </div>
        )}
        {mapsSubTab === "teams" && (
        <div className="card sub">
          <h3>Team Map Stats</h3>
          <div className="grid two">
            <Select label="Team" value={selectedTeamId} onChange={setSelectedTeamId} options={teamOptions} />
            <Select
              label="Timeframe"
              value={teamMonths}
              onChange={setTeamMonths}
              options={[
                { value: "1", label: "Last month" },
                { value: "3", label: "Last 3 months" },
                { value: "6", label: "Last 6 months" },
                { value: "12", label: "Last 12 months" },
              ]}
            />
          </div>
          {!selectedTeamId ? (
            <p className="muted">Select a team to see its per-map record.</p>
          ) : teamProfileLoading && !teamProfile ? (
            <p className="muted">Loading map data...</p>
          ) : Number(teamProfile?.matches || 0) === 0 && Number(teamProfile?.veto_matches || 0) === 0 ? (
            <p className="muted">No stored matches for this team in this window.</p>
          ) : (
            <>
              {renderMapBars(teamRows)}
              <table>
                <thead>
                  <tr>
                    <th>Map</th>
                    <th>Played</th>
                    <th>W - L</th>
                    <th>Win Rate</th>
                    <th>Rounds</th>
                    <th>Pick Rate</th>
                    <th>Ban Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {teamRows.map((row) => (
                    <tr key={row.map}>
                      <td>{row.map}</td>
                      <td>{row.played}</td>
                      <td>{`${row.wins} - ${row.losses}`}</td>
                      <td>{formatRate(row.win_rate)}</td>
                      <td>{row.total_rounds || "-"}</td>
                      <td>{formatRate(row.pick_rate)}</td>
                      <td>{formatRate(row.ban_rate)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
        )}
        {mapsSubTab === "veto" && <PredictedVetoPanel teams={teams} />}
      </div>
    </Section>
  );
}

function PlayoffTab({ teams, teamLookup, players, sortTeams, applyFilters, onOpenPlayer, variant = "main", detectedBracketSize = null }) {
  const isBounty = variant === "bounty";
  const [playoffTab, setPlayoffTab] = useState("stage");
  const [events, setEvents] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [eventTeamNames, setEventTeamNames] = useState(new Set());
  const [latestPayload, setLatestPayload] = useState(null);
  const [updatedAt, setUpdatedAt] = useState("");
  const [slots, setSlots] = useState(Array(8).fill(""));
  const [bracketSize, setBracketSize] = useState(8); // 8 = exact, 16 = Monte-Carlo
  // null = stored-sim hydration pending, 0 = no stored sim, else its size.
  const [storedSize, setStoredSize] = useState(null);
  const [mcSims, setMcSims] = useState(5000); // Monte-Carlo samples for large (16-team) fields
  // Self-loading bracket: once the stored-sim hydration has resolved, seed
  // the page from the event automatically whenever the stored sim doesn't
  // match the detected format (or there is none).
  const autoSeededRef = useRef(false);
  useEffect(() => {
    if (isBounty || autoSeededRef.current) return;
    if (storedSize === null) return; // hydration still pending
    const size = Number(detectedBracketSize);
    if (![2, 4, 6, 8, 16].includes(size)) return;
    if (storedSize === size) return; // stored sim already fits this format
    autoSeededRef.current = true;
    autofillPlayoffFromEvent();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [storedSize, detectedBracketSize]);

  // Fill the seed slots + bracket size straight from the linked HLTV event's
  // single-elimination playoff bracket (16/8/4), in bracket order.
  const autofillPlayoffFromEvent = async () => {
    setPlayoffAutofillBusy(true);
    setPlayoffAutofillMessage("");
    try {
      const data = await api.post(
        "/playoff/autofill-from-hltv-event",
        { fantasy_event_id: selectedEventId ? Number(selectedEventId) : undefined },
        90000
      );
      if (data?.detail || data?.error) {
        setPlayoffAutofillMessage(String(data.detail || data.error));
        return;
      }
      const ids = (data.team_ids || []).map((x) => String(x || ""));
      const names = data.team_names || [];
      const size = Number(data.bracket_size || ids.length);
      if (![2, 4, 6, 8, 16].includes(size)) {
        setPlayoffAutofillMessage(`Unsupported bracket size (${size}).`);
        return;
      }
      setBracketSize(size);
      setSlots([...ids, ...Array(size).fill("")].slice(0, size));
      setResults(null);
      setTopTeams(null);
      setAllTeams(null);
      setBaseTeams(null);
      setCompletedBracket({ rounds: emptyCompletedRounds(size), third: "" });
      setCompletedBracketResult(null);
      setCompletedBracketMessage("");
      const tbd = names.filter((_, i) => !Number(ids[i]));
      setPlayoffAutofillMessage(
        tbd.length
          ? `Filled ${size}-team bracket; ${tbd.length} seed(s) not matched to a team yet (${tbd.join(", ")}).`
          : `Filled ${size}-team bracket from the event.`
      );
    } catch (e) {
      setPlayoffAutofillMessage(e?.message || "Autofill from HLTV event failed.");
    } finally {
      setPlayoffAutofillBusy(false);
    }
  };
  const [hasThirdPlaceDecider, setHasThirdPlaceDecider] = useState(false);
  // Bounty draft state: seeds 5-7 pick their QF opponent (seed 8 gets the
  // leftover), and per QF-winner scenario the first SF drafter's pick.
  const [draftPicks, setDraftPicks] = useState(["", "", ""]);
  const [sfPicks, setSfPicks] = useState({});
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [topTeams, setTopTeams] = useState(null);
  const [allTeams, setAllTeams] = useState(null);
  const [baseTeams, setBaseTeams] = useState(null);
  const [sharedComboCount, setSharedComboCount] = useState(0);
  const [combosApproximate, setCombosApproximate] = useState(false);
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
  // `rounds` holds one winner-array per round in bracket order (round 0 first,
  // final last); `third` is the third-place decider pick. Generalised from the
  // old {qf,sf,final,third} shape so the picker supports 8- and 16-team fields.
  const [completedBracket, setCompletedBracket] = useState({
    rounds: [["", "", "", ""], ["", ""], [""]],
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
  const [playoffAutofillBusy, setPlayoffAutofillBusy] = useState(false);
  const [playoffAutofillMessage, setPlayoffAutofillMessage] = useState("");
  const playoffPollingRef = useRef(false);
  const comboQuerySeqRef = useRef(0);
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
      ...filteredTeams.map((t) => ({ value: String(t.team_id), label: t.name || `Team ${t.team_id}` })),
    ],
    [filteredTeams]
  );
  const teamInitials = (teamId) => {
    const name = teamLookup[Number(teamId)] || "";
    const parts = String(name).trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
    return String(name || "?").slice(0, 2).toUpperCase();
  };
  const hltvIdByTeamId = useMemo(() => {
    const m = {};
    teams.forEach((t) => {
      if (t.hltv_team_id) m[Number(t.team_id)] = Number(t.hltv_team_id);
    });
    return m;
  }, [teams]);
  const TeamBadge = ({ teamId }) => {
    const id = Number(teamId);
    if (!Number.isFinite(id) || id <= 0) return <span className="playoff-team-badge empty">?</span>;
    return <TeamLogo hltvTeamId={hltvIdByTeamId[id]} name={teamLookup[id]} size={24} />;
  };
  // Bracket geometry helpers shared by the seeding view and the completed-bracket
  // picker so both work for any power-of-two field (8 exact, 16 Monte-Carlo).
  const bracketTotalRounds = (n) => (n >= 2 ? Math.round(Math.log2(n)) : 0);
  const roundTitleForTeams = (teamsInRound) =>
    ({ 2: "Grand final", 4: "Semi-finals", 8: "Quarter-finals", 16: "Round of 16", 32: "Round of 32" }[teamsInRound] ||
      `Round of ${teamsInRound}`);
  const roundShortForTeams = (teamsInRound) =>
    ({ 2: "F", 4: "SF", 8: "QF", 16: "R16", 32: "R32" }[teamsInRound] || `R${teamsInRound}`);
  const roundNameForTeams = (teamsInRound) =>
    ({ 2: "final", 4: "semis", 8: "quarters", 16: "round_of_16", 32: "round_of_32" }[teamsInRound] ||
      `round_of_${teamsInRound}`);
  const emptyCompletedRounds = (n) => {
    const rounds = [];
    const total = bracketTotalRounds(n);
    for (let r = 0; r < total; r++) rounds.push(Array(n >> (r + 1)).fill(""));
    return rounds;
  };
  // Display-only: seeds load from the event automatically, no manual picking.
  const BracketTeamRow = ({ slotIndex, placeholder, muted = false }) => {
    const selectedTeamId = slotIndex !== null && slotIndex !== undefined ? slots[slotIndex] : "";
    const hasTeam = Boolean(selectedTeamId);
    return (
      <div className={`playoff-team-row ${muted ? "muted" : ""}`}>
        <TeamBadge teamId={hasTeam ? selectedTeamId : 0} />
        {hasTeam ? (
          <span>{teamLookup[Number(selectedTeamId)] || `Team ${selectedTeamId}`}</span>
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
        <TeamBadge teamId={hasTeam ? teamId : 0} />
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
  const matchCardTitle = (teamsInRound, matchIdx) =>
    teamsInRound === 2 ? "Final" : `${roundShortForTeams(teamsInRound)} ${matchIdx + 1}`;
  const matchCardMeta = (teamsInRound) => (teamsInRound === 2 ? "BO5" : "BO3");
  // Seeding view (Run tab): round 0 has the seed selects; later rounds show the
  // muted winner placeholders. Auto-distributed via .pb-bracket for any size.
  const renderSeedingBracket = () => {
    const n = bracketSize;
    if (n === 6) {
      // Byes bracket: slots [SF bye 1, QF1a, QF1b, QF2a, QF2b, SF bye 2].
      return (
        <div className="pb-bracket">
          <div className="pb-round">
            <div className="pb-round-title">Quarter-finals</div>
            <div className="pb-matches">
              <div className="pb-match-wrap">
                <BracketMatchCard
                  title="QF 1"
                  meta="BO3"
                  rows={
                    <>
                      <BracketTeamRow slotIndex={1} />
                      <BracketTeamRow slotIndex={2} />
                    </>
                  }
                />
              </div>
              <div className="pb-match-wrap">
                <BracketMatchCard
                  title="QF 2"
                  meta="BO3"
                  rows={
                    <>
                      <BracketTeamRow slotIndex={3} />
                      <BracketTeamRow slotIndex={4} />
                    </>
                  }
                />
              </div>
            </div>
          </div>
          <div className="pb-round">
            <div className="pb-round-title">Semi-finals</div>
            <div className="pb-matches">
              <div className="pb-match-wrap">
                <BracketMatchCard
                  title="SF 1"
                  meta="BO3"
                  rows={
                    <>
                      <BracketTeamRow slotIndex={0} />
                      <BracketTeamRow placeholder="Winner QF 1" muted />
                    </>
                  }
                />
              </div>
              <div className="pb-match-wrap">
                <BracketMatchCard
                  title="SF 2"
                  meta="BO3"
                  rows={
                    <>
                      <BracketTeamRow slotIndex={5} />
                      <BracketTeamRow placeholder="Winner QF 2" muted />
                    </>
                  }
                />
              </div>
            </div>
          </div>
          <div className="pb-round">
            <div className="pb-round-title">Grand final</div>
            <div className="pb-matches">
              <div className="pb-match-wrap">
                <BracketMatchCard
                  title="FINAL"
                  meta="BO5"
                  rows={
                    <>
                      <BracketTeamRow placeholder="Winner SF 1" muted />
                      <BracketTeamRow placeholder="Winner SF 2" muted />
                    </>
                  }
                />
              </div>
            </div>
          </div>
        </div>
      );
    }
    const totalRounds = bracketTotalRounds(n);
    return (
      <div className="pb-bracket">
        {Array.from({ length: totalRounds }, (_, r) => {
          const teamsInRound = n >> r;
          const matches = n >> (r + 1);
          const feederShort = r > 0 ? roundShortForTeams(n >> (r - 1)) : "";
          return (
            <div className="pb-round" key={`seed-round-${r}`}>
              <div className="pb-round-title">{roundTitleForTeams(teamsInRound)}</div>
              <div className="pb-matches">
                {Array.from({ length: matches }, (_, m) => (
                  <div className="pb-match-wrap" key={`seed-${r}-${m}`}>
                    <BracketMatchCard
                      title={matchCardTitle(teamsInRound, m)}
                      meta={matchCardMeta(teamsInRound)}
                      rows={
                        r === 0 ? (
                          <>
                            <BracketTeamRow slotIndex={2 * m} />
                            <BracketTeamRow slotIndex={2 * m + 1} />
                          </>
                        ) : (
                          <>
                            <BracketTeamRow placeholder={`Winner ${feederShort} ${2 * m + 1}`} muted />
                            <BracketTeamRow placeholder={`Winner ${feederShort} ${2 * m + 2}`} muted />
                          </>
                        )
                      }
                    />
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
    );
  };
  // Completed-bracket picker: the same auto-distributed bracket, but each row is
  // a clickable winner pick sourced from completedBracketDerived.roundsPairs.
  const renderCompletedPicker = () => {
    const n = slots.length;
    const totalRounds = bracketTotalRounds(n);
    const { roundsPairs, thirdPair } = completedBracketDerived;
    return (
      <>
        <div className="pb-bracket">
          {Array.from({ length: totalRounds }, (_, r) => {
            const teamsInRound = n >> r;
            const matches = n >> (r + 1);
            const pairs = roundsPairs[r] || [];
            const picks = (completedBracket.rounds || [])[r] || [];
            return (
              <div className="pb-round" key={`cmp-round-${r}`}>
                <div className="pb-round-title">{roundTitleForTeams(teamsInRound)}</div>
                <div className="pb-matches">
                  {Array.from({ length: matches }, (_, m) => {
                    const pair = pairs[m] || ["", ""];
                    return (
                      <div className="pb-match-wrap" key={`cmp-${r}-${m}`}>
                        <BracketMatchCard
                          title={matchCardTitle(teamsInRound, m)}
                          meta={matchCardMeta(teamsInRound)}
                          rows={pair.map((teamId, rowIdx) => (
                            <CompletedBracketTeamRow
                              key={`cmp-${r}-${m}-${rowIdx}`}
                              teamId={teamId}
                              selected={Boolean(teamId) && String(picks[m]) === String(teamId)}
                              onSelect={(val) => setCompletedPick(r, m, val)}
                              placeholder="TBD"
                            />
                          ))}
                        />
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
        {hasThirdPlaceDecider && !isBounty && (
          <div className="pb-thirdplace">
            <BracketMatchCard
              title="Third-place decider"
              rows={thirdPair.map((teamId, idx) => (
                <CompletedBracketTeamRow
                  key={`cmp-third-${idx}`}
                  teamId={teamId}
                  selected={Boolean(teamId) && String(completedBracket.third) === String(teamId)}
                  onSelect={(val) => setCompletedThird(val)}
                  placeholder={idx === 0 ? "Semi-final 1 loser" : "Semi-final 2 loser"}
                />
              ))}
            />
          </div>
        )}
      </>
    );
  };
  const setCompletedPick = (roundIdx, matchIdx, value) => {
    setCompletedBracket((prev) => {
      const rounds = (prev.rounds || []).map((r) => [...r]);
      if (!rounds[roundIdx]) return prev;
      rounds[roundIdx][matchIdx] = value;
      // Invalidate downstream picks that depended on this result.
      if (isBounty && roundIdx === 0) {
        // Bounty semis are re-drafted from the surviving four, so any QF change
        // invalidates the entire SF round (and everything after it).
        for (let r = 1; r < rounds.length; r++) rounds[r] = rounds[r].map(() => "");
      } else {
        // Standard single-elim: only the descendant match in each later round
        // depended on this pick.
        let descendant = matchIdx;
        for (let r = roundIdx + 1; r < rounds.length; r++) {
          descendant = Math.floor(descendant / 2);
          if (rounds[r] && rounds[r][descendant] !== undefined) rounds[r][descendant] = "";
        }
      }
      return { ...prev, rounds, third: "" };
    });
    setCompletedBracketResult(null);
    setCompletedBracketMessage("");
  };
  const setCompletedThird = (value) => {
    setCompletedBracket((prev) => ({ ...prev, third: value }));
    setCompletedBracketResult(null);
    setCompletedBracketMessage("");
  };
  const hydrateCompletedBracketFromBracket = (bracket, n = bracketSize) => {
    if (!bracket) return;
    const totalRounds = bracketTotalRounds(n);
    const rounds = [];
    let anyPicked = false;
    for (let r = 0; r < totalRounds; r++) {
      const teamsInRound = n >> r;
      const matches = bracket[roundNameForTeams(teamsInRound)] || [];
      const winners = [];
      for (let i = 0; i < n >> (r + 1); i++) {
        const w = matches[i]?.winner ? String(matches[i].winner) : "";
        if (w) anyPicked = true;
        winners.push(w);
      }
      rounds.push(winners);
    }
    const thirdWinner = (bracket.third_place || [])[0]?.winner ? String(bracket.third_place[0].winner) : "";
    if (anyPicked || thirdWinner) setCompletedBracket({ rounds, third: thirdWinner });
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
  const seedIndexById = useMemo(() => {
    const m = {};
    slots.forEach((s, idx) => {
      const id = Number(s);
      if (Number.isFinite(id) && id > 0) m[id] = idx;
    });
    return m;
  }, [slots]);
  const draftedQfPairs = useMemo(() => {
    if (!isBounty) return null;
    const top = slots.slice(0, 4).map((s) => Number(s));
    const bottom = slots.slice(4, 8).map((s) => Number(s));
    if (top.some((id) => !id) || bottom.some((id) => !id)) return null;
    const picks = draftPicks.map((p) => Number(p));
    if (picks.some((p) => !p)) return null;
    if (new Set(picks).size !== 3 || picks.some((p) => !top.includes(p))) return null;
    const leftover = top.find((id) => !picks.includes(id));
    return bottom.map((drafter, idx) => [drafter, idx < 3 ? picks[idx] : leftover]);
  }, [isBounty, slots, draftPicks]);
  // The 16 possible QF-winner scenarios; in each, the two lowest-seeded
  // survivors re-draft: the higher of them picks first from the top two.
  const bountySfScenarios = useMemo(() => {
    if (!isBounty || !draftedQfPairs) return [];
    const scenarios = [];
    for (let mask = 0; mask < 16; mask++) {
      const winners = draftedQfPairs.map((pair, i) => pair[(mask >> i) & 1]);
      const ordered = [...winners].sort((a, b) => (seedIndexById[a] ?? 99) - (seedIndexById[b] ?? 99));
      const key = [...winners].sort((a, b) => a - b).join("-");
      scenarios.push({ key, winners, ordered, drafter: ordered[2], options: [ordered[0], ordered[1]] });
    }
    return scenarios;
  }, [isBounty, draftedQfPairs, seedIndexById]);
  const bountySfPairsFor = (winners) => {
    const ordered = [...winners.map(Number)].sort((a, b) => (seedIndexById[a] ?? 99) - (seedIndexById[b] ?? 99));
    const key = [...winners.map(Number)].sort((a, b) => a - b).join("-");
    const options = [ordered[0], ordered[1]];
    let pick = Number(sfPicks[key]);
    if (!options.includes(pick)) pick = ordered[1];
    const other = options.find((id) => id !== pick);
    return [
      [ordered[2], pick],
      [ordered[3], other],
    ];
  };
  const bountySfPicksPayload = () => {
    const out = {};
    bountySfScenarios.forEach((sc) => {
      out[sc.key] = bountySfPairsFor(sc.winners);
    });
    return out;
  };
  const completedBracketDerived = useMemo(() => {
    const isPickedFrom = (value, ids) => Boolean(value) && ids.some((id) => String(id) === String(value));
    const n = slots.length;
    const totalRounds = bracketTotalRounds(n);
    const picks = completedBracket.rounds || [];
    // The match-ups a user picks from, per round. Round 0 is the seeded field
    // (or the bounty draft); later rounds are formed from the previous round's
    // picked winners (bounty semis are re-drafted from the surviving four).
    const roundsPairs = [];
    for (let r = 0; r < totalRounds; r++) {
      const matches = n >> (r + 1);
      let pairs;
      if (r === 0) {
        pairs = isBounty
          ? (draftedQfPairs || Array.from({ length: matches }, () => ["", ""])).map((pair) =>
              pair.map((id) => (id ? String(id) : ""))
            )
          : Array.from({ length: matches }, (_, i) => [slots[2 * i] || "", slots[2 * i + 1] || ""]);
      } else if (isBounty && r === 1) {
        const qfPicks = picks[0] || [];
        pairs =
          qfPicks.length === n / 2 && qfPicks.every(Boolean)
            ? bountySfPairsFor(qfPicks).map((pair) => pair.map((id) => (id ? String(id) : "")))
            : Array.from({ length: matches }, () => ["", ""]);
      } else {
        const prev = picks[r - 1] || [];
        pairs = Array.from({ length: matches }, (_, i) => [prev[2 * i] || "", prev[2 * i + 1] || ""]);
      }
      roundsPairs.push(pairs);
    }
    // Third-place decider is between the two semi-final losers (the 4-team round).
    const semiRoundIdx = totalRounds - 2;
    let thirdPair = ["", ""];
    if (semiRoundIdx >= 0 && !isBounty) {
      const semiPairs = roundsPairs[semiRoundIdx] || [];
      const semiPicks = picks[semiRoundIdx] || [];
      thirdPair = semiPairs.map((pair, idx) => pair.find((id) => id && String(id) !== String(semiPicks[idx])) || "");
    }
    let complete = totalRounds > 0;
    for (let r = 0; r < totalRounds && complete; r++) {
      const roundPicks = picks[r] || [];
      const pairs = roundsPairs[r] || [];
      if (roundPicks.length !== pairs.length) complete = false;
      else complete = pairs.every((pair, i) => isPickedFrom(roundPicks[i], pair));
    }
    if (complete && hasThirdPlaceDecider && !isBounty) {
      complete = isPickedFrom(completedBracket.third, thirdPair);
    }
    return { roundsPairs, thirdPair, semiRoundIdx, complete };
  }, [slots, completedBracket, hasThirdPlaceDecider, isBounty, draftedQfPairs, sfPicks, seedIndexById]);
  // Request fields describing the picked bracket. The backend computes the
  // bracket deterministically from `round_winners` (any field size); the bounty
  // variant is still looked up among stored 8-team outcomes, so send the legacy
  // qf/sf/final fields for it too.
  const completedBracketPayloadFields = () => {
    const rounds = (completedBracket.rounds || []).map((r) => r.map((id) => Number(id)));
    const fields = {
      round_winners: rounds,
      third_place_winner: !isBounty && hasThirdPlaceDecider ? Number(completedBracket.third) : 0,
    };
    if (isBounty) {
      fields.qf_winners = rounds[0] || [];
      fields.sf_winners = rounds[1] || [];
      fields.final_winner = (rounds[2] || [])[0] || 0;
    }
    return fields;
  };
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
        ...completedBracketPayloadFields(),
        include_player_ids: Array.from(effectiveAppliedFilters.include),
        exclude_player_ids: Array.from(effectiveAppliedFilters.exclude),
        variant,
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
    most_outcomes: "Most Likely Winner",
  }[playoffBestMode] || "Best Average Value";
  const playoffTopSubtabs = [
    { key: "average", label: "Average Player Value" },
    { key: "single_outcome", label: "Best Single Outcome" },
    { key: "most_outcomes", label: "Most Likely Winner" },
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
    if (playoffBestMode === "most_outcomes") {
      // Percent chance this roster is the winning pick; older stored runs only
      // have the raw outcome-win count.
      const prob = team?.outcome_win_probability;
      if (prob !== undefined && prob !== null) return Number(prob) * 100;
      return Number(team?.outcome_wins || 0);
    }
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
      return `Win chance ${(Number(team?.outcome_win_probability || 0) * 100).toFixed(1)}% | Wins ${formatOutcomeWins(
        team?.outcome_wins
      )} of ${playoffOutcomeCount.toLocaleString()} outcomes`;
    }
    return `EV ${Number(team?.total_ev || 0).toFixed(2)}`;
  };
  const playoffOutcomeDescriptor = (outcomeIdx) => {
    const outcome = (results?.outcomes || [])[outcomeIdx];
    if (!outcome) return null;
    const bracket = outcome.bracket || {};
    const finalRow = (bracket.final || [])[0] || {};
    const name = (id) => teamLookup[id] || String(id);
    return {
      probability: Number(outcome.probability || 0),
      champion: name(finalRow.winner),
      runnerUp: name(finalRow.loser),
      sfWinners: (bracket.semis || []).map((m) => name(m.winner)).join(", "),
      qfWinners: (bracket.quarters || []).map((m) => name(m.winner)).join(", "),
      third: (bracket.third_place || [])[0] ? name(bracket.third_place[0].winner) : "",
    };
  };

  const loadEventsForPlayoff = async (retriesLeft = 3) => {
    // A busy backend (giant combo-blob parses hold the GIL) can stall this
    // request; retry instead of leaving the event dropdown blank, which reads
    // as the active event having been unset.
    let data = null;
    try {
      data = await api.get("/events/");
    } catch (e) {
      if (retriesLeft > 0) setTimeout(() => loadEventsForPlayoff(retriesLeft - 1), 5000);
      return;
    }
    if (data?.detail) return;
    const allEvents = Array.isArray(data.events) ? data.events : [];
    setEvents(allEvents);
    const active = data.active_event_id;
    setSelectedEventId(active == null ? "" : String(active));
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
    const data = await api.get(`/playoff/latest?variant=${variant}`, 120000);
    if (!data?.exists) {
      setLatestPayload(null);
      setResults(null);
      setUpdatedAt("");
      setStoredSize(0);
      return;
    }
    const payload = data.payload || {};
    setLatestPayload(payload);
    setSlots((payload.team_slots || []).map((x) => String(x)));
    const loadedSize = (payload.team_slots || []).length;
    setBracketSize([2, 4, 6, 8, 16].includes(loadedSize) ? loadedSize : 8);
    setStoredSize(loadedSize);
    setMcSims(Number(payload.mc_sims) || 5000);
    setHasThirdPlaceDecider(Boolean(payload.has_third_place_decider));
    if (isBounty) {
      const savedPairs = payload.qf_pairs || [];
      if (savedPairs.length === 4) {
        setDraftPicks(savedPairs.slice(0, 3).map((pair) => String(pair?.[1] || "")));
      }
      const restored = {};
      Object.entries(payload.sf_picks || {}).forEach(([key, pairs]) => {
        const pick = pairs?.[0]?.[1];
        if (pick) restored[key] = String(pick);
      });
      setSfPicks(restored);
    }
    setResults(data.results || null);
    if (loadedSize !== 6) {
      // The completed-bracket picker doesn't model the 6-team byes shape.
      hydrateCompletedBracketFromBracket(data.results?.bracket, loadedSize || bracketSize);
    }
    setUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  const loadLatestCompletedBracket = async () => {
    const data = await api.get(`/playoff/best-team/bracket-from-latest/latest?variant=${variant}`, 120000);
    if (!data?.exists) return;
    const payload = data.payload || {};
    // Prefer the general round_winners; fall back to the legacy 8-team fields.
    let rounds;
    if (Array.isArray(payload.round_winners) && payload.round_winners.length) {
      rounds = payload.round_winners.map((r) => (r || []).map((id) => String(id || "")));
    } else {
      const savedQf = (payload.qf_winners || []).slice(0, 4).map((id) => String(id || ""));
      const savedSf = (payload.sf_winners || []).slice(0, 2).map((id) => String(id || ""));
      rounds = [
        [...savedQf, "", "", "", ""].slice(0, 4),
        [...savedSf, ""].slice(0, 2),
        [payload.final_winner ? String(payload.final_winner) : ""],
      ];
    }
    setCompletedBracket({ rounds, third: payload.third_place_winner ? String(payload.third_place_winner) : "" });
    setCompletedBracketUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
  };

  const loadLatestSharedCombinations = async () => {
    const data = await api.get(`/playoff/best-team/from-latest/latest?variant=${variant}`, 120000);
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
    // While the event's team list is still loading, filteredTeams is empty —
    // wiping then would erase slots freshly hydrated from the saved bracket.
    if (filteredTeams.length === 0) return;
    const allowed = new Set(filteredTeams.map((t) => t.team_id));
    setSlots((prev) => prev.map((v) => (allowed.has(Number(v)) ? v : "")));
  }, [filteredTeams]);

  const run = async () => {
    const ids = slots.map((s) => Number(s));
    if (ids.some((id) => !id)) return;
    if (isBounty && !draftedQfPairs) return;
    const sims = Math.max(500, Math.min(200000, Number(mcSims) || 5000));
    setBusy(true);
    setRunMessage("");
    setProcessedSims(0);
    setTotalSims(slots.length >= 16 ? sims : !isBounty && hasThirdPlaceDecider ? 256 : 128);
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
        has_third_place_decider: isBounty ? false : hasThirdPlaceDecider,
        mc_sims: sims,
        ...(isBounty ? { variant: "bounty", qf_pairs: draftedQfPairs, sf_picks: bountySfPicksPayload() } : {}),
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

  // Self-running valuations: recompute automatically once the bracket is
  // seeded, and again whenever the seeds or the third-place rule change.
  // Stored valuations that already cover the exact same bracket are reused
  // as-is — no recompute on every visit to the tab.
  const lastAutoRunRef = useRef("");
  useEffect(() => {
    if (isBounty || busy) return;
    if (storedSize === null) return; // stored-sim hydration pending
    const ids = slots.map((s) => Number(s));
    if (ids.length === 0 || ids.some((id) => !id)) return;
    const sig = ids.join("-") + (hasThirdPlaceDecider ? ":3rd" : "");
    if (lastAutoRunRef.current === sig) return;
    const storedIds = (latestPayload?.team_slots || []).map((x) => Number(x));
    const storedSig =
      storedIds.length > 0 && storedIds.every(Boolean)
        ? storedIds.join("-") + (latestPayload?.has_third_place_decider ? ":3rd" : "")
        : null;
    // stage_stats gate: stored results from before the round-source feature
    // are recomputed once so the popup has its data.
    if (results?.stage_stats && storedSig === sig) {
      lastAutoRunRef.current = sig;
      return;
    }
    lastAutoRunRef.current = sig;
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slots, hasThirdPlaceDecider, busy, storedSize, isBounty, results, latestPayload]);

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
        variant,
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

  // Self-running combinations: kick off whenever the stored valuations are
  // newer than the stored combination set (or no combinations exist yet).
  const combosLaunchRef = useRef(false);
  useEffect(() => {
    if (busy || !results) return;
    if (storedSize === null) return; // stored-sim hydration pending
    if (
      sharedCombosUpdatedAt &&
      updatedAt &&
      new Date(sharedCombosUpdatedAt).getTime() >= new Date(updatedAt).getTime()
    )
      return;
    if (combosLaunchRef.current) return;
    combosLaunchRef.current = true;
    runSharedCombinations().finally(() => {
      combosLaunchRef.current = false;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [busy, results, updatedAt, sharedCombosUpdatedAt, storedSize]);

  const resetStoredPlayoff = async () => {
    await api.delete(`/playoff/latest?variant=${variant}`);
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

  // Content-based signature so background team refreshes (new array/Set identities
  // with identical contents) do not re-fire the expensive combination query.
  const effectiveFiltersSignature = useMemo(
    () =>
      JSON.stringify({
        include: Array.from(effectiveAppliedFilters.include).sort((a, b) => a - b),
        exclude: Array.from(effectiveAppliedFilters.exclude).sort((a, b) => a - b),
      }),
    [effectiveAppliedFilters]
  );

  useEffect(() => {
    if (!baseTeams) return;
    querySharedCombinations(page);
  }, [baseTeams, effectiveFiltersSignature, comboSearch, sortKey, playoffBestMode]);

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
    // Overlapping queries resolve in arbitrary order; only the newest one may
    // apply its response, otherwise a slow stale result overwrites fresh data.
    const seq = ++comboQuerySeqRef.current;
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
        variant,
      };
      if (playoffTopSubtab === "completed") {
        if (!completedBracketDerived.complete) return;
        Object.assign(body, completedBracketPayloadFields());
      }
      const data = await api.post(endpoint, body, 120000);
      if (seq !== comboQuerySeqRef.current) return;
      setTopTeams(data.top_teams || []);
      setAllTeams(data.page_teams || []);
      setFilteredCount(Number(data.filtered_count || 0));
      setSharedComboCount(data.total_teams != null ? Number(data.total_teams) : sharedComboCount);
      setCombosApproximate(Boolean(data.approximate));
      setPage(Number(data.page ?? nextPage ?? 0));
      if (playoffTopSubtab === "completed") {
        setCompletedBracketResult(data);
      }
    } catch (e) {
      if (seq !== comboQuerySeqRef.current) return;
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
      const total = ["rating", "win", "role", "booster"].reduce((sum, part) => sum + completedBreakdownValue(row, part), 0);
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
    <Section title={isBounty ? "Bounty Playoffs (BO3)" : "Playoff Bracket (BO3)"}>
      <div className="stack">
        <div className="tab-bar small">
          <button className={playoffTab === "stage" ? "tab active" : "tab"} onClick={() => setPlayoffTab("stage")}>
            Bracket Stage
          </button>
          <button className={playoffTab === "value" ? "tab active" : "tab"} onClick={() => setPlayoffTab("value")}>
            Player Value
          </button>
          <button className={playoffTab === "top5" ? "tab active" : "tab"} onClick={() => setPlayoffTab("top5")}>
            Top 5 Teams
          </button>
        </div>

        {playoffTab === "stage" && isBounty && (
          <>
            <div className="card sub">
              <h3>Seeding</h3>
              <p className="muted">
                Seeds 1-4 are the top half and get drafted; seeds 5-8 are the bottom half and pick their quarter-final
                opponent in seed order (highest-rated bottom-half team drafts first).
              </p>
              <div className="grid three">
                {slots.map((slotValue, idx) => (
                  <div className="field" key={`bounty-seed-${idx}`}>
                    <span>
                      Seed {idx + 1} {idx < 4 ? "(top half)" : "(bottom half)"}
                    </span>
                    <select value={slotValue} onChange={(e) => setSlot(idx, e.target.value)} disabled={busy}>
                      {playoffTeamOptions.map((option) => (
                        <option key={option.value || "empty"} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                ))}
              </div>
            </div>
            <div className="card sub">
              <h3>Quarter-final Draft</h3>
              {slots.slice(4, 8).some((s) => !s) || slots.slice(0, 4).some((s) => !s) ? (
                <p className="muted">Fill in all 8 seeds above to run the draft.</p>
              ) : (
                <>
                  {[0, 1, 2].map((pickIdx) => {
                    const drafter = Number(slots[4 + pickIdx]);
                    const takenElsewhere = new Set(
                      draftPicks.filter((p, i) => i !== pickIdx && p).map((p) => String(p))
                    );
                    const options = slots
                      .slice(0, 4)
                      .map((s) => Number(s))
                      .filter((id) => id && !takenElsewhere.has(String(id)));
                    return (
                      <div className="field" key={`bounty-pick-${pickIdx}`}>
                        <span>
                          Pick {pickIdx + 1}: {teamLookup[drafter] || `Seed ${5 + pickIdx}`} plays
                        </span>
                        <select
                          value={draftPicks[pickIdx]}
                          onChange={(e) =>
                            setDraftPicks((prev) => {
                              const next = [...prev];
                              next[pickIdx] = e.target.value;
                              return next;
                            })
                          }
                          disabled={busy}
                        >
                          <option value="">Select opponent</option>
                          {options.map((id) => (
                            <option key={id} value={String(id)}>
                              {teamLookup[id] || id}
                            </option>
                          ))}
                        </select>
                      </div>
                    );
                  })}
                  <p className="muted">
                    {draftedQfPairs
                      ? `${teamLookup[draftedQfPairs[3][0]] || "Seed 8"} gets the remaining team: ${
                          teamLookup[draftedQfPairs[3][1]] || draftedQfPairs[3][1]
                        }`
                      : "Seed 8 automatically plays the remaining top-half team."}
                  </p>
                </>
              )}
              {draftedQfPairs && (
                <div className="playoff-bracket-shell">
                  <div className="playoff-bracket-column qf">
                    <h3>Drafted Quarter-finals</h3>
                    {draftedQfPairs.map((pair, idx) => (
                      <BracketMatchCard
                        key={`bounty-qf-${idx}`}
                        title={`QF ${idx + 1}`}
                        rows={
                          <>
                            {pair.map((teamId, rowIdx) => (
                              <div className="playoff-team-row" key={`bounty-qf-${idx}-${rowIdx}`}>
                                <TeamBadge teamId={teamId} />
                                <span>{teamLookup[teamId] || teamId}</span>
                              </div>
                            ))}
                          </>
                        }
                      />
                    ))}
                  </div>
                </div>
              )}
            </div>
            {draftedQfPairs && (
              <div className="card sub">
                <h3>Semi-final Drafts (per scenario)</h3>
                <p className="muted">
                  After the quarter-finals the two lowest-seeded survivors draft again. For each possible set of QF
                  winners, choose who the first drafter picks; the other pairing is forced. Unset scenarios default to
                  the drafter picking the lower-seeded top survivor.
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>QF Winners</th>
                      <th>First Drafter</th>
                      <th>Picks</th>
                      <th>Other Semi-final</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bountySfScenarios.map((sc) => {
                      const pairs = bountySfPairsFor(sc.winners);
                      return (
                        <tr key={sc.key}>
                          <td>{sc.winners.map((id) => teamLookup[id] || id).join(", ")}</td>
                          <td>{teamLookup[sc.drafter] || sc.drafter}</td>
                          <td>
                            <select
                              value={String(Number(sfPicks[sc.key]) && sc.options.includes(Number(sfPicks[sc.key])) ? sfPicks[sc.key] : pairs[0][1])}
                              onChange={(e) => setSfPicks((prev) => ({ ...prev, [sc.key]: e.target.value }))}
                              disabled={busy}
                            >
                              {sc.options.map((id) => (
                                <option key={id} value={String(id)}>
                                  {teamLookup[id] || id}
                                </option>
                              ))}
                            </select>
                          </td>
                          <td>
                            {(teamLookup[pairs[1][0]] || pairs[1][0]) + " vs " + (teamLookup[pairs[1][1]] || pairs[1][1])}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
            <div className="actions">
              <button className="primary" onClick={run} disabled={busy || !draftedQfPairs}>
                {busy ? "Running..." : "Run Bounty Playoffs And Store Valuations"}
              </button>
              <button className="danger" onClick={resetStoredPlayoff} disabled={busy || !results}>
                Reset Stored Valuations
              </button>
              {updatedAt && <p className="muted">Stored: {new Date(updatedAt).toLocaleString()}</p>}
            </div>
          </>
        )}
        {playoffTab === "stage" && !isBounty && (
          <>
            {playoffAutofillMessage && <p className="muted">{playoffAutofillMessage}</p>}
            {renderSeedingBracket()}
            {bracketSize !== 6 && (
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
              </div>
            )}
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
                {combosApproximate && (
                  <p className="muted">
                    16-team field: too large to score every roster exactly, so the strongest {sharedComboCount.toLocaleString()} candidate
                    teams are ranked. Average value is exact; ceiling and most-likely-winner are near-exact among these candidates.
                  </p>
                )}
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
                {slots.length === 6 ? (
                  <p className="muted">
                    Completed-bracket scoring isn't supported for the 6-team byes bracket yet.
                  </p>
                ) : (
                  renderCompletedPicker()
                )}
                <div className="actions">
                  {!baseTeams && <p className="muted">Run Combinations once above to score this bracket.</p>}
                  {baseTeams && !completedBracketDerived.complete && <p className="muted">Complete the bracket to score the saved combinations.</p>}
                  {baseTeams && completedBracketDerived.complete && (
                    <>
                      <button className="primary" onClick={runCompletedBracket} disabled={busy}>
                        {busy
                          ? `Computing Boosters... ${processedCombos.toLocaleString()} / ${totalCombos.toLocaleString()}`
                          : "Compute Exact Booster Ranking"}
                      </button>
                      <span className="muted">
                        The table below updates instantly but ranks without roster booster assignment. This recomputes
                        booster-exact totals for the picked bracket.
                      </span>
                    </>
                  )}
                </div>
                {busy && totalCombos > 0 && (
                  <div className="card sub">
                    <p className="muted">
                      Booster ranking: {processedCombos.toLocaleString()} / {totalCombos.toLocaleString()}
                      {totalCombos > processedCombos ? ` | ETA: ${formatBatchEta(completedEtaSeconds)}` : ""}
                    </p>
                    <div className="progress">
                      <div
                        className="progress-bar determinate"
                        style={{ width: `${Math.min(100, Math.max(0, (processedCombos / totalCombos) * 100))}%` }}
                      />
                    </div>
                  </div>
                )}
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
          <div className="event-team-rows">
            {Object.entries(results.teams).map(([tid, data]) => {
              const info = (teams || []).find((t) => Number(t.team_id) === Number(tid));
              const stageStats = results.stage_stats || null;
              const stageList = stageStats?.stages || [];
              const teamStage = stageStats?.teams?.[String(tid)] || null;
              const rows = Object.entries(data.players || {}).sort(
                (a, b) => Number(b[1].total_points ?? 0) - Number(a[1].total_points ?? 0)
              );
              return (
                <div className="event-team-row" key={tid}>
                  <div className="event-team-head">
                    <TeamLogo hltvTeamId={info?.hltv_team_id} name={teamLookup[Number(tid)]} size={42} />
                    <div className="event-team-headtext">
                      <span className="event-team-name">{teamLookup[Number(tid)] || `Team ${tid}`}</span>
                      {teamStage && (
                        <div className="event-team-reach">
                          {stageList
                            .filter((s) => Number(teamStage.reach?.[s] || 0) > 0.0005)
                            .map((s) => (
                              <div className="event-player-stat" key={s}>
                                <div className="event-player-mini">
                                  {Math.round(Number(teamStage.reach[s]) * 100)}%
                                </div>
                                <div className="event-player-stat-label">{STAGE_SHORT[s] || s}</div>
                              </div>
                            ))}
                          <div className="event-player-stat">
                            <div className="event-player-mini">
                              {Math.round(Number(teamStage.champion || 0) * 100)}%
                            </div>
                            <div className="event-player-stat-label">Title</div>
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                  <div className="event-team-players">
                    {rows.map(([pid, comps]) => {
                      const total = Number(comps.total_points ?? 0);
                      const pStages = stageStats?.players?.[String(pid)] || null;
                      return (
                        <div
                          className="event-player-card clickable"
                          key={pid}
                          onClick={() =>
                            openScoringBreakdown({
                              player_id: Number(pid),
                              name: playerLookup[Number(pid)] || pid,
                              team_id: Number(tid),
                              points: total,
                              rating: Number(comps.rating_points_total || 0),
                              win: Number(comps.win_points_total || 0),
                              role: Number(comps.role_points_total || 0),
                              booster: Number(comps.booster_points_total || 0),
                              role_id: comps.role_id,
                              stage_ev: pStages,
                              stage_list: stageList,
                              team_reach: teamStage,
                              booster_slots: comps.booster_slots,
                              components_available: true,
                              point_breakdown: comps.point_breakdown || [],
                            })
                          }
                        >
                          <PlayerPhoto playerId={Number(pid)} name={playerLookup[Number(pid)]} size={52} />
                          <div className="event-player-name">{playerLookup[Number(pid)] || pid}</div>
                          {comps.role_id != null && (
                            <div className="event-player-role">
                              <RoleBadge roleId={comps.role_id} size={16} />
                              <span>{ROLE_NAMES[comps.role_id] || `Role ${comps.role_id}`}</span>
                            </div>
                          )}
                          <div className="event-player-rating">{total.toFixed(2)}</div>
                          <div className="event-player-stats ev-mini">
                            {[
                              ["Rating", comps.rating_points_total],
                              ["Win", comps.win_points_total],
                              ["Role", comps.role_points_total],
                              ["Boost", comps.booster_points_total],
                            ].map(([label, value]) => (
                              <div className="event-player-stat" key={label}>
                                <div className="event-player-mini">{Number(value || 0).toFixed(1)}</div>
                                <div className="event-player-stat-label">{label}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              );
            })}
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
              {playoffBestMode === "most_outcomes" &&
                (Array.isArray(team.winning_outcome_indexes) &&
                team.winning_outcome_indexes.length > 0 &&
                (results?.outcomes || []).length > 0 ? (
                  <div>
                    <p className="muted">Bracket outcomes this roster wins ({team.winning_outcome_indexes.length}):</p>
                    <ul className="muted">
                      {team.winning_outcome_indexes
                        .map((outcomeIdx) => playoffOutcomeDescriptor(outcomeIdx))
                        .filter(Boolean)
                        .sort((a, b) => b.probability - a.probability)
                        .slice(0, 12)
                        .map((o, i) => (
                          <li key={i}>
                            {(o.probability * 100).toFixed(2)}% — {o.champion} beats {o.runnerUp} in the final
                            {o.third ? ` | 3rd: ${o.third}` : ""} | SF winners: {o.sfWinners} | QF winners: {o.qfWinners}
                          </li>
                        ))}
                    </ul>
                    {team.winning_outcome_indexes.length > 12 && (
                      <p className="muted">+{team.winning_outcome_indexes.length - 12} more lower-probability outcomes</p>
                    )}
                  </div>
                ) : (
                  <p className="muted">Re-run Combinations to see which bracket outcomes this roster wins.</p>
                ))}
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
          <div className="modal player-sources-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>{completedPlayerBreakdown.name || `Player ${completedPlayerBreakdown.player_id}`} Point Sources</h3>
              <button className="close" onClick={() => setCompletedPlayerBreakdown(null)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <div className="breakdown-hero">
                {completedPlayerBreakdown.role_id != null && (
                  <div className="breakdown-stat role">
                    <RoleBadge roleId={completedPlayerBreakdown.role_id} size={42} />
                    <div className="breakdown-stat-label">
                      {ROLE_NAMES[completedPlayerBreakdown.role_id] || `Role ${completedPlayerBreakdown.role_id}`}
                    </div>
                  </div>
                )}
                {[
                  ["Rating", "rating"],
                  ["Win", "win"],
                  ["Role", "role"],
                  ["Booster", "booster"],
                ].map(([label, key]) => (
                  <div className="breakdown-stat" key={key}>
                    <div className="breakdown-stat-value">
                      {completedBreakdownValue(completedPlayerBreakdown, key).toFixed(2)}
                    </div>
                    <div className="breakdown-stat-label">{label}</div>
                  </div>
                ))}
                <div className="breakdown-stat total">
                  <div className="breakdown-stat-value">
                    {completedBreakdownValue(completedPlayerBreakdown, "total").toFixed(2)}
                  </div>
                  <div className="breakdown-stat-label">Total</div>
                </div>
              </div>
              {completedPlayerBreakdown.stage_ev &&
                Array.isArray(completedPlayerBreakdown.stage_list) &&
                completedPlayerBreakdown.stage_list.length > 0 && (
                <div className="stack">
                  <h4>Round Sources</h4>
                  <div className="round-cards">
                    {completedPlayerBreakdown.stage_list.map((s) => {
                      const cell = completedPlayerBreakdown.stage_ev[s];
                      if (!cell || typeof cell !== "object") return null;
                      const reach = Number(completedPlayerBreakdown.team_reach?.reach?.[s] ?? NaN);
                      const isBye = Number.isFinite(reach) && reach < 0.0005 && Math.abs(Number(cell.total || 0)) > 0.005;
                      const slotInfo = (completedPlayerBreakdown.booster_slots || []).find(
                        (b) => Number(b.booster_id) === Number(cell.booster_id)
                      );
                      const opps = Array.isArray(cell.opponents) ? cell.opponents : [];
                      const playSum = opps.reduce((a, o) => a + Number(o.prob || 0), 0);
                      const winUncond = opps.reduce(
                        (a, o) => a + Number(o.prob || 0) * Number(o.win_chance || 0),
                        0
                      );
                      const wWin = playSum > 0 ? winUncond / playSum : 0;
                      const oppEV = opps.reduce((a, o) => a + Number(o.prob || 0) * Number(o.total || 0), 0);
                      const elimContrib = Number(cell.total || 0) - oppEV;
                      const missProb = Math.max(0, 1 - playSum);
                      const showMiss = missProb > 0.0005 && Math.abs(elimContrib) > 0.005;
                      const missPts = showMiss ? elimContrib / missProb : 0;
                      return (
                        <div className="round-card" key={s}>
                          <div className="round-card-top">
                            <span className="round-card-name">{STAGE_FULL[s] || s}</span>
                            <span className="round-card-play">
                              <span className="round-card-chance">
                                {isBye ? "Bye" : opps.length > 0 ? `${Math.round(wWin * 100)}%` : "-"}
                              </span>
                              {!isBye && <span className="event-player-stat-label">Match win</span>}
                            </span>
                          </div>
                          <div className="event-player-rating">{Number(cell.total || 0).toFixed(2)}</div>
                          <div className="event-player-stats ev-mini">
                            {[
                              ["Rating", cell.rating],
                              ["Win", cell.win],
                              ["Role", cell.role],
                              ["Boost", cell.booster],
                            ].map(([label, value]) => (
                              <div className="event-player-stat" key={label}>
                                <div className="event-player-mini">{Number(value || 0).toFixed(1)}</div>
                                <div className="event-player-stat-label">{label}</div>
                              </div>
                            ))}
                          </div>
                          {cell.booster_id != null && (
                            <div className="round-card-boost">
                              <BoosterBadge boosterId={cell.booster_id} size={20} />
                              <span className="round-card-boost-name">{cell.booster_name || `Booster ${cell.booster_id}`}</span>
                              <span className="round-card-boost-rate">
                                {(Number(cell.booster_rate || 0) * 100).toFixed(0)}%
                              </span>
                              {slotInfo?.edge != null && (
                                <span className="round-card-boost-edge">
                                  {Number(slotInfo.edge) >= 0 ? "+" : ""}
                                  {(Number(slotInfo.edge) * 100).toFixed(0)}% vs avg
                                </span>
                              )}
                            </div>
                          )}
                          {Array.isArray(cell.opponents) && cell.opponents.length > 0 && (
                            <div className="round-opps">
                              <div className="round-opp head">
                                <span className="round-opp-name">Opponent</span>
                                <span className="round-opp-prob">Play</span>
                                <span className="round-opp-win">Win</span>
                                <span className="round-opp-pts">Pts</span>
                              </div>
                              {cell.opponents.map((o) => {
                                const oinfo = (teams || []).find((t) => Number(t.team_id) === Number(o.team_id));
                                return (
                                  <div className="round-opp" key={o.team_id}>
                                    <TeamLogo hltvTeamId={oinfo?.hltv_team_id} name={teamLookup[o.team_id]} size={16} />
                                    <span className="round-opp-name">
                                      {teamLookup[o.team_id] || o.team_id}
                                      {o.rank ? ` (#${o.rank})` : ""}
                                    </span>
                                    <span className="round-opp-prob">{Math.round(Number(o.prob || 0) * 100)}%</span>
                                    <span className="round-opp-win">{Math.round(Number(o.win_chance || 0) * 100)}%</span>
                                    <span className="round-opp-pts">{Number(o.total || 0).toFixed(1)}</span>
                                  </div>
                                );
                              })}
                              {showMiss && (
                                <div className="round-opp miss">
                                  <span className="round-opp-name">Eliminated earlier</span>
                                  <span className="round-opp-prob">{Math.round(missProb * 100)}%</span>
                                  <span className="round-opp-win">—</span>
                                  <span className="round-opp-pts">{missPts.toFixed(1)}</span>
                                </div>
                              )}
                              <div className="round-opp total">
                                <span className="round-opp-name">Total</span>
                                <span className="round-opp-prob">
                                  {Math.round((playSum + (showMiss ? missProb : 0)) * 100)}%
                                </span>
                                <span className="round-opp-win">{Math.round(wWin * 100)}%</span>
                                <span className="round-opp-pts">{Number(cell.total || 0).toFixed(1)}</span>
                              </div>
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
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

// Human labels for detected tournament structures. Built from everything the
// kind detector reports (kind + group format + combined playoff bracket), so
// "groups with a combined playoff" reads as exactly that instead of a bare
// "groups"/"playoff" pick.
const describeEventFormat = (k) => {
  if (!k || !k.kind) return null;
  // The backend now composes an authoritative label from the event page's
  // structured data (variant, group count/size, bracket size) — prefer it.
  if (k.label) return { key: k.label, short: k.label, long: "" };
  const gf = String(k.group_format || "");
  const po = Number(k.playoff_size || 0);
  const teams = Number(k.team_count || 0);
  if (k.kind === "swiss") {
    return {
      key: "swiss",
      short: "Swiss Stage",
      long: `Swiss system${teams ? ` (${teams} teams)` : ""} — win three rounds to advance, lose three and you're out.`,
    };
  }
  if (k.kind === "bounty") {
    return {
      key: "bounty",
      short: "Bounty",
      long: "BLAST Bounty draft — seeded teams pick their quarter-final opponents in a single-elimination bracket.",
    };
  }
  if (k.kind === "groups") {
    const gfShort = gf === "gsl4" ? "GSL Groups" : gf === "de8" ? "Double-Elim Groups" : "Group Stage";
    const gfLong =
      gf === "gsl4"
        ? "Four-team GSL groups — opening, winners', elimination and decider matches (double elimination inside each group)."
        : gf === "de8"
        ? "Eight-team double-elimination groups — an upper and lower bracket inside each group."
        : "Round-robin style group stage.";
    if (po > 0) {
      return {
        key: `groups-${gf || "rr"}-po`,
        short: `${gfShort} + Playoff`,
        long: `${gfLong} Combined with a ${po}-team playoff bracket that the group placings feed into.`,
      };
    }
    return { key: `groups-${gf || "rr"}`, short: gfShort, long: gfLong };
  }
  if (k.kind === "playoff") {
    return {
      key: `playoff-${teams || po || 0}`,
      short: `Playoff${teams ? ` (${teams} teams)` : ""}`,
      long: `Single-elimination playoff bracket${teams ? ` with ${teams} priced teams` : ""}.`,
    };
  }
  return { key: String(k.kind), short: String(k.kind), long: "" };
};

function FormatsPanel() {
  // Static catalog of the tournament formats the app is built to handle.
  return (
          <div className="stack">
            <p className="muted">
              Tournament formats this app has been built to handle — the modes the Tournament page can simulate
              and optimize for.
            </p>
            <div className="card sub">
              <h4>Swiss Stage</h4>
              <div className="fmt-diagram">
                {[
                  ["0-0"],
                  ["1-0", "0-1"],
                  ["2-0", "1-1", "0-2"],
                  ["3-0 ✓", "2-1", "1-2", "0-3 ✗"],
                  ["3-1 ✓", "2-2", "1-3 ✗"],
                  ["3-2 ✓", "2-3 ✗"],
                ].map((col, i) => (
                  <div className="fmt-col" key={`sw-${i}`}>
                    <span className="fmt-col-label">R{i + 1}</span>
                    {col.map((rec) => (
                      <span
                        key={rec}
                        className={`fmt-node ${rec.includes("✓") ? "win" : rec.includes("✗") ? "loss" : ""}`}
                      >
                        {rec}
                      </span>
                    ))}
                  </div>
                ))}
              </div>
            </div>
            <div className="card sub">
              <h4>GSL Groups (4-team double elimination)</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Opening</span>
                  <span className="fmt-node">Match 1</span>
                  <span className="fmt-node">Match 2</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Winners / Losers</span>
                  <span className="fmt-node win">Winners' match</span>
                  <span className="fmt-node loss">Elimination match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Last chance</span>
                  <span className="fmt-node hot">Decider</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Out of group</span>
                  <span className="fmt-node win">1st — winners' winner</span>
                  <span className="fmt-node win">2nd — decider winner</span>
                  <span className="fmt-node loss">Out — elim &amp; decider losers</span>
                </div>
              </div>
            </div>
            <div className="card sub">
              <h4>Double-Elimination Groups (8 teams, top 4 qualify — EWC)</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Opening round</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Upper semi-finals</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Winners qualify (2)</span>
                </div>
              </div>
              <div className="fmt-diagram">
                <span className="fmt-drop">losers drop ↓</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Lower round 1</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node loss">Losers out</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Lower semi-finals</span>
                  <span className="fmt-node muted-node">Upper-SF losers join</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Winners qualify (2)</span>
                  <span className="fmt-node loss">Losers out</span>
                </div>
              </div>
            </div>
            <div className="card sub">
              <h4>Double-Elimination Groups (8 teams, top 3 qualify — Cologne)</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Opening round</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Upper semi-finals</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Upper final</span>
                  <span className="fmt-node hot">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Winner → playoff semis</span>
                  <span className="fmt-node win">Loser qualifies</span>
                </div>
              </div>
              <div className="fmt-diagram">
                <span className="fmt-drop">losers drop ↓</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Lower round 1</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node loss">Losers out</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Lower semi-finals</span>
                  <span className="fmt-node muted-node">Upper-SF losers join</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node loss">Losers out</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Lower final</span>
                  <span className="fmt-node hot">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Winner qualifies</span>
                  <span className="fmt-node loss">Loser out</span>
                </div>
              </div>
            </div>
            <div className="card sub">
              <h4>Single-Elimination Playoff</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Quarter-finals</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Semi-finals</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Final</span>
                  <span className="fmt-node hot">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Champion</span>
                </div>
              </div>
              <p className="muted">Also runs as 2, 4 and 16-team brackets.</p>
            </div>
            <div className="card sub">
              <h4>Playoff with Semi-final Byes (6 teams)</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Quarter-finals</span>
                  <span className="fmt-node muted-node">4 lower seeds</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Semi-finals</span>
                  <span className="fmt-node hot">2 top seeds enter here</span>
                  <span className="fmt-node">Match</span>
                  <span className="fmt-node">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Final</span>
                  <span className="fmt-node hot">Match</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-node win">Champion</span>
                </div>
              </div>
              <p className="muted">
                The bracket the Cologne-style groups feed: group winners skip the quarter-finals. Not yet supported
                by the playoff simulator (full 2/4/8/16 brackets only).
              </p>
            </div>
            <div className="card sub">
              <h4>Bounty Draft</h4>
              <div className="fmt-diagram">
                <div className="fmt-col">
                  <span className="fmt-col-label">Challengers</span>
                  <span className="fmt-node hot">Seed 5</span>
                  <span className="fmt-node hot">Seed 6</span>
                  <span className="fmt-node hot">Seed 7</span>
                  <span className="fmt-node">Seed 8 — leftover</span>
                </div>
                <span className="fmt-arrow">
                  pick
                  <br />→
                </span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Targets</span>
                  <span className="fmt-node">Seed 1</span>
                  <span className="fmt-node">Seed 2</span>
                  <span className="fmt-node">Seed 3</span>
                  <span className="fmt-node">Seed 4</span>
                </div>
                <span className="fmt-arrow">→</span>
                <div className="fmt-col">
                  <span className="fmt-col-label">Then</span>
                  <span className="fmt-node">8-team single-elim bracket</span>
                  <span className="fmt-node muted-node">Semi-final drafts per scenario</span>
                </div>
              </div>
            </div>
          </div>
  );
}

function DevLabTab({ players }) {
  const [devTab, setDevTab] = useState("rating");
  return (
    <div className="stack">
      <div className="tab-bar small">
        <button className={devTab === "rating" ? "tab active" : "tab"} onClick={() => setDevTab("rating")}>
          Rating Lab
        </button>
        <button className={devTab === "model" ? "tab active" : "tab"} onClick={() => setDevTab("model")}>
          Model Lab
        </button>
        <button className={devTab === "formats" ? "tab active" : "tab"} onClick={() => setDevTab("formats")}>
          Formats
        </button>
      </div>
      {devTab === "rating" && <RatingLabTab players={players} />}
      {devTab === "model" && <ModelLabTab />}
      {devTab === "formats" && <FormatsPanel />}
    </div>
  );
}

function EventsTab({ refreshData, notify, players, teams = [], onOpenPlayer, onOpenTeam }) {
  const [events, setEvents] = useState([]);
  const [activeEventId, setActiveEventId] = useState(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  // Full kind-detection result per event (kind, group format, playoff size,
  // team count) — drives the Type column and the Formats sub-tab.
  const [eventKinds, setEventKinds] = useState({});
  useEffect(() => {
    let cancelled = false;
    (events || []).forEach((ev) => {
      const id = Number(ev.event_id);
      if (!id || eventKinds[id] !== undefined) return;
      api
        .get(`/events/${id}/kind`, 60000)
        .then((d) => {
          if (!cancelled && d?.kind) setEventKinds((prev) => ({ ...prev, [id]: d }));
        })
        .catch(() => {});
    });
    return () => {
      cancelled = true;
    };
  }, [events]);

  const loadEvents = async () => {
    const res = await api.get("/events/");
    if (res?.detail) {
      setMessage(String(res.detail));
      return;
    }
    setEvents(res.events || []);
    setActiveEventId(res.active_event_id ?? null);
  };

  useEffect(() => {
    loadEvents();
  }, []);

  // Teams & Prices below the table always shows the ACTIVE event.
  const [activeEventDetail, setActiveEventDetail] = useState(null);
  useEffect(() => {
    if (!activeEventId) {
      setActiveEventDetail(null);
      return undefined;
    }
    let cancelled = false;
    api
      .get(`/events/${activeEventId}`)
      .then((d) => {
        if (!cancelled && d && !d.detail) setActiveEventDetail(d);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [activeEventId]);

  // Active event rosters: teams in HLTV-rank order (best first, unranked
  // last), players by price, each joined with their overall rating.
  const eventTeamGroups = useMemo(() => {
    const byTeam = {};
    (activeEventDetail?.players || []).forEach((p) => {
      const teamName = p.team_name || "Unknown Team";
      if (!byTeam[teamName]) byTeam[teamName] = [];
      byTeam[teamName].push(p);
    });
    const teamByName = {};
    (teams || []).forEach((t) => {
      teamByName[String(t.name || "").trim().toLowerCase()] = t;
    });
    const ratingById = {};
    (players || []).forEach((p) => {
      ratingById[Number(p.player_id)] = p.rating;
    });
    const groups = Object.entries(byTeam).map(([teamName, teamPlayers]) => {
      const info = teamByName[teamName.trim().toLowerCase()] || null;
      return {
        teamName,
        info,
        rank: Number(info?.hltv_rank) > 0 ? Number(info.hltv_rank) : null,
        players: teamPlayers
          .slice()
          .sort((a, b) => Number(b.price || 0) - Number(a.price || 0))
          .map((p) => ({ ...p, rating: ratingById[Number(p.player_id)] })),
      };
    });
    groups.sort((a, b) => (a.rank ?? 9999) - (b.rank ?? 9999) || a.teamName.localeCompare(b.teamName));
    return groups;
  }, [activeEventDetail, teams, players]);

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
      await refreshData();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack">
      <Section title="Events">
        <div className="stack">
          {events.length > 0 && (
            <div className="card sub">
              <h4>Imported Events</h4>
              <table>
                <thead>
                  <tr>
                    <th>Name</th>
                    <th>Fantasy Event</th>
                    <th>HLTV Event</th>
                    <th>Type</th>
                    <th>Teams</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {events.map((ev) => (
                    <tr key={ev.event_id}>
                      <td>{ev.name || "-"}</td>
                      <td>{ev.event_id}</td>
                      <td>{ev.hltv_event_id ?? "-"}</td>
                      <td>{describeEventFormat(eventKinds[ev.event_id])?.short || "..."}</td>
                      <td>{ev.team_count ?? 0}</td>
                      <td>
                        <div className="actions" style={{ marginTop: 0 }}>
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

          {activeEventDetail && (
            <div className="card sub">
              <h4>
                {events.find((e) => e.event_id === activeEventDetail.event_id)?.name ||
                  `Event ${activeEventDetail.event_id}`}{" "}
                — Teams &amp; Prices
              </h4>
              <div className="event-team-rows">
                {eventTeamGroups.map((g) => (
                  <div className="event-team-row" key={g.teamName}>
                    <div
                      className={`event-team-head ${g.info?.team_id && onOpenTeam ? "clickable" : ""}`}
                      onClick={() => g.info?.team_id && onOpenTeam && onOpenTeam(g.info.team_id)}
                    >
                      <TeamLogo hltvTeamId={g.info?.hltv_team_id} name={g.teamName} size={42} />
                      <div className="event-team-headtext">
                        <span className="event-team-name">{g.teamName}</span>
                        {g.rank && <span className="event-team-rank">HLTV #{g.rank}</span>}
                      </div>
                    </div>
                    <div className="event-team-players">
                      {g.players.map((p) => (
                        <div
                          className={`event-player-card ${onOpenPlayer ? "clickable" : ""}`}
                          key={`${g.teamName}-${p.player_id}`}
                          onClick={() => onOpenPlayer && onOpenPlayer(p.player_id)}
                        >
                          <PlayerPhoto playerId={p.player_id} name={p.player_name} size={56} />
                          <div className="event-player-name">{p.player_name || `Player ${p.player_id}`}</div>
                          <div className="event-player-stats">
                            <div className="event-player-stat">
                              <div className="event-player-rating">
                                {Number.isFinite(Number(p.rating)) ? Number(p.rating).toFixed(2) : "-"}
                              </div>
                              <div className="event-player-stat-label">Rating</div>
                            </div>
                            <div className="event-player-stat">
                              <div className="event-player-price">${Number(p.price || 0).toLocaleString()}</div>
                              <div className="event-player-stat-label">Price</div>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
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

function MatchesDataPanel({ notify, mode = "full", teams = [], players = [], onOpenPlayer, onOpenTeam }) {
  // Split across pages: Scheduling renders mode="import" (ingestion controls +
  // progress), Database > Matches renders mode="view" (stored-matches browser).
  const showImport = mode !== "view";
  const showTable = mode !== "import";
  const [recentResults, setRecentResults] = useState([]);
  const [recentResultsLoading, setRecentResultsLoading] = useState(false);
  const [recentResultsError, setRecentResultsError] = useState("");
  const [recentResultsImportMode, setRecentResultsImportMode] = useState("until_date");
  const [recentResultsPages, setRecentResultsPages] = useState("3");
  const [recentResultsUntilDate, setRecentResultsUntilDate] = useState("");
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
  const recentResultsImportActive = ["queued", "running", "pausing", "canceling"].includes(recentResultsImportStatus);
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
    const value = String(recentResultsUntilDate || "").trim();
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return "";
    const parsed = new Date(`${value}T00:00:00Z`);
    return Number.isNaN(parsed.getTime()) ? "" : value;
  };

  const clearRecentResultsImportStorage = () => {
    localStorage.removeItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    localStorage.removeItem(RECENT_RESULTS_IMPORT_JOB_STARTED_AT_KEY);
    setRecentResultsImportJobId("");
  };

  const [matchSearch, setMatchSearch] = useState("");
  const [matchSort, setMatchSort] = useState("date_desc");
  const [matchRankValue, setMatchRankValue] = useState("");
  const [matchRankScope, setMatchRankScope] = useState("both");
  const [matchRankDir, setMatchRankDir] = useState("within");
  const loadStoredRecentResults = async (offset = 0, searchOverride = null, sortOverride = null, rankOverride = {}) => {
    const safeOffset = Math.max(0, Number(offset) || 0);
    const q = searchOverride !== null ? searchOverride : matchSearch;
    const s = sortOverride !== null ? sortOverride : matchSort;
    const rv = rankOverride.value !== undefined ? rankOverride.value : matchRankValue;
    const rs = rankOverride.scope !== undefined ? rankOverride.scope : matchRankScope;
    const rd = rankOverride.dir !== undefined ? rankOverride.dir : matchRankDir;
    const rankParams =
      Number(rv) > 0 ? `&vrs_rank=${Number(rv)}&vrs_scope=${encodeURIComponent(rs)}&vrs_dir=${encodeURIComponent(rd)}` : "";
    setRecentResultsLoading(true);
    setRecentResultsError("");
    try {
      const res = await api.get(
        `/events/hltv-results?limit=100&offset=${safeOffset}&search=${encodeURIComponent(q)}&sort=${encodeURIComponent(s)}${rankParams}`
      );
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
  const changeMatchSearch = (value) => {
    setMatchSearch(value);
    loadStoredRecentResults(0, value, null);
  };
  const changeMatchSort = (value) => {
    setMatchSort(value);
    loadStoredRecentResults(0, null, value);
  };
  const changeMatchRankValue = (value) => {
    setMatchRankValue(value);
    loadStoredRecentResults(0, null, null, { value });
  };
  const changeMatchRankScope = (value) => {
    setMatchRankScope(value);
    loadStoredRecentResults(0, null, null, { scope: value });
  };
  const changeMatchRankDir = (value) => {
    setMatchRankDir(value);
    loadStoredRecentResults(0, null, null, { dir: value });
  };
  const hltvIdByTeamName = useMemo(() => {
    const m = {};
    (teams || []).forEach((t) => {
      if (t.hltv_team_id) m[String(t.name || "").trim().toLowerCase()] = Number(t.hltv_team_id);
    });
    return m;
  }, [teams]);
  const logoForTeamName = (name) => hltvIdByTeamName[String(name || "").trim().toLowerCase()];

  const AUTO_FETCH_JOBS_KEY = "hltv_auto_fetch_jobs_after_import";
  const [autoRunFetchJobs, setAutoRunFetchJobs] = useState(() => localStorage.getItem(AUTO_FETCH_JOBS_KEY) === "1");
  const autoRunFetchJobsRef = useRef(autoRunFetchJobs);
  const [autoChainStatus, setAutoChainStatus] = useState("");
  const toggleAutoRunFetchJobs = (checked) => {
    setAutoRunFetchJobs(checked);
    autoRunFetchJobsRef.current = checked;
    localStorage.setItem(AUTO_FETCH_JOBS_KEY, checked ? "1" : "0");
  };

  const pollFetchJobUntilDone = async (basePath, jobId) => {
    for (;;) {
      let status;
      try {
        status = await api.get(`${basePath}/job/${jobId}`, 60000);
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 5000));
        continue;
      }
      const st = String(status?.status || "");
      if (["completed", "failed", "paused", "canceled"].includes(st)) return status;
      await new Promise((resolve) => setTimeout(resolve, 3000));
    }
  };

  const runAutoFetchJobs = async () => {
    try {
      setAutoChainStatus("Auto: fetching missing historical map stats...");
      const hist = await api.post("/events/hltv-results/historical-map-stats/start", {});
      if (hist?.job_id) {
        const done = await pollFetchJobUntilDone("/events/hltv-results/historical-map-stats", hist.job_id);
        if (String(done?.status) !== "completed") {
          setAutoChainStatus(`Auto: historical stats job ended (${done?.status}); veto backfill not started.`);
          return;
        }
      }
      setAutoChainStatus("Auto: fetching missing match vetoes...");
      const veto = await api.post("/events/hltv-results/veto-backfill/start", {});
      if (veto?.job_id) {
        const done = await pollFetchJobUntilDone("/events/hltv-results/veto-backfill", veto.job_id);
        if (String(done?.status) !== "completed") {
          setAutoChainStatus(`Auto: veto backfill ended (${done?.status}).`);
          return;
        }
      }
      setAutoChainStatus("Auto: import + both fetch jobs complete.");
      if (notify) notify("Import and fetch jobs complete");
    } catch (e) {
      setAutoChainStatus(`Auto fetch jobs failed: ${e?.message || e}`);
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
        if (status.status === "canceled") {
          if (notify) notify("HLTV results import canceled.");
          clearRecentResultsImportStorage();
          done = true;
          break;
        }
        if (status.status === "completed") {
          const res = status.result || {};
          if (notify) {
            notify(
              `Imported HLTV results: ${res.kept ?? res.fetched ?? 0} kept, ${res.skipped_existing || 0} already imported, ${res.rank_filtered_out || 0} rank-filtered, ${res.date_filtered_out || 0} date-filtered, ${res.inserted || 0} inserted, ${res.updated || 0} updated`
            );
          }
          clearRecentResultsImportStorage();
          await loadStoredRecentResults(0);
          if (autoRunFetchJobsRef.current) runAutoFetchJobs();
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

  const cancelRecentResultsImportJob = async () => {
    const jobId = recentResultsImportJobId || localStorage.getItem(RECENT_RESULTS_IMPORT_JOB_ID_KEY);
    if (!jobId) return;
    setRecentResultsImportStatus("canceling");
    setRecentResultsImportCurrent("Canceling after current request");
    try {
      const status = await api.post(`/events/hltv-results/import/job/${jobId}/cancel`, {});
      if (status?.detail) {
        setRecentResultsError(String(status.detail));
        return;
      }
      setRecentResultsImportStatus(String(status.status || "canceling"));
      setRecentResultsImportPhase(String(status.phase || status.status || ""));
      setRecentResultsImportCurrent(String(status.current || ""));
      if (String(status.status || "") === "canceled") {
        setRecentResultsImporting(false);
        clearRecentResultsImportStorage();
        if (notify) notify("HLTV results import canceled.");
      }
    } catch (e) {
      setRecentResultsError(e?.message || "Failed to cancel HLTV results import.");
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
      {showImport && (
      <>
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
            <input
              type="date"
              value={recentResultsUntilDate}
              onChange={(e) => setRecentResultsUntilDate(e.target.value)}
              max={new Date().toISOString().slice(0, 10)}
            />
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
          <button
            className="secondary"
            onClick={pauseRecentResultsImportJob}
            disabled={["pausing", "canceling"].includes(recentResultsImportStatus)}
          >
            {recentResultsImportStatus === "pausing" ? "Pausing..." : "Pause"}
          </button>
        )}
        {recentResultsImportResumable && recentResultsImportJobId && (
          <button className="secondary" onClick={resumeRecentResultsImportJob}>
            Resume
          </button>
        )}
        {(recentResultsImportActive || recentResultsImportResumable) && recentResultsImportJobId && (
          <button className="danger" onClick={cancelRecentResultsImportJob} disabled={recentResultsImportStatus === "canceling"}>
            {recentResultsImportStatus === "canceling" ? "Canceling..." : "Cancel"}
          </button>
        )}
        <label className="checkbox-inline">
          <input
            type="checkbox"
            checked={autoRunFetchJobs}
            onChange={(e) => toggleAutoRunFetchJobs(e.target.checked)}
          />
          <span>Auto-run fetch jobs after import (historical map stats, then vetoes)</span>
        </label>
      </div>
      {autoChainStatus && <p className="muted">{autoChainStatus}</p>}
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
      </>
      )}
      {showTable && (
      <>
      <div className="grid two">
        <Input label="Search Matches" value={matchSearch} onChange={changeMatchSearch} placeholder="Team or day/month/year" />
        <div className="match-rank-filter">
          <Input label="VRS Rank" value={matchRankValue} onChange={changeMatchRankValue} placeholder="e.g. 50" />
          <Select
            label="Direction"
            value={matchRankDir}
            onChange={changeMatchRankDir}
            options={[
              { value: "within", label: "Inside top N" },
              { value: "outside", label: "Outside top N" },
            ]}
          />
          <Select
            label="Teams"
            value={matchRankScope}
            onChange={changeMatchRankScope}
            options={[
              { value: "both", label: "Both teams" },
              { value: "either", label: "At least one team" },
            ]}
          />
        </div>
      </div>
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(Math.max(0, recentResultsOffset - 100))}
          disabled={recentResultsLoading || recentResultsOffset <= 0}
        >
          Previous
        </button>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(recentResultsOffset + 100)}
          disabled={recentResultsLoading || recentResults.length < 100}
        >
          Next
        </button>
      </div>
      {recentResultsError && <p className="error">{recentResultsError}</p>}
      {!recentResultsLoading && !recentResultsError && recentResults.length === 0 && <p className="muted">No matches found.</p>}
      {recentResults.length > 0 && (
        <table>
          <thead>
            <tr>
              <th>Team A</th>
              <th>Score</th>
              <th>Team B</th>
              <SortHeader sortValue={matchSort} asc="date_asc" desc="date_desc" defaultDirection="desc" onChange={changeMatchSort}>Date</SortHeader>
            </tr>
          </thead>
          <tbody>
            {recentResults.map((r, idx) => {
              const s1 = Number(r?.score1);
              const s2 = Number(r?.score2);
              const haveScore = Number.isFinite(s1) && Number.isFinite(s2);
              return (
                <tr
                  key={`hltv-res-${idx}-${r?.match_url || ""}`}
                  className={`row-link ${String(r?.match_url || "") === selectedMatchUrl ? "row-active" : ""}`}
                  onClick={() => openMatchModal(r)}
                >
                  <td><TeamLogo hltvTeamId={logoForTeamName(r?.team1)} name={r?.team1} size={22} />{r?.team1 || "-"}</td>
                  <td className="score-cell">
                    {haveScore ? (
                      <>
                        <span className={s1 > s2 ? "score-win" : s1 < s2 ? "score-loss" : ""}>{s1}</span>
                        {" - "}
                        <span className={s2 > s1 ? "score-win" : s2 < s1 ? "score-loss" : ""}>{s2}</span>
                      </>
                    ) : (
                      "-"
                    )}
                  </td>
                  <td><TeamLogo hltvTeamId={logoForTeamName(r?.team2)} name={r?.team2} size={22} />{r?.team2 || "-"}</td>
                  <td>{formatDMY(r?.match_date)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <div className="actions" style={{ marginTop: 0 }}>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(Math.max(0, recentResultsOffset - 100))}
          disabled={recentResultsLoading || recentResultsOffset <= 0}
        >
          Previous
        </button>
        <button
          className="secondary"
          onClick={() => loadStoredRecentResults(recentResultsOffset + 100)}
          disabled={recentResultsLoading || recentResults.length < 100}
        >
          Next
        </button>
      </div>
      {showMatchModal && selectedMatchRow && (
        <MatchDetailModal
          row={selectedMatchRow}
          teams={teams}
          players={players}
          onClose={() => setShowMatchModal(false)}
          onOpenPlayer={
            onOpenPlayer
              ? (pid) => {
                  setShowMatchModal(false);
                  onOpenPlayer(pid);
                }
              : undefined
          }
          onOpenTeam={
            onOpenTeam
              ? (tid) => {
                  setShowMatchModal(false);
                  onOpenTeam(tid);
                }
              : undefined
          }
        />
      )}
      </>
      )}
    </div>
  );
}

function MatchDetailModal({ row, onClose, teams = [], players = [], onOpenPlayer, onOpenTeam }) {
  // Stored-match detail view shared by the Matches browser and the player
  // card's Recent Matches list.
  let maps = [];
  try {
    const parsed = JSON.parse(String(row?.maps_json || "[]"));
    if (Array.isArray(parsed)) maps = parsed;
  } catch {}
  let playerStats = [];
  try {
    const parsed = JSON.parse(String(row?.player_stats_json || "[]"));
    if (Array.isArray(parsed)) playerStats = parsed;
  } catch {}
  // Per-map scoreboards ({map: [entries]}), filled by the map-scoreboards
  // backfill; "{}" means the page has no per-map tabs.
  let mapPlayerStats = {};
  try {
    const parsed = JSON.parse(String(row?.map_player_stats_json || "{}"));
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) mapPlayerStats = parsed;
  } catch {}
  const sbMapNames = Object.keys(mapPlayerStats).filter(
    (k) => Array.isArray(mapPlayerStats[k]) && mapPlayerStats[k].length > 0
  );
  const [sbMap, setSbMap] = useState("all");
  useEffect(() => setSbMap("all"), [row?.match_url]);
  const activeStats = sbMap !== "all" && mapPlayerStats[sbMap] ? mapPlayerStats[sbMap] : playerStats;
  const hltvIdByName = useMemo(() => {
    const m = {};
    (teams || []).forEach((t) => {
      if (t.hltv_team_id) m[String(t.name || "").trim().toLowerCase()] = Number(t.hltv_team_id);
    });
    return m;
  }, [teams]);
  const logoIdFor = (name) => hltvIdByName[String(name || "").trim().toLowerCase()];
  // Click-through to stored player/team cards — only for ids we actually have.
  const knownPlayerIds = useMemo(
    () => new Set((players || []).map((p) => Number(p.player_id))),
    [players]
  );
  const teamIdByName = useMemo(() => {
    const m = {};
    (teams || []).forEach((t) => {
      if (t.team_id) m[String(t.name || "").trim().toLowerCase()] = Number(t.team_id);
    });
    return m;
  }, [teams]);
  const playerLinkable = (pid) => Boolean(onOpenPlayer) && knownPlayerIds.has(Number(pid));
  const teamIdFor = (name) => teamIdByName[String(name || "").trim().toLowerCase()] || 0;
  const teamLinkable = (name) => Boolean(onOpenTeam) && teamIdFor(name) > 0;
  // Group the selected scoreboard by team, team1's block first, best rating on top.
  const groups = new Map();
  activeStats.forEach((p) => {
    const key = String(p?.team || "").trim() || "Unknown";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  });
  const t1Name = String(row?.team1 || "").trim().toLowerCase();
  const scoreboards = Array.from(groups.entries()).map(([team, entries]) => ({
    team,
    entries: entries.slice().sort((a, b) => (Number(b?.rating) || 0) - (Number(a?.rating) || 0)),
  }));
  scoreboards.sort(
    (a, b) =>
      (a.team.trim().toLowerCase() === t1Name ? 0 : 1) -
      (b.team.trim().toLowerCase() === t1Name ? 0 : 1)
  );
  const ratingClass = (v) => {
    if (!Number.isFinite(v)) return "";
    if (v >= 1.05) return "score-win";
    if (v < 0.95) return "score-loss";
    return "";
  };
  // One rank per team next to its name: HLTV rank when both teams have one,
  // otherwise VRS ranks (many lower-tier matches never have an HLTV rank).
  const toRank = (v) => {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : null;
  };
  const useHltvRanks = toRank(row?.hltv_rank_1) !== null && toRank(row?.hltv_rank_2) !== null;
  const rankLabel = useHltvRanks ? "HLTV" : "VRS";
  const rank1 = useHltvRanks ? toRank(row?.hltv_rank_1) : toRank(row?.vrs_rank_1);
  const rank2 = useHltvRanks ? toRank(row?.hltv_rank_2) : toRank(row?.vrs_rank_2);
  const s1 = Number(row?.score1);
  const s2 = Number(row?.score2);
  const haveScore = Number.isFinite(s1) && Number.isFinite(s2);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal match-modal" onClick={(e) => e.stopPropagation()}>
        <header className="modal-header match-hero-header">
          <button className="close" onClick={onClose}>
            &times;
          </button>
          <div className="match-hero">
            <div
              className={`match-hero-team away-from-center ${teamLinkable(row?.team1) ? "clickable" : ""}`}
              onClick={() => teamLinkable(row?.team1) && onOpenTeam(teamIdFor(row?.team1))}
            >
              <span className="match-team-block">
                <span className="match-team-name">{row?.team1 || "-"}</span>
                {rank1 !== null && <span className="match-team-rank">{rankLabel} #{rank1}</span>}
              </span>
              <TeamLogo hltvTeamId={logoIdFor(row?.team1)} name={row?.team1} size={58} />
            </div>
            <div className="match-hero-score">
              {haveScore ? (
                <>
                  <span className={s1 > s2 ? "score-win" : s1 < s2 ? "score-loss" : ""}>{s1}</span>
                  <span className="match-hero-dash">:</span>
                  <span className={s2 > s1 ? "score-win" : s2 < s1 ? "score-loss" : ""}>{s2}</span>
                </>
              ) : (
                <span className="match-hero-dash">vs</span>
              )}
            </div>
            <div
              className={`match-hero-team ${teamLinkable(row?.team2) ? "clickable" : ""}`}
              onClick={() => teamLinkable(row?.team2) && onOpenTeam(teamIdFor(row?.team2))}
            >
              <TeamLogo hltvTeamId={logoIdFor(row?.team2)} name={row?.team2} size={58} />
              <span className="match-team-block">
                <span className="match-team-name">{row?.team2 || "-"}</span>
                {rank2 !== null && <span className="match-team-rank">{rankLabel} #{rank2}</span>}
              </span>
            </div>
          </div>
          <p className="match-hero-sub">
            {formatDMY(row?.match_date)}
            {row?.event || row?.event_name ? ` | ${row?.event || row?.event_name}` : ""}
          </p>
        </header>
        <div className="modal-body">
          <h4>Maps Played</h4>
          {maps.length === 0 ? (
            <p className="muted">No map details stored for this match.</p>
          ) : (
            <div className="match-maps">
              {maps.map((m, i) => {
                const ms1 = Number(m?.score1);
                const ms2 = Number(m?.score2);
                const haveMapScore = Number.isFinite(ms1) && Number.isFinite(ms2);
                const halves = Array.isArray(m?.halves) ? m.halves : [];
                return (
                  <div
                    className="match-map-row"
                    key={`map-${i}-${m?.map || ""}`}
                    style={{ "--map-color": MAP_BAR_COLORS[m?.map] || "#3a4452" }}
                  >
                    <span className="match-map-name">{m?.map || "-"}</span>
                    <span className="match-map-score">
                      <TeamLogo hltvTeamId={logoIdFor(row?.team1)} name={row?.team1} size={18} />
                      <span className={`match-map-num ${haveMapScore ? (ms1 > ms2 ? "score-win" : ms1 < ms2 ? "score-loss" : "") : ""}`}>
                        {m?.score1 ?? "-"}
                      </span>
                      <span className="match-map-score-dash">-</span>
                      <span className={`match-map-num away ${haveMapScore ? (ms2 > ms1 ? "score-win" : ms2 < ms1 ? "score-loss" : "") : ""}`}>
                        {m?.score2 ?? "-"}
                      </span>
                      <TeamLogo hltvTeamId={logoIdFor(row?.team2)} name={row?.team2} size={18} />
                    </span>
                    <span className="match-map-halves">
                      {halves.map((h, j) => (
                        <span key={`half-${j}`} className="match-map-half">
                          {j > 0 && <span className="match-map-half-sep">·</span>}
                          <span className={`match-map-half-num side-${String(h?.side1 || "").toLowerCase()}`}>{h?.score1 ?? "-"}</span>
                          <span className="match-map-half-colon">:</span>
                          <span className={`match-map-half-num side-${String(h?.side2 || "").toLowerCase()}`}>{h?.score2 ?? "-"}</span>
                        </span>
                      ))}
                    </span>
                  </div>
                );
              })}
            </div>
          )}
          <h4>Scoreboard</h4>
          {sbMapNames.length > 0 && (
            <div className="sb-map-tabs">
              <button
                className={`sb-map-tab ${sbMap === "all" ? "active" : ""}`}
                onClick={() => setSbMap("all")}
              >
                All maps
              </button>
              {sbMapNames.map((m) => (
                <button
                  key={`sbt-${m}`}
                  className={`sb-map-tab ${sbMap === m ? "active" : ""}`}
                  style={{ "--map-color": MAP_BAR_COLORS[m] || "#ff6b1a" }}
                  onClick={() => setSbMap(m)}
                >
                  {m}
                </button>
              ))}
            </div>
          )}
          {scoreboards.length === 0 ? (
            <p className="muted">No player stats stored for this match yet.</p>
          ) : (
            scoreboards.map((sb) => (
              <div className="scoreboard-card" key={`sb-${sb.team}`}>
                <table className="scoreboard-table">
                  <thead>
                    <tr>
                      <th
                        className={`scoreboard-team ${teamLinkable(sb.team) ? "clickable" : ""}`}
                        onClick={() => teamLinkable(sb.team) && onOpenTeam(teamIdFor(sb.team))}
                      >
                        <TeamLogo hltvTeamId={logoIdFor(sb.team)} name={sb.team} size={24} />
                        <span>{sb.team}</span>
                      </th>
                      <th className="num">K-D</th>
                      <th className="num">+/-</th>
                      <th className="num">ADR</th>
                      <th className="num">KAST</th>
                      <th className="num">Rating</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sb.entries.map((p, i) => {
                      const kills = Number(p?.kills);
                      const deaths = Number(p?.deaths);
                      const pm = Number(p?.plus_minus);
                      const adr = Number(p?.adr);
                      const kast = Number(p?.kast);
                      const rating = Number(p?.rating);
                      return (
                        <tr key={`sb-p-${p?.player_id || i}`}>
                          <td className="scoreboard-player">
                            <PlayerPhoto playerId={p?.player_id} name={p?.player} size={24} />
                            {playerLinkable(p?.player_id) ? (
                              <button
                                className="sb-player-link"
                                onClick={() => onOpenPlayer(Number(p.player_id))}
                              >
                                {p?.player || "-"}
                              </button>
                            ) : (
                              <span>{p?.player || "-"}</span>
                            )}
                          </td>
                          <td className="num">
                            {Number.isFinite(kills) && Number.isFinite(deaths)
                              ? `${kills} - ${deaths}`
                              : "-"}
                          </td>
                          <td
                            className={`num ${
                              Number.isFinite(pm)
                                ? pm > 0
                                  ? "score-win"
                                  : pm < 0
                                  ? "score-loss"
                                  : ""
                                : ""
                            }`}
                          >
                            {Number.isFinite(pm) ? (pm > 0 ? `+${pm}` : `${pm}`) : "-"}
                          </td>
                          <td className="num">{Number.isFinite(adr) ? adr.toFixed(1) : "-"}</td>
                          <td className="num">{Number.isFinite(kast) ? `${kast.toFixed(1)}%` : "-"}</td>
                          <td className={`num scoreboard-rating ${ratingClass(rating)}`}>
                            {Number.isFinite(rating) ? rating.toFixed(2) : "-"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ))
          )}
        </div>
        <div className="actions">
          <button className="secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

function DatabaseTab({ players, teams, loading, error, refresh, notify, openPlayerId, onOpenPlayerHandled, openTeamId, onOpenTeamHandled, mapStats, mapStatsModalRefreshRef }) {
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
  const [playerCurve, setPlayerCurve] = useState(null);
  const [playerCurveLoading, setPlayerCurveLoading] = useState(false);
  const [playerCurveError, setPlayerCurveError] = useState("");

  const [selectedTeam, setSelectedTeam] = useState(null);
  const [showTeamModal, setShowTeamModal] = useState(false);
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
  // A player can sit on several stored rosters (old event teams are kept);
  // only their CURRENT team is shown: the roster of the most recent EVENT they
  // appeared in (backend /events/player-latest-teams). Team-row creation order
  // is no signal — a player can move to a team whose row is older than their
  // previous team's. Players never priced into an event fall back to any
  // stored roster containing them. One-element arrays keep join()/[0]
  // consumers working unchanged.
  const [eventLatestTeams, setEventLatestTeams] = useState({});
  useEffect(() => {
    let cancelled = false;
    api
      .get("/events/player-latest-teams", 30000)
      .then((data) => {
        if (!cancelled) setEventLatestTeams(data?.teams || {});
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [players]);
  const latestTeamByPlayer = useMemo(() => {
    const teamIdByName = {};
    teams.forEach((t) => {
      teamIdByName[String(t.name || "").trim().toLowerCase()] = Number(t.team_id) || 0;
    });
    const best = {};
    Object.entries(eventLatestTeams).forEach(([pid, row]) => {
      const name = String(row?.team_name || "").trim();
      if (!name) return;
      best[pid] = { team_id: teamIdByName[name.toLowerCase()] || 0, team_name: name };
    });
    // Fallback for players outside any event's rosters.
    teams.forEach((t) => {
      const tid = Number(t.team_id) || 0;
      [t.player1_id, t.player2_id, t.player3_id, t.player4_id, t.player5_id].filter(Boolean).forEach((pid) => {
        if (!best[pid]) best[pid] = { team_id: tid, team_name: t.name || `Team ${tid}` };
      });
    });
    return best;
  }, [teams, eventLatestTeams]);
  const playerTeamLookup = useMemo(() => {
    const m = {};
    Object.entries(latestTeamByPlayer).forEach(([pid, team]) => {
      m[pid] = [team.team_name];
    });
    return m;
  }, [latestTeamByPlayer]);
  const playerTeamLinks = useMemo(() => {
    const m = {};
    Object.entries(latestTeamByPlayer).forEach(([pid, team]) => {
      m[pid] = [team];
    });
    return m;
  }, [latestTeamByPlayer]);

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

  // Hero enrichment: the selected player's current team (with HLTV id for the
  // logo), headline chips, and their team's recent match form.
  const heroTeam = useMemo(() => {
    const link = (playerTeamLinks[playerForm.player_id] || [])[0];
    if (!link) return null;
    const t = teams.find((x) => Number(x.team_id) === Number(link.team_id));
    return { ...link, hltv_team_id: t?.hltv_team_id };
  }, [playerTeamLinks, playerForm.player_id, teams]);
  const heroExtras = useMemo(() => {
    const pid = Number(selectedPlayer);
    const p = players.find((x) => Number(x.player_id) === pid);
    if (!p) return null;
    const normalizeRate = (v) => {
      const n = Number(v);
      if (!Number.isFinite(n)) return null;
      if (n < 0) return 0;
      return n > 1 ? n / 100 : n;
    };
    const rated = players.filter((x) => Number(x.rating) > 0);
    const rank = 1 + rated.filter((x) => Number(x.rating) > Number(p.rating || 0)).length;
    let bestRole = null;
    Object.entries(roleNames).forEach(([id, label]) => {
      const maj = normalizeRate(roleForm[id]?.major ?? roleForm[id]?.major_win_pct);
      const min = normalizeRate(roleForm[id]?.minor ?? roleForm[id]?.minor_win_pct);
      if (maj === null || min === null) return;
      const expected = 5 * maj + 2 * min - 2 * (1 - maj - min);
      if (!bestRole || expected > bestRole.expected) bestRole = { id: Number(id), label, expected };
    });
    // Best booster = the one that beats the field-wide average by the most —
    // a high raw rate on a booster everyone triggers isn't an edge.
    let bestBooster = null;
    Object.entries(boosterNames).forEach(([id, label]) => {
      const v = Number(boosterForm[id]);
      const avg = Number(boosterAverages[id]);
      if (!Number.isFinite(v) || !Number.isFinite(avg)) return;
      const delta = v - avg;
      if (!bestBooster || delta > bestBooster.delta) bestBooster = { id: Number(id), label, value: v, delta };
    });
    return { price: Number(p.price || 0), rank, ratedCount: rated.length, bestRole, bestBooster };
  }, [players, selectedPlayer, roleForm, boosterForm, boosterAverages]);
  // Recent form: the player's last matches with their per-match rating,
  // extracted server-side from the archived match-page scoreboards.
  const [playerRecentForm, setPlayerRecentForm] = useState([]);
  const [playerMatchDetail, setPlayerMatchDetail] = useState(null);
  const openStoredMatch = async (matchUrl) => {
    if (!matchUrl) return;
    try {
      const row = await api.get(`/events/hltv-results/stored?match_url=${encodeURIComponent(matchUrl)}`, 15000);
      if (row && row.team1) setPlayerMatchDetail(row);
      else notify("No stored details for that match.");
    } catch (e) {
      notify(`Could not load match: ${e?.message || "unknown error"}`);
    }
  };
  useEffect(() => {
    const pid = Number(selectedPlayer);
    if (!showPlayerModal || !Number.isFinite(pid) || pid <= 0) {
      setPlayerRecentForm([]);
      return;
    }
    let cancelled = false;
    api
      .get(`/players/${pid}/recent-form?limit=5`, 30000)
      .then((res) => {
        if (!cancelled) setPlayerRecentForm(Array.isArray(res?.matches) ? res.matches : []);
      })
      .catch(() => {
        if (!cancelled) setPlayerRecentForm([]);
      });
    return () => {
      cancelled = true;
    };
  }, [showPlayerModal, selectedPlayer]);

  useEffect(() => {
    if (selectedPlayer) {
      const p = players.find((x) => x.player_id === selectedPlayer);
      if (p) {
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
    if (!openTeamId) return;
    const tid = Number(openTeamId);
    if (Number.isFinite(tid) && tid > 0) {
      setDbTab("teams");
      setSelectedTeam(tid);
      setShowTeamModal(true);
    }
    if (onOpenTeamHandled) onOpenTeamHandled();
  }, [openTeamId, onOpenTeamHandled]);

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

  // Keep an open team modal's map stats fresh when the shared import job finishes.
  useEffect(() => {
    if (!mapStatsModalRefreshRef) return undefined;
    mapStatsModalRefreshRef.current = async () => {
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
    return () => {
      mapStatsModalRefreshRef.current = null;
    };
  }, [selectedTeam, mapStatsModalRefreshRef]);

  const currentMapPool = ACTIVE_MAP_POOL;
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
  // Self-computed veto profile from stored match vetoes — covers maps HLTV's
  // team-maps page omits entirely (a permaban has no played-stats there).
  const [teamVetoProfile, setTeamVetoProfile] = useState(null);
  useEffect(() => {
    const tid = Number(selectedTeam);
    if (!showTeamModal || !Number.isFinite(tid) || tid <= 0) {
      setTeamVetoProfile(null);
      return;
    }
    let cancelled = false;
    api
      .get(`/teams/${tid}/veto-profile?months=3`, 30000)
      .then((res) => {
        if (!cancelled) setTeamVetoProfile(res || null);
      })
      .catch(() => {
        if (!cancelled) setTeamVetoProfile(null);
      });
    return () => {
      cancelled = true;
    };
  }, [showTeamModal, selectedTeam]);
  const teamMapStatsRows = useMemo(() => {
    try {
      const parsed = JSON.parse(String(teamForm.map_stats_json || "[]"));
      const byMap = {};
      (Array.isArray(parsed) ? parsed : []).forEach((row) => {
        const map = canonicalMapName(row?.map);
        if (map) byMap[map] = row;
      });
      const vetoByMap = {};
      const vetoMatches = Number(teamVetoProfile?.matches || 0);
      Object.entries(teamVetoProfile?.maps || {}).forEach(([m, v]) => {
        const map = canonicalMapName(m);
        if (map) vetoByMap[map] = v;
      });
      if (Object.keys(byMap).length === 0 && vetoMatches === 0) return [];
      // Every active-pool map gets a card — a map the team never touches is
      // itself information (usually their permaban). Our own veto data wins
      // over HLTV's page rates: it also covers never-played maps.
      return currentMapPool.map((map) => {
        const row = byMap[map] || {};
        const veto = vetoByMap[map];
        const useVeto = vetoMatches > 0;
        return {
          map,
          hasData: Boolean(byMap[map]) || Boolean(veto),
          played: Number(row.played || 0),
          winRate: Number(row.win_rate || 0),
          pickRate: useVeto ? Number(veto?.pick_rate || 0) : Number(row.pick_rate || 0),
          banRate: useVeto ? Number(veto?.ban_rate || 0) : Number(row.ban_rate || 0),
        };
      });
    } catch {
      return [];
    }
  }, [teamForm.map_stats_json, teamVetoProfile]);
  const teamMapStatsTotalPlayed = useMemo(
    () => teamMapStatsRows.reduce((sum, row) => sum + Math.max(0, Number(row.played || 0)), 0),
    [teamMapStatsRows]
  );
  const toNum = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
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
        rawBucketRating: Number.isFinite(Number(row?.raw_bucket_rating)) ? Number(row.raw_bucket_rating) : null,
        priorRating: Number.isFinite(Number(row?.prior_rating)) ? Number(row.prior_rating) : null,
        adjustedPriorRating: Number.isFinite(Number(row?.adjusted_prior_rating)) ? Number(row.adjusted_prior_rating) : null,
        shrinkageWeight: Number.isFinite(Number(row?.shrinkage_weight)) ? Number(row.shrinkage_weight) : null,
        maps: Number(row?.maps || 0),
        estimated: Boolean(row?.estimated),
      }))
      .filter((row) => Number.isFinite(row.tier) && row.tier > 0 && row.tier < 100)
      .sort((a, b) => a.tier - b.tier);
  }, [playerCurve]);
  // The per-player vs-ranked shift baked into the weighted line; surfaced as
  // its own dashed curve so "weighted below both predicted AND actual" is
  // visibly the shift at work, not a glitch.
  const playerTopxShift = useMemo(() => {
    const v = Number(playerCurve?.personal_offset);
    return Number.isFinite(v) ? v : 0;
  }, [playerCurve]);
  const playerTopxRows = useMemo(() => {
    const rows = Array.isArray(playerCurve?.graph_rows) ? playerCurve.graph_rows : [];
    if (rows.length === 0) {
      return playerTopxBucketRows
        .map((row) => ({
          rank: row.tier,
          rankLabel: String(row.tier),
          finalRating: row.bucketRating,
          predictedRating: row.priorRating,
          shiftedRating: row.adjustedPriorRating,
        }))
        .filter((row) => Number.isFinite(row.rank) && row.rank > 0 && row.finalRating !== null);
    }
    return rows
      .map((row) => {
        const predicted = Number.isFinite(Number(row?.predicted_rating)) ? Number(row.predicted_rating) : null;
        return {
          rank: Number(row?.rank),
          rankLabel: String(row?.rank_label || row?.rank || ""),
          finalRating: Number.isFinite(Number(row?.final_rating)) ? Number(row.final_rating) : null,
          predictedRating: predicted,
          shiftedRating: predicted === null ? null : predicted + playerTopxShift,
        };
      })
      .filter((row) => Number.isFinite(row.rank) && row.rank > 0 && row.finalRating !== null)
      .sort((a, b) => a.rank - b.rank);
  }, [playerCurve, playerTopxBucketRows, playerTopxShift]);
  // Actual (observed) ratings — only at tiers with real map data, plotted as points.
  const playerTopxActualPoints = useMemo(
    () =>
      playerTopxBucketRows
        .filter((r) => !r.estimated && r.maps > 0 && Number.isFinite(r.rawBucketRating))
        .map((r) => ({ rank: r.tier, rating: r.rawBucketRating, maps: r.maps, tierLabel: r.tierLabel })),
    [playerTopxBucketRows]
  );
  const playerTopxRatingAxis = useMemo(() => {
    return buildNiceStepAxis(
      [
        ...playerTopxRows.map((row) => row.finalRating),
        ...playerTopxRows.map((row) => row.predictedRating),
        ...playerTopxRows.map((row) => row.shiftedRating),
        ...playerTopxActualPoints.map((p) => p.rating),
      ].filter((v) => Number.isFinite(v)),
      0.05
    );
  }, [playerTopxRows, playerTopxActualPoints]);
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
        case "rating_desc":
          return toNum(b.rating) - toNum(a.rating);
        case "rating_asc":
          return toNum(a.rating) - toNum(b.rating);
        case "team_asc":
          return ((playerTeamLookup[a.player_id] || [])[0] || "").localeCompare(((playerTeamLookup[b.player_id] || [])[0] || ""));
        case "team_desc":
          return ((playerTeamLookup[b.player_id] || [])[0] || "").localeCompare(((playerTeamLookup[a.player_id] || [])[0] || ""));
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
        <button className={dbTab === "maps" ? "tab active" : "tab"} onClick={() => setDbTab("maps")}>
          Maps
        </button>
      </div>

      {dbTab === "maps" && <MapsTab teams={teams} />}

      {dbTab === "players" && <Section title="Players">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <div className="players-panel">
            <div className="grid two">
              <Input label="Search Players" value={playerSearch} onChange={setPlayerSearch} placeholder="Name or team" />
            </div>
            <div className="players-table-wrap">
            <table className="players-table">
              <colgroup>
                <col style={{ width: "34%" }} />
                <col style={{ width: "44%" }} />
                <col style={{ width: "22%" }} />
              </colgroup>
              <thead>
                <tr>
                  <SortHeader sortValue={playerSort} asc="name_asc" desc="name_desc" onChange={setPlayerSort}>Name</SortHeader>
                  <SortHeader sortValue={playerSort} asc="team_asc" desc="team_desc" onChange={setPlayerSort}>Team</SortHeader>
                  <SortHeader sortValue={playerSort} asc="rating_asc" desc="rating_desc" defaultDirection="desc" onChange={setPlayerSort}>Rating</SortHeader>
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
                      <td><PlayerPhoto playerId={p.player_id} name={p.name} size={26} />{p.name}</td>
                      <td>{(playerTeamLookup[p.player_id] || []).join(", ") || "-"}</td>
                      <td>{Number(p.rating || 0).toFixed(2)}</td>
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
          <div className="modal player-modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header player-hero-header">
              <div className="player-hero">
                <PlayerPhoto playerId={playerForm.player_id} name={playerForm.name} size={132} className="hero" />
                <div className="player-hero-main">
                  <h3 className="player-modal-title">{playerForm.name || "Player Details"}</h3>
                  <div className="hero-rating">
                    <span className="hero-rating-label">Rating</span>
                    <span className="hero-rating-value">{Number(playerForm.rating || 0).toFixed(2)}</span>
                  </div>
                </div>
                {heroTeam && (
                  <button
                    type="button"
                    className="player-hero-team"
                    onClick={() => openTeamDetailsFromPlayer(heroTeam.team_id)}
                    title={`Open ${heroTeam.team_name}`}
                  >
                    <TeamLogo hltvTeamId={heroTeam.hltv_team_id} name={heroTeam.team_name} size={84} />
                    <span>{heroTeam.team_name}</span>
                  </button>
                )}
              </div>
              <button className="close" onClick={() => setShowPlayerModal(false)}>
                &times;
              </button>
            </header>
            <div className="modal-body">
              <div className="tab-bar small">
                <button className={playerTab === "info" ? "tab active" : "tab"} onClick={() => setPlayerTab("info")}>
                  Ratings
                </button>
                <button className={playerTab === "roles" ? "tab active" : "tab"} onClick={() => setPlayerTab("roles")}>
                  Roles
                </button>
                <button className={playerTab === "boosters" ? "tab active" : "tab"} onClick={() => setPlayerTab("boosters")}>
                  Boosters
                </button>
                <button className={playerTab === "topxGraph" ? "tab active" : "tab"} onClick={() => setPlayerTab("topxGraph")}>
                  Top X
                </button>
              </div>
              {playerTab === "info" && (
                <div className="stack">
                  <div className="topx-tile-row">
                    {TOP_RATING_TIERS.map((tier) => {
                      // A tier with no maps has no real rating either — a
                      // stored 0/0 just marks "never played vs this tier".
                      const maps = Number(playerForm[`maps_top${tier}`]);
                      const rating = Number(playerForm[`rating_top${tier}`]);
                      const hasData = Number.isFinite(maps) && maps > 0 && Number.isFinite(rating) && rating > 0;
                      return (
                        <div key={`tile-${tier}`} className={`topx-tile ${hasData ? "" : "empty"}`}>
                          <div className="topx-tile-label">Top {tier}</div>
                          <div className="topx-tile-value">{hasData ? rating.toFixed(2) : "---"}</div>
                          <div className="topx-tile-maps">{hasData ? `${Math.round(maps)} maps` : "no maps"}</div>
                        </div>
                      );
                    })}
                  </div>
                  <div className="player-highlight-row">
                    {heroExtras?.bestRole && (
                      <div className="player-highlight-card">
                        <RoleBadge roleId={heroExtras.bestRole.id} size={46} />
                        <div>
                          <div className="player-highlight-label">Best Role</div>
                          <div className="player-highlight-value">{heroExtras.bestRole.label}</div>
                          <div className="player-highlight-sub">{heroExtras.bestRole.expected.toFixed(2)} pts/game</div>
                        </div>
                      </div>
                    )}
                    {heroExtras?.bestBooster && (
                      <div className="player-highlight-card">
                        <BoosterBadge boosterId={heroExtras.bestBooster.id} size={46} />
                        <div>
                          <div className="player-highlight-label">Best Booster</div>
                          <div className="player-highlight-value">{heroExtras.bestBooster.label}</div>
                          <div className="player-highlight-sub">
                            {heroExtras.bestBooster.delta >= 0 ? "+" : ""}
                            {Math.round(heroExtras.bestBooster.delta * 100)}% vs average ({Math.round(heroExtras.bestBooster.value * 100)}%)
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                  <div className="form-section">
                    <h4 className="form-heading">Recent Matches</h4>
                    {playerRecentForm.length === 0 && <p className="muted">No stored matches for this player's team yet.</p>}
                    {playerRecentForm.length > 0 && (
                      <table>
                        <thead>
                          <tr>
                            <th>Date</th>
                            <th>Opponent</th>
                            <th>Result</th>
                            <th>Rating</th>
                          </tr>
                        </thead>
                        <tbody>
                          {playerRecentForm.map((m) => {
                            const opp = teams.find(
                              (x) => String(x.name || "").trim().toLowerCase() === String(m.opponent || "").trim().toLowerCase()
                            );
                            return (
                              <tr
                                key={m.match_url || `${m.date}-${m.opponent}`}
                                className={m.match_url ? "row-link" : ""}
                                onClick={() => openStoredMatch(m.match_url)}
                                title={m.match_url ? "Open stored match details" : undefined}
                              >
                                <td>{formatDMY(m.date)}</td>
                                <td>
                                  <TeamLogo hltvTeamId={opp?.hltv_team_id} name={m.opponent} size={20} />
                                  {m.opponent}
                                </td>
                                <td>
                                  <span className={`hero-form-chip ${m.won ? "win" : "loss"}`}>{m.won ? "W" : "L"}</span>{" "}
                                  {m.score}
                                </td>
                                <td className="form-rating">
                                  {m.rating !== null && Number.isFinite(Number(m.rating)) ? Number(m.rating).toFixed(2) : "—"}
                                </td>
                              </tr>
                            );
                          })}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}
              {playerTab === "topxGraph" && (
                <div className="stack">
                  {playerCurveLoading && <p className="muted">Loading Top-X data...</p>}
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
                        {Number.isFinite(Number(playerCurve?.personal_offset)) && Math.abs(Number(playerCurve.personal_offset)) >= 0.005 ? (
                          <>
                            {" "}| vs-ranked shift:{" "}
                            <span style={{ color: Number(playerCurve.personal_offset) < 0 ? "#f0a763" : "#34d399" }}>
                              {Number(playerCurve.personal_offset) >= 0 ? "+" : ""}
                              {Number(playerCurve.personal_offset).toFixed(3)}
                            </span>
                          </>
                        ) : null}
                      </p>
                      <div className="value-chart-wrap topx-chart">
                        <ResponsiveContainer width="100%" height={260}>
                          <ComposedChart data={playerTopxRows} margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                            <CartesianGrid stroke="#232a34" strokeDasharray="3 3" />
                            <XAxis
                              type="number"
                              dataKey="rank"
                              domain={[1, 50]}
                              ticks={[1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]}
                              interval={0}
                              minTickGap={0}
                              tick={{ fill: "#9fb2c9", fontSize: 12 }}
                              axisLine={{ stroke: "#3a4452" }}
                              tickLine={{ stroke: "#3a4452" }}
                              tickFormatter={(v) => String(v)}
                            />
                            <YAxis
                              tick={{ fill: "#9fb2c9", fontSize: 12 }}
                              axisLine={{ stroke: "#3a4452" }}
                              tickLine={{ stroke: "#3a4452" }}
                              domain={playerTopxRatingAxis.domain}
                              ticks={playerTopxRatingAxis.ticks}
                              interval={0}
                              minTickGap={0}
                              tickFormatter={(v) => Number(v).toFixed(2)}
                            />
                            <Tooltip
                              content={({ active, payload, label }) => {
                                if (!active || !payload || payload.length === 0) return null;
                                const rows = payload.filter(
                                  (p) => p && p.value != null && p.dataKey !== "rank" && p.name !== "rank"
                                );
                                if (rows.length === 0) return null;
                                return (
                                  <div
                                    style={{
                                      background: "#14181f",
                                      border: "1px solid #3a4452",
                                      borderRadius: 10,
                                      padding: "9px 13px",
                                      color: "#e9edf3",
                                      fontSize: 13,
                                      lineHeight: 1.55,
                                    }}
                                  >
                                    <div style={{ fontWeight: 700, marginBottom: 4 }}>Rank {label}</div>
                                    {rows.map((p, i) => {
                                      const maps = p.name === "Actual (observed)" ? p.payload?.maps : null;
                                      return (
                                        <div key={i} style={{ display: "flex", alignItems: "center", gap: 7 }}>
                                          <span
                                            style={{
                                              width: 8,
                                              height: 8,
                                              borderRadius: "50%",
                                              background: p.color || p.stroke || "#9fb2c9",
                                              flex: "none",
                                            }}
                                          />
                                          <span style={{ color: "#9fb2c9" }}>{p.name}:</span>
                                          <span style={{ fontWeight: 600 }}>
                                            {Number(p.value).toFixed(3)}
                                            {maps ? ` (${Math.round(maps)} maps)` : ""}
                                          </span>
                                        </div>
                                      );
                                    })}
                                  </div>
                                );
                              }}
                            />
                            <Legend wrapperStyle={{ color: "#9fb2c9" }} />
                            <Line
                              type="linear"
                              dataKey="predictedRating"
                              name="Predicted (avg curve)"
                              stroke="#8aa0c6"
                              strokeWidth={1.8}
                              strokeDasharray="5 4"
                              dot={false}
                              connectNulls
                              isAnimationActive={false}
                            />
                            {Math.abs(playerTopxShift) >= 0.005 && (
                              <Line
                                type="linear"
                                dataKey="shiftedRating"
                                name={`Predicted + shift (${playerTopxShift >= 0 ? "+" : ""}${playerTopxShift.toFixed(3)})`}
                                stroke="#a78bfa"
                                strokeWidth={1.8}
                                strokeDasharray="2 4"
                                dot={false}
                                connectNulls
                                isAnimationActive={false}
                              />
                            )}
                            <Line
                              type="linear"
                              dataKey="finalRating"
                              name="Weighted (used)"
                              stroke="#22d3ee"
                              strokeWidth={2.4}
                              dot={{ r: 3, fill: "#22d3ee", strokeWidth: 0 }}
                              connectNulls={false}
                              isAnimationActive={false}
                            />
                            <Scatter
                              data={playerTopxActualPoints}
                              dataKey="rating"
                              name="Actual (observed)"
                              fill="#f97316"
                              isAnimationActive={false}
                            />
                          </ComposedChart>
                        </ResponsiveContainer>
                      </div>
                      <table className="topx-bucket-table">
                        <thead>
                          <tr>
                            <th>Bucket</th>
                            <th>Predicted</th>
                            <th title="Predicted curve after this player's vs-ranked shift — the baseline the weighted value blends from">
                              + Shift ({playerTopxShift >= 0 ? "+" : ""}
                              {playerTopxShift.toFixed(3)})
                            </th>
                            <th>Actual</th>
                            <th>Weighted</th>
                            <th>Sample Weight</th>
                            <th>Maps</th>
                          </tr>
                        </thead>
                        <tbody>
                          {playerTopxBucketRows.map((row) => (
                            <tr key={`topx-row-${row.tier}`} style={row.estimated ? { opacity: 0.6 } : undefined}>
                              <td>
                                {row.tierLabel}
                                {row.estimated && <span className="muted"> (est.)</span>}
                              </td>
                              <td style={{ color: "#8aa0c6" }}>
                                {Number.isFinite(row.priorRating) ? row.priorRating.toFixed(3) : "-"}
                              </td>
                              <td style={{ color: "#a78bfa" }}>
                                {Number.isFinite(row.adjustedPriorRating) ? row.adjustedPriorRating.toFixed(3) : "-"}
                              </td>
                              <td style={{ color: "#f0a763" }}>
                                {!row.estimated && row.maps > 0 && Number.isFinite(row.rawBucketRating)
                                  ? row.rawBucketRating.toFixed(3)
                                  : "—"}
                              </td>
                              <td style={{ color: "#22d3ee", fontWeight: 600 }}>
                                {Number.isFinite(row.bucketRating) ? row.bucketRating.toFixed(3) : "-"}
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
                        <BoosterBadge boosterId={id} size={54} />
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
                          <RoleBadge roleId={r.id} />
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
              <div className="modal-title-wrap">
                <TeamLogo hltvTeamId={teamForm.hltv_team_id} name={teamForm.name} size={44} />
                <h3 className="player-modal-title">{teamForm.name || "Team"}</h3>
              </div>
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
                      <PlayerPhoto playerId={pid} name={playerLookup[Number(pid)]} size={72} className="roster-photo" />
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

              <section className="team-detail-block">
                <div className="team-detail-heading-row">
                  <div>
                    <h4 className="team-detail-heading">Map Stats</h4>
                    {teamMapStatsRows.length > 0 && (
                      <p className="muted">
                        Last 3 months: {teamMapStatsTotalPlayed.toLocaleString()} maps played
                        {Number(teamVetoProfile?.matches || 0) > 0
                          ? ` · pick/ban from ${teamVetoProfile.matches} stored vetoes`
                          : ""}
                      </p>
                    )}
                  </div>
                </div>
                {mapStats.show && (
                  <div className="team-map-stats-progress">
                    <p className="muted">
                      {mapStats.processed.toLocaleString()} / {mapStats.total.toLocaleString()} | ok {mapStats.ok} | failed{" "}
                      {mapStats.failed}
                      {mapStats.active && mapStats.total > mapStats.processed ? ` | ETA: ${formatBatchEta(mapStats.etaSeconds)}` : ""}
                    </p>
                    <div className="progress">
                      <div className="progress-bar determinate" style={{ width: `${mapStats.progressPct}%` }} />
                    </div>
                    <p className="muted">Status: {mapStats.statusLabel}</p>
                  </div>
                )}
                <div className="team-map-stats-grid">
                  {teamMapStatsRows.length > 0 ? (
                    teamMapStatsRows.map((row) => (
                      <div
                        className={`team-map-stat-card ${row.hasData ? "" : "empty"}`}
                        key={`team-map-${row.map}`}
                        style={{ "--map-color": MAP_BAR_COLORS[row.map] || MAP_BAR_FALLBACK_COLOR }}
                      >
                        <div className="team-map-stat-head">
                          <span className="team-map-stat-title">{row.map}</span>
                          <span className="team-map-stat-count">{row.played > 0 ? `${row.played} maps` : "not played"}</span>
                        </div>
                        <div className="team-map-stat-main">{row.played > 0 ? formatTeamPct(row.winRate, 1) : "—"}</div>
                        <div className="team-map-stat-bar">
                          <div
                            className="team-map-stat-bar-fill"
                            style={{ width: `${row.played > 0 ? Math.max(0, Math.min(100, Number(row.winRate || 0) * 100)) : 0}%` }}
                          />
                        </div>
                        <div className="team-map-stat-veto">
                          <div className="veto-row">
                            <span className="veto-label">Pick</span>
                            <div className="veto-bar">
                              <div
                                className="veto-fill pick"
                                style={{ width: `${Math.max(0, Math.min(100, Number(row.pickRate || 0) * 100))}%` }}
                              />
                            </div>
                            <span className="veto-val">{formatTeamPct(row.pickRate, 1)}</span>
                          </div>
                          <div className="veto-row">
                            <span className="veto-label">Ban</span>
                            <div className="veto-bar">
                              <div
                                className="veto-fill ban"
                                style={{ width: `${Math.max(0, Math.min(100, Number(row.banRate || 0) * 100))}%` }}
                              />
                            </div>
                            <span className="veto-val">{formatTeamPct(row.banRate, 1)}</span>
                          </div>
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="team-map-stat-empty">No current-pool map stats imported</div>
                  )}
                </div>
              </section>
            </div>
          </div>
        </div>
      )}

      {playerMatchDetail && (
        <MatchDetailModal
          row={playerMatchDetail}
          teams={teams}
          players={players}
          onClose={() => setPlayerMatchDetail(null)}
          onOpenPlayer={(pid) => {
            setPlayerMatchDetail(null);
            openPlayerDetailsFromTeam(pid);
          }}
          onOpenTeam={(tid) => {
            setPlayerMatchDetail(null);
            openTeamDetailsFromPlayer(tid);
          }}
        />
      )}

      {dbTab === "teams" && <Section title="Teams">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <>
            <div className="teams-controls">
              <div className="grid two teams-filters">
                <Input label="Search Teams" value={teamSearch} onChange={setTeamSearch} placeholder="Name or player" />
              </div>
            </div>
            <table>
              <thead>
                <tr>
                  <SortHeader sortValue={teamSort} asc="name_asc" desc="name_desc" onChange={setTeamSort}>Name</SortHeader>
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
                    <td><TeamLogo hltvTeamId={t.hltv_team_id} name={t.name} size={28} />{t.name}</td>
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
          <MatchesDataPanel
            notify={notify}
            mode="view"
            teams={teams}
            players={players}
            onOpenPlayer={openPlayerDetailsFromTeam}
            onOpenTeam={openTeamDetailsFromPlayer}
          />
        </Section>
      )}

    </div>
  );
}

const matchWinnerKey = (roundIndex, matchIndex) => `${roundIndex}:${matchIndex}`;

const roundFromWinnerKey = (key) => Number(String(key).split(":")[0]);

const swissBuchholz = (teamName, states) =>
  (states[teamName]?.opponents || []).reduce((total, opponent) => total + Number(states[opponent]?.wins || 0), 0);

const pairSwissPool = (teamNames, states, ranks) => {
  const ordered = [...teamNames].sort((a, b) => {
    const buchholzGap = swissBuchholz(b, states) - swissBuchholz(a, states);
    if (buchholzGap) return buchholzGap;
    const rankGap = Number(ranks[a] || 9999) - Number(ranks[b] || 9999);
    if (rankGap) return rankGap;
    return a.localeCompare(b);
  });

  const backtrack = (remaining, acc) => {
    if (!remaining.length) return acc;
    const first = remaining[0];
    const rest = remaining.slice(1);
    for (let idx = rest.length - 1; idx >= 0; idx -= 1) {
      const candidate = rest[idx];
      if ((states[first]?.opponents || []).includes(candidate)) continue;
      const result = backtrack([...rest.slice(0, idx), ...rest.slice(idx + 1)], [...acc, { team_a: first, team_b: candidate }]);
      if (result) return result;
    }
    return null;
  };

  const noRematchPairs = backtrack(ordered, []);
  if (noRematchPairs) return noRematchPairs;

  const fallback = [];
  for (let idx = 0; idx < Math.floor(ordered.length / 2); idx += 1) {
    fallback.push({ team_a: ordered[idx], team_b: ordered[ordered.length - 1 - idx] });
  }
  return fallback;
};

const buildReplicaSwiss = (simulatorResult, winnerSelections) => {
  const seedRanks = simulatorResult?.ranks || {};
  const seedNames =
    Array.isArray(simulatorResult?.extracted_team_names) && simulatorResult.extracted_team_names.length
      ? simulatorResult.extracted_team_names
      : Object.keys(seedRanks).sort((a, b) => Number(seedRanks[a] || 9999) - Number(seedRanks[b] || 9999));
  const initialPairs = simulatorResult?.scenarios?.[0]?.initial || [];
  const states = Object.fromEntries(
    seedNames.map((name) => [
      name,
      {
        name,
        wins: 0,
        losses: 0,
        opponents: [],
      },
    ])
  );

  const rounds = [];
  for (let roundIndex = 0; roundIndex < 5; roundIndex += 1) {
    let pairings = [];
    if (roundIndex === 0) {
      pairings = initialPairs;
    } else {
      const buckets = {};
      Object.values(states)
        .filter((team) => team.wins < 3 && team.losses < 3)
        .forEach((team) => {
          const bucket = `${team.wins}:${team.losses}`;
          buckets[bucket] = [...(buckets[bucket] || []), team.name];
        });
      Object.keys(buckets)
        .sort((a, b) => {
          const [aw, al] = a.split(":").map(Number);
          const [bw, bl] = b.split(":").map(Number);
          return bw - aw || al - bl;
        })
        .forEach((bucket) => {
          pairings.push(...pairSwissPool(buckets[bucket], states, seedRanks).map((pair) => ({ ...pair, bucket })));
        });
    }

    if (!pairings.length) break;

    const displayPairings = pairings.map((pair, matchIndex) => {
      const key = matchWinnerKey(roundIndex, matchIndex);
      return {
        ...pair,
        key,
        bucket: pair.bucket || `${states[pair.team_a]?.wins || 0}:${states[pair.team_a]?.losses || 0}`,
        team_a_record: `${states[pair.team_a]?.wins || 0}-${states[pair.team_a]?.losses || 0}`,
        team_b_record: `${states[pair.team_b]?.wins || 0}-${states[pair.team_b]?.losses || 0}`,
        team_a_buchholz: swissBuchholz(pair.team_a, states),
        team_b_buchholz: swissBuchholz(pair.team_b, states),
        selectedWinner: winnerSelections[key] || "",
      };
    });
    rounds.push({ round: roundIndex + 1, pairings: displayPairings });

    if (!displayPairings.every((pair) => pair.selectedWinner)) break;

    displayPairings.forEach((pair) => {
      const winner = pair.selectedWinner;
      const loser = winner === pair.team_a ? pair.team_b : pair.team_a;
      states[winner].wins += 1;
      states[loser].losses += 1;
      states[winner].opponents.push(loser);
      states[loser].opponents.push(winner);
    });
  }

  const standings = Object.values(states).sort((a, b) => {
    const statusGap = Number(b.wins >= 3) - Number(a.wins >= 3) || Number(a.losses >= 3) - Number(b.losses >= 3);
    if (statusGap) return statusGap;
    return (
      b.wins - a.wins ||
      a.losses - b.losses ||
      swissBuchholz(b.name, states) - swissBuchholz(a.name, states) ||
      Number(seedRanks[a.name] || 9999) - Number(seedRanks[b.name] || 9999)
    );
  });

  return { rounds, standings, ranks: seedRanks };
};

const teamInitials = (name) =>
  String(name || "?")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();

function TeamBubble({ name, logos, selected, onClick, disabled = false }) {
  const logo = logos?.[name];
  return (
    <button className={`swiss-team-bubble${selected ? " selected" : ""}`} onClick={onClick} disabled={disabled} title={name}>
      {logo ? <img src={logo} alt={name} /> : <span>{teamInitials(name)}</span>}
    </button>
  );
}

function PlaceholderBubble() {
  return (
    <button className="swiss-team-bubble placeholder" disabled>
      <span>?</span>
    </button>
  );
}

const SWISS_BOARD_COLUMNS = [
  { roundIndex: 0, buckets: [{ key: "0:0", rows: 8 }] },
  { roundIndex: 1, buckets: [{ key: "1:0", rows: 4 }, { key: "0:1", rows: 4 }] },
  { roundIndex: 2, buckets: [{ key: "2:0", rows: 2 }, { key: "1:1", rows: 4 }, { key: "0:2", rows: 2 }] },
  {
    roundIndex: 3,
    buckets: [
      { key: "3:0", rows: 1, outcome: "advanced-perfect" },
      { key: "2:1", rows: 3 },
      { key: "1:2", rows: 3 },
      { key: "0:3", rows: 1, outcome: "eliminated-perfect" },
    ],
  },
  {
    roundIndex: 4,
    buckets: [
      { key: "3:1 / 3:2", rows: 2, outcome: "advanced" },
      { key: "2:2", rows: 3 },
      { key: "1:3 / 2:3", rows: 2, outcome: "eliminated" },
    ],
  },
];

function HltvReplicaSimulatorPanel({ eventId, hltvEventId, hltvEventUrl }) {
  const [simulatorBusy, setSimulatorBusy] = useState(false);
  const [simulatorResult, setSimulatorResult] = useState(null);
  const [simulatorWinners, setSimulatorWinners] = useState({});
  const [simulatorMessage, setSimulatorMessage] = useState("");
  const replicaSwiss = useMemo(() => buildReplicaSwiss(simulatorResult, simulatorWinners), [simulatorResult, simulatorWinners]);

  const loadHltvReplicaSimulator = async () => {
    if (!eventId) {
      setSimulatorMessage("Select or import a fantasy event first.");
      return;
    }
    setSimulatorBusy(true);
    setSimulatorMessage("");
    setSimulatorResult(null);
    setSimulatorWinners({});
    try {
      const res = await requestJson(
        "/admin/infer-hltv-simulator-pairing",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            fantasy_event_id: String(eventId),
            hltv_event_id: hltvEventId ? String(hltvEventId) : undefined,
            hltv_event_url: hltvEventUrl || undefined,
          }),
        },
        90000
      );
      setSimulatorResult(res);
      const best = res.inference?.best;
      setSimulatorMessage(
        best
          ? `Loaded from HLTV event ${res.hltv_event_id || hltvEventId || "resolved"}: ${best.label} (${best.matched || 0}/${best.total || 0} observed round-two pairs).`
          : `Loaded from HLTV event ${res.hltv_event_id || hltvEventId || "resolved"}.`
      );
    } catch (e) {
      setSimulatorMessage(e?.message || "Failed to load HLTV simulator data.");
    } finally {
      setSimulatorBusy(false);
    }
  };

  useEffect(() => {
    setSimulatorResult(null);
    setSimulatorWinners({});
    setSimulatorMessage("");
  }, [eventId, hltvEventId, hltvEventUrl]);

  useEffect(() => {
    if (eventId) {
      loadHltvReplicaSimulator();
    }
  }, [eventId, hltvEventId, hltvEventUrl]);

  const selectReplicaWinner = (matchKey, winner) => {
    const roundIndex = roundFromWinnerKey(matchKey);
    setSimulatorWinners((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([key]) => roundFromWinnerKey(key) <= roundIndex));
      next[matchKey] = winner;
      return next;
    });
  };

  const resetReplicaRound = (roundIndex) => {
    setSimulatorWinners((prev) => Object.fromEntries(Object.entries(prev).filter(([key]) => roundFromWinnerKey(key) < roundIndex)));
  };

  const resetReplicaSimulator = () => setSimulatorWinners({});

  const autoPickReplicaRound = (round, mode) => {
    setSimulatorWinners((prev) => {
      const next = Object.fromEntries(Object.entries(prev).filter(([key]) => roundFromWinnerKey(key) <= round.round - 1));
      round.pairings.forEach((pair) => {
        next[pair.key] =
          mode === "higher"
            ? Number(replicaSwiss.ranks[pair.team_a] || 9999) <= Number(replicaSwiss.ranks[pair.team_b] || 9999)
              ? pair.team_a
              : pair.team_b
            : Math.random() < 0.5
              ? pair.team_a
              : pair.team_b;
      });
      return next;
    });
  };

  return (
    <Section title="HLTV Swiss Replica">
      <div className="stack">
        <div className="actions">
          <button className="primary" onClick={loadHltvReplicaSimulator} disabled={simulatorBusy || !eventId}>
            {simulatorBusy ? "Loading..." : eventId ? `Load Fantasy ${eventId}` : "No Event Selected"}
          </button>
          <button className="secondary" onClick={resetReplicaSimulator} disabled={!simulatorResult || simulatorBusy}>
            Reset Picks
          </button>
        </div>
        <p className="muted">
          Uses the selected fantasy event and its linked HLTV event page to parse the embedded Swiss simulator data.
        </p>
        {simulatorMessage && <p className="muted">{simulatorMessage}</p>}

        {simulatorResult && (
          <>
            <div className="card sub">
              <h4>Replica Simulator</h4>
              {Number.isFinite(Number(simulatorResult.extracted_team_count)) && (
                <p className="muted">
                  Teams detected: {simulatorResult.extracted_team_count} | First round: {simulatorResult.first_round_rule || "unknown"} |
                  Successor rounds: {simulatorResult.successor_round_rule || "unknown"}
                </p>
              )}
              {simulatorResult.inference?.best && (
                <p className="muted">
                  Inferred pairing: {simulatorResult.inference.best.label}
                  {Number(simulatorResult.inference.best.total || 0) > 0 &&
                    ` (${simulatorResult.inference.best.matched || 0}/${simulatorResult.inference.best.total || 0} observed pairs)`}
                </p>
              )}
            </div>

            {replicaSwiss.rounds.length > 0 && (
              <div className="swiss-replica-board">
                <div className="swiss-replica-title">Swiss simulator for {simulatorResult.event_name || `Event ${eventId}`}</div>
                <div className="swiss-round-grid">
                  {SWISS_BOARD_COLUMNS.map((column) => {
                    const round = replicaSwiss.rounds.find((item) => item.round === column.roundIndex + 1);
                    const buckets = (round?.pairings || []).reduce((acc, pair) => {
                      acc[pair.bucket] = [...(acc[pair.bucket] || []), pair];
                      return acc;
                    }, {});
                    const outcomeTeams = (kind) => {
                      if (kind === "advanced-perfect") return replicaSwiss.standings.filter((team) => team.wins >= 3 && team.losses === 0);
                      if (kind === "advanced") return replicaSwiss.standings.filter((team) => team.wins >= 3 && team.losses > 0);
                      if (kind === "eliminated-perfect") return replicaSwiss.standings.filter((team) => team.losses >= 3 && team.wins === 0);
                      if (kind === "eliminated") return replicaSwiss.standings.filter((team) => team.losses >= 3 && team.wins > 0);
                      return [];
                    };
                    return (
                      <div key={column.roundIndex} className="swiss-round-column">
                        <div className="swiss-round-controls">
                          <button onClick={() => resetReplicaRound(column.roundIndex)} disabled={!round}>
                            Reset
                          </button>
                          <button onClick={() => round && autoPickReplicaRound(round, "random")} disabled={!round}>
                            Shuffle
                          </button>
                          <button onClick={() => round && autoPickReplicaRound(round, "higher")} disabled={!round}>
                            Higher seed
                          </button>
                        </div>
                        {column.buckets.map((bucketConfig) => {
                          const pairs = buckets[bucketConfig.key] || [];
                          if (bucketConfig.outcome) {
                            const teams = outcomeTeams(bucketConfig.outcome);
                            return (
                              <div
                                key={bucketConfig.key}
                                className={`swiss-outcome-panel ${bucketConfig.outcome.startsWith("advanced") ? "advanced" : "eliminated"}`}
                              >
                                <div className="swiss-bucket-label">{bucketConfig.key}</div>
                                <div className="swiss-outcome-teams">
                                  {teams.length
                                    ? teams.map((team) => <TeamBubble key={team.name} name={team.name} logos={simulatorResult.team_logos} disabled />)
                                    : Array.from({ length: bucketConfig.rows * 2 }).map((_, idx) => <PlaceholderBubble key={idx} />)}
                                </div>
                              </div>
                            );
                          }
                          return (
                            <div key={bucketConfig.key} className="swiss-bucket-panel">
                              <div className="swiss-bucket-label">{bucketConfig.key.replace(":", "-")}</div>
                              <div className="swiss-match-list">
                                {pairs.length
                                  ? pairs.map((pair) => (
                                      <div key={pair.key} className="swiss-match-card">
                                        <div className="swiss-team-wrap">
                                          <TeamBubble
                                            name={pair.team_a}
                                            logos={simulatorResult.team_logos}
                                            selected={pair.selectedWinner === pair.team_a}
                                            onClick={() => selectReplicaWinner(pair.key, pair.team_a)}
                                          />
                                          <span>{pair.team_a}</span>
                                        </div>
                                        <div className="swiss-vs">vs</div>
                                        <div className="swiss-team-wrap">
                                          <TeamBubble
                                            name={pair.team_b}
                                            logos={simulatorResult.team_logos}
                                            selected={pair.selectedWinner === pair.team_b}
                                            onClick={() => selectReplicaWinner(pair.key, pair.team_b)}
                                          />
                                          <span>{pair.team_b}</span>
                                        </div>
                                      </div>
                                    ))
                                  : Array.from({ length: bucketConfig.rows }).map((_, idx) => (
                                      <div key={idx} className="swiss-match-card placeholder">
                                        <div className="swiss-team-wrap">
                                          <PlaceholderBubble />
                                        </div>
                                        <div className="swiss-vs">vs</div>
                                        <div className="swiss-team-wrap">
                                          <PlaceholderBubble />
                                        </div>
                                      </div>
                                    ))}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </>
        )}
      </div>
    </Section>
  );
}

function TopxImportPanel({ players, notify, refresh }) {
  // Player Top-X data controls (timeframe window + manual batch imports),
  // relocated from the Database tab so all data ingestion lives on the
  // Scheduling page alongside the nightly schedule.
  const [topxMonths, setTopxMonths] = useState("3"); // active window (months of HLTV history)
  const [topxCoverage, setTopxCoverage] = useState({}); // { "3": 337, "6": 0, ... }
  const [topxWindowBusy, setTopxWindowBusy] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchStatus, setBatchStatus] = useState("idle");
  const [batchJobId, setBatchJobId] = useState("");
  const [batchProcessed, setBatchProcessed] = useState(0);
  const [batchTotal, setBatchTotal] = useState(0);
  const [batchOk, setBatchOk] = useState(0);
  const [batchFailed, setBatchFailed] = useState(0);
  const [batchLastError, setBatchLastError] = useState("");
  const [batchEtaSeconds, setBatchEtaSeconds] = useState(null);
  const [eventPlayerIds, setEventPlayerIds] = useState([]);
  const batchPollingRef = useRef(false);
  // Exponentially-weighted seconds-per-player so the ETA tracks the CURRENT pace
  // (recent samples weighted most, older ones decaying), instead of the lifetime
  // average which lags badly when the speed changes mid-run.
  const batchEmaRef = useRef({ lastT: 0, lastProcessed: 0, ema: null });

  const loadTopxWindow = async () => {
    try {
      const data = await api.get("/players/topx-window", 30000);
      if (data?.active) setTopxMonths(String(data.active));
      if (data?.coverage) setTopxCoverage(data.coverage);
    } catch {
      /* non-fatal */
    }
  };
  useEffect(() => {
    loadTopxWindow();
  }, []);

  useEffect(() => {
    let cancelled = false;
    api
      .get("/players/event-player-ids", 30000)
      .then((data) => {
        if (!cancelled) setEventPlayerIds(Array.isArray(data?.player_ids) ? data.player_ids : []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [players]);

  // Switching the window rebuilds the shared tier columns from that window's
  // archive (no re-scrape), so 3/6/12-month data coexist and are swappable.
  const changeTopxWindow = async (months) => {
    setTopxMonths(String(months));
    setTopxWindowBusy(true);
    try {
      const res = await api.post("/players/topx-window/active", { months: Number(months) || 3 }, 60000);
      if (res?.coverage) setTopxCoverage(res.coverage);
      await refresh();
      notify(`Active Top-X window: last ${months} months`);
    } catch (e) {
      notify(`Failed to switch window: ${e?.message || "unknown error"}`);
    } finally {
      setTopxWindowBusy(false);
    }
  };

  const applyBatchStatus = (status, jobIdOverride = "") => {
    const jobId = String(jobIdOverride || status?.job_id || "");
    const processed = Number(status?.processed_players || 0);
    const total = Number(status?.total_players || 0);
    const ok = Number(status?.ok || 0);
    const failed = Number(status?.failed || 0);
    const nextStatus = String(status?.status || "queued");
    const lastError = String(status?.last_error || status?.error || "");

    setBatchStatus(nextStatus);
    setBatchJobId(jobId);
    setBatchProcessed(processed);
    setBatchTotal(total);
    setBatchOk(ok);
    setBatchFailed(failed);
    setBatchLastError(lastError);
    setBatchBusy(["queued", "running", "pausing", "canceling"].includes(nextStatus));

    if (processed > 0 && total > processed) {
      const nowMs = Date.now();
      const st = batchEmaRef.current;
      const ALPHA = 0.3; // smoothing: higher = reacts faster to recent pace
      if (st.lastProcessed > 0 && processed > st.lastProcessed) {
        const instantSecPerItem = (nowMs - st.lastT) / 1000 / (processed - st.lastProcessed);
        st.ema = st.ema == null ? instantSecPerItem : ALPHA * instantSecPerItem + (1 - ALPHA) * st.ema;
      } else if (st.lastProcessed === 0) {
        // Seed from the lifetime average so we show something on the first tick.
        const elapsedSec = Math.max(0.001, (nowMs - getBatchStartedAtMs(status)) / 1000);
        st.ema = elapsedSec / processed;
      }
      if (processed !== st.lastProcessed) {
        st.lastT = nowMs;
        st.lastProcessed = processed;
      }
      const secPerItem = st.ema;
      setBatchEtaSeconds(secPerItem && secPerItem > 0 ? (total - processed) * secPerItem : null);
    } else if (total > 0 && processed >= total) {
      setBatchEtaSeconds(0);
    } else {
      setBatchEtaSeconds(null);
    }

    return { jobId, processed, total, ok, failed, nextStatus, lastError };
  };

  const pollBatchJob = async (jobId) => {
    if (!jobId || batchPollingRef.current) return;
    batchPollingRef.current = true;
    try {
      let done = false;
      let pollFailures = 0;
      while (!done) {
        let status;
        try {
          status = await api.get(`/players/fetch-top-ratings-batch/job/${jobId}`, 60000);
          pollFailures = 0;
        } catch (pollError) {
          // The job keeps running server-side; only give up after repeated failures.
          pollFailures += 1;
          if (pollFailures >= 5) throw pollError;
          await new Promise((resolve) => setTimeout(resolve, 3000));
          continue;
        }
        const { ok, failed, nextStatus, lastError } = applyBatchStatus(status, jobId);

        if (["failed", "paused", "canceled"].includes(nextStatus)) {
          setBatchBusy(false);
          if (nextStatus === "failed") notify(lastError || "Top-X batch failed.");
          done = true;
          break;
        }
        if (nextStatus === "completed") {
          setBatchBusy(false);
          setBatchStatus("idle");
          setBatchEtaSeconds(null);
          await refresh();
          await loadTopxWindow();
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
      setBatchBusy(false);
      setBatchStatus("failed");
      setBatchLastError(String(e?.message || "Failed to poll Top-X batch status."));
      notify(`Top-X batch failed: ${e?.message || "unknown error"}`);
    } finally {
      batchPollingRef.current = false;
    }
  };

  const playerHasCompleteTopRatings = (player) => {
    // "Complete" = the import ran and produced an overall rating. Players with
    // thin per-tier data (few maps vs top teams) are still complete — their
    // rank-adjusted ratings are estimated from the average degradation curve.
    return Boolean(Number(player?.last_topx_import_at)) && Number(player?.rating) > 0;
  };

  const getBatchPlayerIds = (onlyMissing = false) =>
    (players || [])
      .filter((player) => !onlyMissing || !playerHasCompleteTopRatings(player))
      .map((player) => Number(player?.player_id))
      .filter((value) => Number.isFinite(value) && value > 0);

  const runBatch = async (playerIds, emptyMsg) => {
    if (!playerIds || playerIds.length === 0) {
      notify(emptyMsg);
      return;
    }
    setBatchBusy(true);
    setBatchStatus("queued");
    setBatchProcessed(0);
    setBatchTotal(playerIds.length);
    setBatchOk(0);
    setBatchFailed(0);
    setBatchLastError("");
    setBatchEtaSeconds(null);
    batchEmaRef.current = { lastT: 0, lastProcessed: 0, ema: null };
    try {
      const start = await api.post("/players/fetch-top-ratings-batch/start", {
        player_ids: playerIds,
        months: Number(topxMonths) || 3,
      });
      const jobId = String(start?.job_id || "");
      if (!jobId) {
        throw new Error("Failed to start Top-X batch job.");
      }
      setBatchJobId(jobId);
      await pollBatchJob(jobId);
    } catch (e) {
      setBatchStatus("failed");
      setBatchLastError(String(e?.message || "Failed to start Top-X batch job."));
      notify(`Top-X batch failed: ${e?.message || "unknown error"}`);
    } finally {
      setBatchBusy(false);
    }
  };

  const importAll = (onlyMissing = false) =>
    runBatch(
      getBatchPlayerIds(onlyMissing),
      onlyMissing ? "No players are missing Top-X data." : "No players available to import."
    );
  const importCurrentEvent = () =>
    runBatch(eventPlayerIds, "No players found for the current event. Import an event first.");

  const pauseBatchJob = async () => {
    if (!batchJobId) return;
    setBatchStatus("pausing");
    setBatchBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchJobId}/pause`, {});
      const applied = applyBatchStatus(status, batchJobId);
      if (["pausing", "running", "queued"].includes(applied.nextStatus)) {
        pollBatchJob(batchJobId);
      }
    } catch (e) {
      setBatchStatus("running");
      notify(`Failed to pause Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  const cancelBatchJob = async () => {
    if (!batchJobId) return;
    setBatchStatus("canceling");
    setBatchBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchJobId}/cancel`, {});
      const applied = applyBatchStatus(status, batchJobId);
      if (["canceling", "running", "queued", "pausing"].includes(applied.nextStatus)) {
        pollBatchJob(batchJobId);
      }
    } catch (e) {
      setBatchBusy(false);
      notify(`Failed to cancel Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  const resumeBatchJob = async () => {
    if (!batchJobId) return;
    setBatchBusy(true);
    try {
      const status = await api.post(`/players/fetch-top-ratings-batch/job/${batchJobId}/resume`, {});
      const applied = applyBatchStatus(status, batchJobId);
      if (["queued", "running", "pausing"].includes(applied.nextStatus)) {
        pollBatchJob(batchJobId);
      }
    } catch (e) {
      setBatchBusy(false);
      notify(`Failed to resume Top-X batch: ${e?.message || "unknown error"}`);
    }
  };

  // Re-attach to an in-flight batch (e.g. started by the nightly scheduler or a
  // previous session) so progress is visible here.
  useEffect(() => {
    let cancelled = false;
    const hydrateLatestJob = async () => {
      try {
        const latest = await api.get("/players/fetch-top-ratings-batch/latest");
        if (cancelled || !latest?.exists) return;
        if (latest?.status === "completed") return;
        const applied = applyBatchStatus(latest);
        if (["queued", "running", "pausing", "canceling"].includes(applied.nextStatus)) {
          pollBatchJob(applied.jobId);
        }
      } catch {
        /* progress panel is optional on startup */
      }
    };
    hydrateLatestJob();
    return () => {
      cancelled = true;
    };
  }, []);

  const progressPct = batchTotal > 0 ? Math.min(100, Math.max(0, (batchProcessed / batchTotal) * 100)) : 0;
  const showProgress = batchStatus !== "idle";
  const batchActive = ["queued", "running", "pausing", "canceling"].includes(batchStatus);
  const batchResumable = ["paused", "failed"].includes(batchStatus);
  const missingCount = (players || []).filter((player) => !playerHasCompleteTopRatings(player)).length;
  const statusLabel =
    {
      completed: "Completed",
      failed: "Failed",
      canceled: "Canceled",
      canceling: "Canceling",
      paused: "Paused",
      pausing: "Pausing",
      running: "Running",
      queued: "Queued",
    }[batchStatus] || "Queued";

  return (
    <Section title="Player Top-X Data">
      <div className="actions" style={{ marginTop: 0, alignItems: "center" }}>
        <label className="field" style={{ margin: 0 }}>
          <span>Timeframe {topxWindowBusy ? "(switching…)" : ""}</span>
          <select value={topxMonths} onChange={(e) => changeTopxWindow(e.target.value)} disabled={batchActive || topxWindowBusy}>
            {["3", "6", "12"].map((m) => (
              <option key={m} value={m}>
                Last {m} months{Number(topxCoverage[m] || 0) > 0 ? ` (${topxCoverage[m]} stored)` : " (none stored)"}
              </option>
            ))}
          </select>
        </label>
        <button className="primary" onClick={() => importAll(false)} disabled={(players || []).length === 0 || batchActive}>
          {batchActive ? `Importing ${batchTotal} players...` : `Import All (${(players || []).length})`}
        </button>
        <button
          className="secondary"
          onClick={importCurrentEvent}
          disabled={eventPlayerIds.length === 0 || batchActive}
          title="Import Top-X data only for players in the active event"
        >
          {batchActive ? "Importing..." : `Import Current Event (${eventPlayerIds.length})`}
        </button>
        <button className="secondary" onClick={() => importAll(true)} disabled={missingCount === 0 || batchActive}>
          {batchActive ? "Importing..." : `Import Missing (${missingCount})`}
        </button>
        {batchActive && batchJobId && (
          <button className="secondary" onClick={pauseBatchJob} disabled={batchStatus === "pausing"}>
            {batchStatus === "pausing" ? "Pausing..." : "Pause"}
          </button>
        )}
        {batchActive && batchJobId && (
          <button className="danger" onClick={cancelBatchJob} disabled={batchStatus === "canceling"}>
            {batchStatus === "canceling" ? "Canceling..." : "Cancel"}
          </button>
        )}
        {batchResumable && batchJobId && (
          <button className="secondary" onClick={resumeBatchJob} disabled={batchBusy}>
            Resume
          </button>
        )}
      </div>
      {showProgress && (
        <div className="card sub">
          <p className="muted">
            Top-X progress: {batchProcessed.toLocaleString()} / {batchTotal.toLocaleString()} | ok {batchOk} | failed{" "}
            {batchFailed}
            {batchActive && batchTotal > batchProcessed ? ` | ETA: ${formatBatchEta(batchEtaSeconds)}` : ""}
          </p>
          <div className="progress">
            <div className="progress-bar determinate" style={{ width: `${progressPct}%` }} />
          </div>
          <p className="muted">Status: {statusLabel}</p>
          {batchLastError && <p className="muted">Last error: {batchLastError}</p>}
        </div>
      )}
    </Section>
  );
}

function SchedulingTab({ notify, players, refresh, mapStats, teams = [] }) {
  const [status, setStatus] = useState(null);
  const [runs, setRuns] = useState([]);
  const [saveBusy, setSaveBusy] = useState(false);
  const [runBusy, setRunBusy] = useState(false);
  const [message, setMessage] = useState("");
  // Local edit state so typing a time doesn't fire a save per keystroke.
  const [runTime, setRunTime] = useState("00:00");
  const [lookbackDays, setLookbackDays] = useState(3);
  const pollRef = useRef(null);

  const TASK_LABELS = {
    events: "New fantasy events",
    rankings: "Team rankings (HLTV + VRS)",
    matches: "New matches played",
    ratings: "Player Top-X ratings",
  };

  const loadStatus = async () => {
    try {
      const data = await api.get("/schedule/status", 15000);
      setStatus(data);
      if (data?.config) {
        setRunTime(String(data.config.run_time || "00:00"));
        setLookbackDays(Number(data.config.matches_lookback_days || 3));
      }
    } catch (e) {
      setMessage(e?.message || "Backend not reachable.");
    }
  };
  const loadRuns = async () => {
    try {
      const data = await api.get("/schedule/runs?limit=50", 15000);
      setRuns(data.runs || []);
    } catch {
      /* runs table is non-critical; status error already surfaces */
    }
  };

  useEffect(() => {
    loadStatus();
    loadRuns();
    // Poll while the tab is open so live progress and run history stay fresh.
    pollRef.current = setInterval(() => {
      loadStatus();
      loadRuns();
    }, 5000);
    return () => clearInterval(pollRef.current);
  }, []);

  const config = status?.config || {};
  const state = status?.state || {};
  const lastByTask = status?.last_success_by_task || {};

  const patchConfig = async (patch) => {
    setSaveBusy(true);
    setMessage("");
    try {
      const data = await api.post("/schedule/config", patch);
      setStatus(data);
      notify("Schedule updated");
    } catch (e) {
      setMessage(e?.message || "Failed to update schedule.");
    } finally {
      setSaveBusy(false);
    }
  };

  const runNow = async (task) => {
    setRunBusy(true);
    setMessage("");
    try {
      const res = await api.post("/schedule/run-now", { task });
      if (res?.status === "busy") {
        setMessage(res.detail || "A run is already in progress.");
      } else if (res?.status === "error") {
        setMessage(res.detail || "Failed to start run.");
      } else {
        notify(`Started: ${(res.tasks || [task]).join(", ")}`);
      }
      loadStatus();
    } catch (e) {
      setMessage(e?.message || "Failed to start run.");
    } finally {
      setRunBusy(false);
    }
  };

  const fmtTs = (ts) => (Number(ts) > 0 ? new Date(Number(ts) * 1000).toLocaleString() : "Never");

  return (
    <div className="stack">
      <Section title="Nightly Data Refresh">
        <p className="muted">
          The always-on backend refreshes data automatically each night, so the Database pages don't need manual
          imports. Tasks run in sequence (they share one scraping browser). Requires the backend auto-start install
          (scripts\install-autostart.ps1) for refreshes to happen with the app closed.
        </p>
        <div className="grid three">
          <div className="field">
            <span>Scheduler</span>
            <label className="checkbox-inline">
              <input
                type="checkbox"
                checked={Boolean(config.enabled)}
                onChange={(e) => patchConfig({ enabled: e.target.checked })}
                disabled={saveBusy}
              />
              <span>{config.enabled ? "Enabled" : "Disabled"}</span>
            </label>
          </div>
          <div className="field">
            <span>Run time (daily)</span>
            <input
              type="time"
              value={runTime}
              onChange={(e) => setRunTime(e.target.value)}
              onBlur={() => patchConfig({ run_time: runTime })}
              disabled={saveBusy}
            />
          </div>
          <div className="field">
            <span>Next scheduled run</span>
            <div className="pill">{status?.next_run_at ? new Date(status.next_run_at * 1000).toLocaleString() : "-"}</div>
          </div>
        </div>
        {message && <p className="muted">{message}</p>}
      </Section>

      <Section title="Tasks">
        <table>
          <thead>
            <tr>
              <th>Task</th>
              <th>Enabled</th>
              <th>Last successful run</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {["events", "rankings", "matches", "ratings"].map((task) => {
              const flagKey = `do_${task}`;
              return (
                <tr key={task}>
                  <td>
                    {TASK_LABELS[task]}
                    {task === "events" && (
                      <div className="muted" style={{ fontSize: 12 }}>
                        Checks hltv.org/fantasy and imports any fantasy events not in the database; the active
                        event is never switched automatically.
                      </div>
                    )}
                    {task === "matches" && (
                      <div className="muted" style={{ fontSize: 12 }}>
                        Imports results back{" "}
                        <input
                          type="number"
                          min={1}
                          max={30}
                          value={lookbackDays}
                          onChange={(e) => setLookbackDays(Number(e.target.value))}
                          onBlur={() => patchConfig({ matches_lookback_days: lookbackDays })}
                          disabled={saveBusy}
                          style={{ width: 52 }}
                        />{" "}
                        day(s); already-imported matches are skipped.
                      </div>
                    )}
                    {task === "ratings" && (
                      <div className="muted" style={{ fontSize: 12 }}>
                        All players in the database, using the active timeframe window.
                      </div>
                    )}
                  </td>
                  <td>
                    <input
                      type="checkbox"
                      checked={Boolean(config[flagKey])}
                      onChange={(e) => patchConfig({ [flagKey]: e.target.checked })}
                      disabled={saveBusy}
                    />
                  </td>
                  <td>{fmtTs(lastByTask[task])}</td>
                  <td>
                    <button className="secondary" onClick={() => runNow(task)} disabled={runBusy || state.running}>
                      Run now
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="actions">
          <button className="primary" onClick={() => runNow("all")} disabled={runBusy || state.running}>
            {state.running ? "Refresh in progress..." : "Run All Now"}
          </button>
          {state.running && (
            <span className="muted">
              Running {TASK_LABELS[state.current_task] || state.current_task}
              {state.total > 0 ? ` — ${state.processed}/${state.total}` : ""}
              {state.trigger ? ` (${state.trigger})` : ""}
            </span>
          )}
        </div>
      </Section>

      <TopxImportPanel players={players} notify={notify} refresh={refresh} />

      <Section title="Matches Import">
        <MatchesDataPanel notify={notify} mode="import" />
      </Section>

      {mapStats && (
        <Section title="Map Stats Import">
          <p className="muted">
            Scrapes each team's HLTV map page (last 3 months). Pausable and resumable; safe to leave running.
          </p>
          <MapStatsJobControls job={mapStats} teamsAvailable={(teams || []).length > 0} />
          <MapStatsJobProgress job={mapStats} />
        </Section>
      )}

      <Section title="Run History">
        {runs.length === 0 && <p className="muted">No runs recorded yet.</p>}
        {runs.length > 0 && (
          <table>
            <thead>
              <tr>
                <th>Task</th>
                <th>Trigger</th>
                <th>Status</th>
                <th>Started</th>
                <th>Duration</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const dur =
                  run.finished_at && run.started_at
                    ? `${Math.max(0, Math.round(run.finished_at - run.started_at))}s`
                    : "-";
                return (
                  <tr key={run.id}>
                    <td>{TASK_LABELS[run.task] || run.task}</td>
                    <td>{run.trigger}</td>
                    <td>
                      <span className={run.status === "success" ? "pill" : "pill warn"}>{run.status}</span>
                    </td>
                    <td>{fmtTs(run.started_at)}</td>
                    <td>{dur}</td>
                    <td className="muted" style={{ maxWidth: 420, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {run.message || ""}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Section>
    </div>
  );
}

const RATING_LAB_TIERS = [
  { tier: 5, label: "Top 5" },
  { tier: 10, label: "Top 10" },
  { tier: 20, label: "Top 20" },
  { tier: 30, label: "Top 30" },
  { tier: 50, label: "Top 50" },
];
const TIER_COLORS = { 5: "#f97316", 10: "#eab308", 20: "#22d3ee", 30: "#a78bfa", 50: "#34d399" };

function RatingLabTab({ players }) {
  const [stats, setStats] = useState({
    rating: "1.10",
    rating_top5: "1.02",
    maps_top5: "40",
    rating_top10: "",
    maps_top10: "",
    rating_top20: "1.08",
    maps_top20: "25",
    rating_top30: "",
    maps_top30: "",
    rating_top50: "1.14",
    maps_top50: "60",
  });
  const [preview, setPreview] = useState(null);
  const [previewError, setPreviewError] = useState("");
  const [avg, setAvg] = useState(null);
  const [avgLoading, setAvgLoading] = useState(false);
  const [avgError, setAvgError] = useState("");
  const [loadPid, setLoadPid] = useState("");

  const setStat = (key, val) => setStats((prev) => ({ ...prev, [key]: val }));

  // Live preview: run the exact rating-curve system on the hand-entered stats.
  useEffect(() => {
    let cancel = false;
    const t = setTimeout(async () => {
      try {
        setPreviewError("");
        const data = await api.post("/players/rating-curve/preview", stats, 30000);
        if (!cancel) setPreview(data);
      } catch (e) {
        if (!cancel) {
          setPreview(null);
          setPreviewError(e?.message || "Preview failed.");
        }
      }
    }, 300);
    return () => {
      cancel = true;
      clearTimeout(t);
    };
  }, [stats]);

  const loadAverage = async () => {
    setAvgLoading(true);
    setAvgError("");
    try {
      const data = await api.get("/players/average-rating-curve", 60000);
      setAvg(data);
    } catch (e) {
      setAvgError(e?.message || "Failed to load average curve.");
    } finally {
      setAvgLoading(false);
    }
  };
  useEffect(() => {
    loadAverage();
  }, []);

  // Pull a real player's stored per-tier numbers into the form to inspect them.
  const loadFromPlayer = async () => {
    const pid = Number(loadPid);
    if (!Number.isFinite(pid) || pid <= 0) return;
    try {
      const p = await api.get(`/players/${pid}`);
      const next = { rating: p.rating != null ? String(p.rating) : "" };
      RATING_LAB_TIERS.forEach(({ tier }) => {
        next[`rating_top${tier}`] = p[`rating_top${tier}`] != null ? String(p[`rating_top${tier}`]) : "";
        next[`maps_top${tier}`] = p[`maps_top${tier}`] != null ? String(p[`maps_top${tier}`]) : "";
      });
      setStats(next);
    } catch {
      /* ignore */
    }
  };

  const predictedRows = useMemo(() => {
    const rows = Array.isArray(preview?.predicted_curve) ? preview.predicted_curve : [];
    return rows.map((r) => ({ rank: Number(r.rank), rating: r.rating == null ? null : Number(r.rating) }));
  }, [preview]);
  const anchorRows = useMemo(() => {
    const rows = Array.isArray(preview?.graph_rows) ? preview.graph_rows : [];
    return rows.map((r) => ({ rank: Number(r.rank), rating: Number(r.bucket_rating) }));
  }, [preview]);
  const predictedAxis = useMemo(
    () => buildNiceStepAxis([...predictedRows.map((r) => r.rating), ...anchorRows.map((r) => r.rating)].filter((v) => Number.isFinite(v)), 0.02),
    [predictedRows, anchorRows]
  );

  // Average population view: scatter every per-tier delta, one series per tier.
  const scatterByTier = useMemo(() => {
    const pts = Array.isArray(avg?.points) ? avg.points : [];
    const grouped = {};
    RATING_LAB_TIERS.forEach(({ tier }) => (grouped[tier] = []));
    pts.forEach((p) => {
      const tier = Number(p.tier);
      if (!grouped[tier]) grouped[tier] = [];
      // percentage deviation vs overall (fraction → percent for display)
      grouped[tier].push({ rank: Number(p.rank_midpoint), pct: Number(p.pct) * 100, name: p.name, maps: p.maps });
    });
    return grouped;
  }, [avg]);
  const avgLineRows = useMemo(() => {
    const rows = Array.isArray(avg?.average_curve) ? avg.average_curve : [];
    return rows.map((r) => ({ rank: Number(r.rank), pct: Number(r.pct) * 100 })).sort((a, b) => a.rank - b.rank);
  }, [avg]);

  return (
    <Section title="Rating Lab — Top-X Curve Diagnostics">
      <div className="stack">
        <div className="card sub">
          <h3>Example Player</h3>
          <p className="muted">
            Enter an overall rating and any cumulative "rating vs Top-N" values (with maps played) to see exactly the
            rank-adjusted curve the match engine would use. Leave the tier fields blank to see the average-curve
            fallback the system applies when a player has no Top-X data.
          </p>
          <div className="grid two">
            <Input label="Overall rating" value={stats.rating} onChange={(v) => setStat("rating", v)} placeholder="1.10" />
            <div className="field">
              <span>Load a real player's numbers</span>
              <div className="actions" style={{ margin: 0 }}>
                <input
                  value={loadPid}
                  onChange={(e) => setLoadPid(e.target.value)}
                  placeholder="player id"
                  style={{ maxWidth: 120 }}
                />
                <button className="secondary" onClick={loadFromPlayer} disabled={!loadPid}>
                  Load
                </button>
              </div>
            </div>
          </div>
          <table>
            <thead>
              <tr>
                <th>Tier</th>
                <th>Rating vs Top-N</th>
                <th>Maps vs Top-N</th>
              </tr>
            </thead>
            <tbody>
              {RATING_LAB_TIERS.map(({ tier, label }) => (
                <tr key={`in-${tier}`}>
                  <td style={{ color: TIER_COLORS[tier], fontWeight: 700 }}>{label}</td>
                  <td>
                    <input
                      value={stats[`rating_top${tier}`]}
                      onChange={(e) => setStat(`rating_top${tier}`, e.target.value)}
                      placeholder="—"
                      style={{ maxWidth: 120 }}
                    />
                  </td>
                  <td>
                    <input
                      value={stats[`maps_top${tier}`]}
                      onChange={(e) => setStat(`maps_top${tier}`, e.target.value)}
                      placeholder="—"
                      style={{ maxWidth: 120 }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {previewError && <p className="muted">{previewError}</p>}
          {preview && (
            <>
              <p className="muted">
                {preview.used_average_fallback
                  ? "No per-tier data → using the average degradation curve on top of the overall rating."
                  : "Rank-adjusted from the entered Top-X buckets (with sample shrinkage toward the overall rating)."}
              </p>
              <div className="value-chart-wrap">
                <ResponsiveContainer width="100%" height={340}>
                  <ComposedChart data={predictedRows} margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                    <CartesianGrid stroke="#232a34" strokeDasharray="3 3" />
                    <XAxis
                      type="number"
                      dataKey="rank"
                      domain={[1, 50]}
                      ticks={[1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]}
                      interval={0}
                      tick={{ fill: "#9fb2c9", fontSize: 12 }}
                      axisLine={{ stroke: "#3a4452" }}
                      tickLine={{ stroke: "#3a4452" }}
                      label={{ value: "Opponent HLTV rank", position: "insideBottom", offset: -4, fill: "#7f97bd", fontSize: 11 }}
                    />
                    <YAxis
                      tick={{ fill: "#9fb2c9", fontSize: 12 }}
                      axisLine={{ stroke: "#3a4452" }}
                      tickLine={{ stroke: "#3a4452" }}
                      domain={predictedAxis.domain}
                      ticks={predictedAxis.ticks}
                      tickFormatter={(v) => Number(v).toFixed(2)}
                    />
                    <Tooltip
                      contentStyle={{ background: "#14181f", border: "1px solid #3a4452", borderRadius: 10, color: "#e9edf3" }}
                      formatter={(value, name) => [value == null ? "—" : Number(value).toFixed(3), name]}
                      labelFormatter={(v) => `Rank ${v}`}
                    />
                    <Legend wrapperStyle={{ color: "#9fb2c9" }} />
                    <Line
                      type="linear"
                      data={predictedRows}
                      dataKey="rating"
                      name="Predicted rating"
                      stroke="#22d3ee"
                      strokeWidth={2.2}
                      dot={false}
                      connectNulls={false}
                      isAnimationActive={false}
                    />
                    <Scatter data={anchorRows} dataKey="rating" name="Tier anchors" fill="#f97316" isAnimationActive={false} />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Bucket</th>
                    <th>Raw rating</th>
                    <th>Adjusted rating</th>
                    <th>Raw delta</th>
                    <th>Shrunk delta</th>
                    <th>Sample weight</th>
                    <th>Maps</th>
                  </tr>
                </thead>
                <tbody>
                  {(preview.bucket_rows || [])
                    .filter((r) => Number(r.tier) > 0 && Number(r.tier) < 100)
                    .map((r) => (
                      <tr key={`br-${r.tier}`} style={r.estimated ? { opacity: 0.6 } : undefined}>
                        <td style={{ color: TIER_COLORS[Number(r.tier)], fontWeight: 700 }}>
                          {r.tier_label}
                          {r.estimated && <span className="muted" style={{ fontWeight: 400 }}> (est.)</span>}
                        </td>
                        <td>{Number(r.raw_bucket_rating).toFixed(3)}</td>
                        <td>{Number(r.bucket_rating).toFixed(3)}</td>
                        <td>
                          {Number(r.raw_bucket_delta) >= 0 ? "+" : ""}
                          {Number(r.raw_bucket_delta).toFixed(3)}
                        </td>
                        <td>
                          {Number(r.bucket_delta) >= 0 ? "+" : ""}
                          {Number(r.bucket_delta).toFixed(3)}
                        </td>
                        <td>{Math.round(Number(r.shrinkage_weight) * 100)}%</td>
                        <td>{Math.round(Number(r.maps || 0))}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </>
          )}
        </div>

        <div className="card sub">
          <h3>Average Player Top-X (whole pool)</h3>
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="secondary" onClick={loadAverage} disabled={avgLoading}>
              {avgLoading ? "Loading..." : "Refresh"}
            </button>
            {avg && (
              <span className="muted">
                {avg.sampled_players} players · {avg.point_count} tier data points
              </span>
            )}
          </div>
          <p className="muted">
            Every player's per-tier deviation as a <strong>percentage of their own overall rating</strong> (so a 1.30 and
            a 0.90 player are comparable), colored by tier, with the maps-weighted average curve the model shrinks toward.
            The prior for any player is <code>overall × (1 + this %)</code>. Wide spread or a thin point count in a tier
            means the Top-X import there is noisy or sparse.
          </p>
          {avgError && <p className="muted">{avgError}</p>}
          {avg && (
            <>
              <div className="value-chart-wrap">
                <ResponsiveContainer width="100%" height={360}>
                  <ComposedChart margin={{ top: 12, right: 18, left: 6, bottom: 12 }}>
                    <CartesianGrid stroke="#232a34" strokeDasharray="3 3" />
                    <XAxis
                      type="number"
                      dataKey="rank"
                      domain={[1, 50]}
                      ticks={[1, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50]}
                      interval={0}
                      tick={{ fill: "#9fb2c9", fontSize: 12 }}
                      axisLine={{ stroke: "#3a4452" }}
                      tickLine={{ stroke: "#3a4452" }}
                      label={{ value: "Opponent HLTV rank (tier midpoint)", position: "insideBottom", offset: -4, fill: "#7f97bd", fontSize: 11 }}
                    />
                    <YAxis
                      type="number"
                      dataKey="pct"
                      tick={{ fill: "#9fb2c9", fontSize: 12 }}
                      axisLine={{ stroke: "#3a4452" }}
                      tickLine={{ stroke: "#3a4452" }}
                      tickFormatter={(v) => `${Number(v).toFixed(0)}%`}
                      label={{ value: "% deviation vs overall", angle: -90, position: "insideLeft", fill: "#7f97bd", fontSize: 11 }}
                    />
                    <Tooltip
                      cursor={{ stroke: "#3a4452", strokeDasharray: "3 3" }}
                      content={({ active, payload }) => {
                        if (!active || !payload || payload.length === 0) return null;
                        // Every dot in a tier shares one x (the tier midpoint), so
                        // resolve the hovered column to its tier summary instead of
                        // dumping the whole overlapping column of players.
                        const x = Number(payload[0]?.payload?.rank);
                        const tier = (avg?.tiers || []).find((t) => Math.abs(Number(t.rank_midpoint) - x) < 0.6);
                        if (!tier) return null;
                        const pct = tier.pct;
                        return (
                          <div
                            style={{
                              background: "#14181f",
                              border: "1px solid #3a4452",
                              borderRadius: 10,
                              color: "#e9edf3",
                              padding: "8px 11px",
                              fontSize: 13,
                              lineHeight: 1.6,
                            }}
                          >
                            <div style={{ fontWeight: 700, color: TIER_COLORS[tier.tier] }}>{tier.tier_label}</div>
                            <div>
                              Avg deviation:{" "}
                              <strong>{pct == null ? "—" : `${pct >= 0 ? "+" : ""}${(pct * 100).toFixed(1)}%`}</strong>
                            </div>
                            <div style={{ color: "#7f97bd" }}>{tier.count} players</div>
                          </div>
                        );
                      }}
                    />
                    <Legend wrapperStyle={{ color: "#9fb2c9" }} />
                    {RATING_LAB_TIERS.map(({ tier, label }) => (
                      <Scatter
                        key={`sc-${tier}`}
                        data={scatterByTier[tier] || []}
                        dataKey="pct"
                        name={label}
                        fill={TIER_COLORS[tier]}
                        fillOpacity={0.5}
                        isAnimationActive={false}
                      />
                    ))}
                    <Line
                      type="linear"
                      data={avgLineRows}
                      dataKey="pct"
                      name="Average"
                      stroke="#f8fafc"
                      strokeWidth={2.4}
                      dot={{ r: 4, fill: "#f8fafc" }}
                      isAnimationActive={false}
                    />
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
              <table>
                <thead>
                  <tr>
                    <th>Tier</th>
                    <th>Data points</th>
                    <th>Avg % (maps-weighted)</th>
                    <th>Mean %</th>
                    <th>Median %</th>
                    <th>Prior % used</th>
                  </tr>
                </thead>
                <tbody>
                  {(avg.tiers || []).map((t) => {
                    const fmt = (v) => (v == null ? "—" : `${v >= 0 ? "+" : ""}${(v * 100).toFixed(1)}%`);
                    return (
                      <tr key={`avt-${t.tier}`}>
                        <td style={{ color: TIER_COLORS[t.tier], fontWeight: 700 }}>{t.tier_label}</td>
                        <td>{t.count}</td>
                        <td>{fmt(t.weighted_mean_pct)}</td>
                        <td>{fmt(t.mean_pct)}</td>
                        <td>{fmt(t.median_pct)}</td>
                        <td>{fmt(t.pct)}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}
        </div>
      </div>
    </Section>
  );
}

function ModelLabTab() {
  const [trainLimit, setTrainLimit] = useState("0");
  const [testLimit, setTestLimit] = useState("0");
  const [randomSplit, setRandomSplit] = useState(false);
  const [dbMatchCount, setDbMatchCount] = useState(0);
  const [histCoverage, setHistCoverage] = useState(null);
  const [histJobStatus, setHistJobStatus] = useState("idle");
  const [histJobId, setHistJobId] = useState("");
  const [histJobProcessed, setHistJobProcessed] = useState(0);
  const [histJobTotal, setHistJobTotal] = useState(0);
  const [histJobOk, setHistJobOk] = useState(0);
  const [histJobFailed, setHistJobFailed] = useState(0);
  const [histJobCurrent, setHistJobCurrent] = useState("");
  const [histJobLastError, setHistJobLastError] = useState("");
  const [histJobEtaSeconds, setHistJobEtaSeconds] = useState(null);
  const histJobPollingRef = useRef(false);

  const loadHistCoverage = async () => {
    try {
      const cov = await api.get("/events/hltv-results/historical-map-stats/coverage");
      if (cov && cov.status === "ok") setHistCoverage(cov);
    } catch {
      // Coverage is informational; the lab still works without it.
    }
  };

  const [vetoCoverage, setVetoCoverage] = useState(null);
  const vetoJob = useBackfillJob("/events/hltv-results/veto-backfill", "veto backfill");
  const loadVetoCoverage = async () => {
    try {
      const cov = await api.get("/events/hltv-results/veto-backfill/coverage");
      if (cov && cov.status === "ok") setVetoCoverage(cov);
    } catch {
      // Coverage is informational; the lab still works without it.
    }
  };
  vetoJob.onSettledRef.current = loadVetoCoverage;

  const [mapSbCoverage, setMapSbCoverage] = useState(null);
  const mapSbJob = useBackfillJob("/events/hltv-results/map-scoreboards", "map scoreboards backfill");
  const loadMapSbCoverage = async () => {
    try {
      const cov = await api.get("/events/hltv-results/map-scoreboards/coverage");
      if (cov && cov.status === "ok") setMapSbCoverage(cov);
    } catch {
      // Coverage is informational; the lab still works without it.
    }
  };
  mapSbJob.onSettledRef.current = loadMapSbCoverage;

  const applyHistJobStatus = (status, jobIdOverride = "") => {
    const jobId = String(jobIdOverride || status?.job_id || "");
    const nextStatus = String(status?.status || "queued");
    const lastError = String(status?.last_error || status?.error || "");
    const processed = Number(status?.processed_items || 0);
    const total = Number(status?.total_items || 0);
    setHistJobStatus(nextStatus);
    setHistJobId(jobId);
    setHistJobProcessed(processed);
    setHistJobTotal(total);
    setHistJobOk(Number(status?.ok || 0));
    setHistJobFailed(Number(status?.failed || 0));
    setHistJobCurrent(String(status?.current_item || ""));
    setHistJobLastError(lastError);
    const startedAtMs = getBatchStartedAtMs(status);
    if (processed > 0 && total > processed && ["queued", "running", "pausing", "canceling"].includes(nextStatus)) {
      const elapsedSeconds = Math.max(1, (Date.now() - startedAtMs) / 1000);
      const rate = processed / elapsedSeconds;
      setHistJobEtaSeconds(rate > 0 ? (total - processed) / rate : null);
    } else {
      setHistJobEtaSeconds(null);
    }
    return { jobId, nextStatus, lastError };
  };

  const pollHistJob = async (jobId) => {
    if (!jobId || histJobPollingRef.current) return;
    histJobPollingRef.current = true;
    try {
      let done = false;
      let pollFailures = 0;
      while (!done) {
        let status;
        try {
          status = await api.get(`/events/hltv-results/historical-map-stats/job/${jobId}`, 60000);
          pollFailures = 0;
        } catch (pollError) {
          // The job keeps running server-side; only give up after repeated failures.
          pollFailures += 1;
          if (pollFailures >= 5) throw pollError;
          await new Promise((resolve) => setTimeout(resolve, 3000));
          continue;
        }
        const applied = applyHistJobStatus(status, jobId);
        if (["completed", "failed", "paused", "canceled"].includes(applied.nextStatus)) {
          await loadHistCoverage();
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 1500));
      }
    } catch (e) {
      setHistJobStatus("failed");
      setHistJobLastError(String(e?.message || "Failed to poll historical map stats job."));
    } finally {
      histJobPollingRef.current = false;
    }
  };

  const startHistJob = async () => {
    setHistJobStatus("queued");
    setHistJobProcessed(0);
    setHistJobTotal(0);
    setHistJobOk(0);
    setHistJobFailed(0);
    setHistJobLastError("");
    setHistJobCurrent("");
    try {
      const start = await api.post("/events/hltv-results/historical-map-stats/start", {});
      const jobId = String(start?.job_id || "");
      if (!jobId) throw new Error("Failed to start historical map stats job.");
      setHistJobId(jobId);
      await pollHistJob(jobId);
    } catch (e) {
      setHistJobStatus("failed");
      setHistJobLastError(String(e?.message || "Failed to start historical map stats job."));
    }
  };

  const pauseHistJob = async () => {
    if (!histJobId) return;
    setHistJobStatus("pausing");
    try {
      const status = await api.post(`/events/hltv-results/historical-map-stats/job/${histJobId}/pause`, {});
      const applied = applyHistJobStatus(status, histJobId);
      if (["pausing", "running", "queued"].includes(applied.nextStatus)) pollHistJob(histJobId);
    } catch (e) {
      setHistJobLastError(String(e?.message || "Failed to pause historical map stats job."));
    }
  };

  const cancelHistJob = async () => {
    if (!histJobId) return;
    setHistJobStatus("canceling");
    try {
      const status = await api.post(`/events/hltv-results/historical-map-stats/job/${histJobId}/cancel`, {});
      const applied = applyHistJobStatus(status, histJobId);
      if (["canceling", "running", "queued", "pausing"].includes(applied.nextStatus)) pollHistJob(histJobId);
    } catch (e) {
      setHistJobLastError(String(e?.message || "Failed to cancel historical map stats job."));
    }
  };

  const resumeHistJob = async () => {
    if (!histJobId) return;
    try {
      const status = await api.post(`/events/hltv-results/historical-map-stats/job/${histJobId}/resume`, {});
      const applied = applyHistJobStatus(status, histJobId);
      if (["queued", "running", "pausing"].includes(applied.nextStatus)) pollHistJob(histJobId);
    } catch (e) {
      setHistJobLastError(String(e?.message || "Failed to resume historical map stats job."));
    }
  };

  useEffect(() => {
    let cancelled = false;
    loadHistCoverage();
    const hydrateHistJob = async () => {
      try {
        const latest = await api.get("/events/hltv-results/historical-map-stats/latest");
        if (cancelled || !latest?.exists) return;
        if (["completed", "canceled"].includes(String(latest?.status || ""))) return;
        const applied = applyHistJobStatus(latest);
        if (["queued", "running", "pausing", "canceling"].includes(applied.nextStatus)) {
          pollHistJob(applied.jobId);
        }
      } catch {
        // Optional panel; ignore startup failures.
      }
    };
    hydrateHistJob();
    loadVetoCoverage();
    vetoJob.hydrate();
    loadMapSbCoverage();
    mapSbJob.hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const histJobActive = ["queued", "running", "pausing", "canceling"].includes(histJobStatus);
  const histJobResumable = ["paused", "failed"].includes(histJobStatus);
  const histJobPct = histJobTotal > 0 ? Math.min(100, Math.max(0, (histJobProcessed / histJobTotal) * 100)) : 0;
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
        fetch_missing_map_stats: "false",
      });
      // Training scales with dataset size (~40s at 1,600 matches); give it far
      // more than the 30s default before declaring the backend unresponsive.
      const data = await api.get(`/events/hltv-results/map-model-lab?${params.toString()}`, 600000);
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
          <button className="primary" onClick={run} disabled={busy}>
            {busy ? "Running..." : "Train & Evaluate"}
          </button>
        </div>
        <div className="card sub">
          <h3>Historical Map Stats</h3>
          <p className="muted">
            The map-data model only uses maps where both teams have pre-match six-month map stats stored in the
            database. This job scrapes and permanently stores each missing team/date window; it is safe to pause,
            cancel, and resume across sessions, and already-stored windows are always skipped.
          </p>
          {histCoverage && (
            <p className="muted">
              Coverage: {Number(histCoverage.cached_windows || 0).toLocaleString()} of{" "}
              {Number(histCoverage.required_windows || 0).toLocaleString()} team-windows stored |{" "}
              {Number(histCoverage.missing_windows || 0).toLocaleString()} missing
              {Number(histCoverage.unmapped_team_keys || 0) > 0
                ? ` | ${histCoverage.unmapped_team_keys} team names not in the team DB`
                : ""}
            </p>
          )}
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="secondary" onClick={startHistJob} disabled={histJobActive}>
              {histJobActive ? `Fetching ${histJobProcessed}/${histJobTotal}` : "Fetch Missing Historical Stats"}
            </button>
            {histJobActive && histJobId && (
              <button className="secondary" onClick={pauseHistJob} disabled={["pausing", "canceling"].includes(histJobStatus)}>
                {histJobStatus === "pausing" ? "Pausing..." : "Pause"}
              </button>
            )}
            {histJobResumable && histJobId && (
              <button className="secondary" onClick={resumeHistJob}>
                Resume
              </button>
            )}
            {(histJobActive || histJobResumable) && histJobId && (
              <button className="danger" onClick={cancelHistJob} disabled={histJobStatus === "canceling"}>
                {histJobStatus === "canceling" ? "Canceling..." : "Cancel"}
              </button>
            )}
          </div>
          {histJobStatus !== "idle" && histJobStatus !== "completed" && (
            <>
              <p className="muted">
                Progress: {histJobProcessed.toLocaleString()} / {histJobTotal.toLocaleString()} | ok {histJobOk} | failed{" "}
                {histJobFailed}
                {histJobActive && histJobTotal > histJobProcessed ? ` | ETA: ${formatBatchEta(histJobEtaSeconds)}` : ""}
              </p>
              <div className="progress">
                <div className="progress-bar determinate" style={{ width: `${histJobPct}%` }} />
              </div>
              {histJobCurrent && <p className="muted">Current: {histJobCurrent}</p>}
              {histJobLastError && <p className="muted">Last error: {histJobLastError}</p>}
            </>
          )}
          {histJobStatus === "completed" && (
            <p className="muted">Backfill complete: {histJobOk} fetched, {histJobFailed} failed.</p>
          )}
        </div>
        <div className="card sub">
          <h3>Match Veto Backfill</h3>
          <p className="muted">
            Fetches each stored match page to fill in the map veto (who picked each map) plus any missing per-map
            scores and player stats. The picked-map feature and veto-simulated series predictions need this data.
            Safe to pause, cancel, and resume across sessions; matches already checked are always skipped.
          </p>
          {vetoCoverage && (
            <p className="muted">
              Coverage: {Number(vetoCoverage.with_veto || 0).toLocaleString()} of{" "}
              {Number(vetoCoverage.total_matches || 0).toLocaleString()} matches have veto data |{" "}
              {Number(vetoCoverage.missing_veto || 0).toLocaleString()} missing
            </p>
          )}
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="secondary" onClick={vetoJob.start} disabled={vetoJob.active}>
              {vetoJob.active ? `Fetching ${vetoJob.processed}/${vetoJob.total}` : "Fetch Missing Vetoes"}
            </button>
            {vetoJob.active && vetoJob.jobId && (
              <button className="secondary" onClick={vetoJob.pause} disabled={["pausing", "canceling"].includes(vetoJob.status)}>
                {vetoJob.status === "pausing" ? "Pausing..." : "Pause"}
              </button>
            )}
            {vetoJob.resumable && vetoJob.jobId && (
              <button className="secondary" onClick={vetoJob.resume}>
                Resume
              </button>
            )}
            {(vetoJob.active || vetoJob.resumable) && vetoJob.jobId && (
              <button className="danger" onClick={vetoJob.cancel} disabled={vetoJob.status === "canceling"}>
                {vetoJob.status === "canceling" ? "Canceling..." : "Cancel"}
              </button>
            )}
          </div>
          {vetoJob.status !== "idle" && vetoJob.status !== "completed" && (
            <>
              <p className="muted">
                Progress: {vetoJob.processed.toLocaleString()} / {vetoJob.total.toLocaleString()} | ok {vetoJob.ok} |{" "}
                failed {vetoJob.failed}
                {vetoJob.active && vetoJob.total > vetoJob.processed ? ` | ETA: ${formatBatchEta(vetoJob.etaSeconds)}` : ""}
              </p>
              <div className="progress">
                <div className="progress-bar determinate" style={{ width: `${vetoJob.pctDone}%` }} />
              </div>
              {vetoJob.current && <p className="muted">Current: {vetoJob.current}</p>}
              {vetoJob.lastError && <p className="muted">Last error: {vetoJob.lastError}</p>}
            </>
          )}
          {vetoJob.status === "completed" && (
            <p className="muted">Backfill complete: {vetoJob.ok} fetched, {vetoJob.failed} failed.</p>
          )}
        </div>
        <div className="card sub">
          <h3>Per-Map Scoreboard Backfill</h3>
          <p className="muted">
            Fills each stored match's per-map player scoreboards (the map tabs in the match view). Most matches
            are re-parsed from the archived page copy with no scraping; only matches with no archived page are
            fetched live. Safe to pause, cancel, and resume; already-scanned matches are always skipped.
          </p>
          {mapSbCoverage && (
            <p className="muted">
              Coverage: {Number(mapSbCoverage.with_map_scoreboards || 0).toLocaleString()} of{" "}
              {Number(mapSbCoverage.total_matches || 0).toLocaleString()} matches scanned |{" "}
              {Number(mapSbCoverage.missing_map_scoreboards || 0).toLocaleString()} missing
            </p>
          )}
          <div className="actions" style={{ marginTop: 0 }}>
            <button className="secondary" onClick={mapSbJob.start} disabled={mapSbJob.active}>
              {mapSbJob.active ? `Scanning ${mapSbJob.processed}/${mapSbJob.total}` : "Fetch Map Scoreboards"}
            </button>
            {mapSbJob.active && mapSbJob.jobId && (
              <button className="secondary" onClick={mapSbJob.pause} disabled={["pausing", "canceling"].includes(mapSbJob.status)}>
                {mapSbJob.status === "pausing" ? "Pausing..." : "Pause"}
              </button>
            )}
            {mapSbJob.resumable && mapSbJob.jobId && (
              <button className="secondary" onClick={mapSbJob.resume}>
                Resume
              </button>
            )}
            {(mapSbJob.active || mapSbJob.resumable) && mapSbJob.jobId && (
              <button className="danger" onClick={mapSbJob.cancel} disabled={mapSbJob.status === "canceling"}>
                {mapSbJob.status === "canceling" ? "Canceling..." : "Cancel"}
              </button>
            )}
          </div>
          {mapSbJob.status !== "idle" && mapSbJob.status !== "completed" && (
            <>
              <p className="muted">
                Progress: {mapSbJob.processed.toLocaleString()} / {mapSbJob.total.toLocaleString()} | ok {mapSbJob.ok} |{" "}
                failed {mapSbJob.failed}
                {mapSbJob.active && mapSbJob.total > mapSbJob.processed ? ` | ETA: ${formatBatchEta(mapSbJob.etaSeconds)}` : ""}
              </p>
              <div className="progress">
                <div className="progress-bar determinate" style={{ width: `${mapSbJob.pctDone}%` }} />
              </div>
              {mapSbJob.current && <p className="muted">Current: {mapSbJob.current}</p>}
              {mapSbJob.lastError && <p className="muted">Last error: {mapSbJob.lastError}</p>}
            </>
          )}
          {mapSbJob.status === "completed" && (
            <p className="muted">Backfill complete: {mapSbJob.ok} scanned, {mapSbJob.failed} failed.</p>
          )}
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
                <h3>Model Comparison</h3>
                {(() => {
                  const wm = result.metrics || {};
                  const ro = result.rank_only_metrics || {};
                  const rows = [
                    { label: "Map winner", a: wm.winner_accuracy, b: ro.winner_accuracy, fmt: (v) => pct(v, 1), higherBetter: true },
                    { label: "Map Brier", a: wm.brier, b: ro.brier, fmt: (v) => Number(v).toFixed(3), higherBetter: false },
                    { label: "Score MAE", a: wm.score_mae, b: ro.score_mae, fmt: (v) => Number(v).toFixed(2), higherBetter: false },
                    { label: `Series winner (n ${Number(wm.n_series || 0).toLocaleString()})`, a: wm.series_winner_accuracy, b: ro.series_winner_accuracy, fmt: (v) => pct(v, 1), higherBetter: true },
                    { label: "Series Brier", a: wm.series_brier, b: ro.series_brier, fmt: (v) => Number(v).toFixed(3), higherBetter: false },
                    { label: `Veto-sim winner (n ${Number(wm.veto_sim?.n || 0).toLocaleString()})`, a: wm.veto_sim?.winner_accuracy, b: ro.veto_sim?.winner_accuracy, fmt: (v) => pct(v, 1), higherBetter: true },
                    { label: "Veto-sim Brier", a: wm.veto_sim?.brier, b: ro.veto_sim?.brier, fmt: (v) => Number(v).toFixed(3), higherBetter: false },
                    { label: "Veto maps matched", a: wm.veto_sim?.map_match_rate, b: ro.veto_sim?.map_match_rate, fmt: (v) => pct(v, 1), higherBetter: true },
                  ].filter((row) => row.a != null && row.b != null);
                  return (
                    <table>
                      <thead>
                        <tr>
                          <th>Metric</th>
                          <th>With Map Data</th>
                          <th>Rank Only</th>
                        </tr>
                      </thead>
                      <tbody>
                        {rows.map((row) => {
                          const aWins = row.higherBetter ? Number(row.a) > Number(row.b) : Number(row.a) < Number(row.b);
                          const bWins = row.higherBetter ? Number(row.b) > Number(row.a) : Number(row.b) < Number(row.a);
                          return (
                            <tr key={row.label}>
                              <td>{row.label}</td>
                              <td>{aWins ? <strong>{row.fmt(row.a)}</strong> : row.fmt(row.a)}</td>
                              <td>{bWins ? <strong>{row.fmt(row.b)}</strong> : row.fmt(row.b)}</td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  );
                })()}
                <p className="muted">
                  Bold = better. Historical maps kept {pct(result.input_summary?.train?.map_stats_coverage, 1)} (
                  {Number(result.input_summary?.train?.maps || 0).toLocaleString()} /{" "}
                  {Number(result.input_summary?.train?.candidate_maps || 0).toLocaleString()})
                </p>
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
                    <td>{formatDMY(row.match_date)}</td>
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
  // Structured swiss context from the event page: official seed order + Bo
  // mode detected from the stage's format rules.
  const [eventSwiss, setEventSwiss] = useState(null);
  useEffect(() => {
    if (!selectedEventId) {
      setEventSwiss(null);
      return undefined;
    }
    let cancelled = false;
    api
      .get(`/events/${selectedEventId}/swiss-context`, 60000)
      .then((d) => {
        if (cancelled || d?.status !== "ok") {
          if (!cancelled) setEventSwiss(null);
          return;
        }
        setEventSwiss(d);
        // Sensible default: with no manual selection yet, take the stage roster.
        setSelectedTeamIds((prev) => (prev.length === 0 ? (d.team_ids || []).map(Number) : prev));
      })
      .catch(() => {
        if (!cancelled) setEventSwiss(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedEventId]);

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

  const loadEventsForSwiss = async (retriesLeft = 3) => {
    let data = null;
    try {
      data = await api.get("/events/");
    } catch (e) {
      if (retriesLeft > 0) setTimeout(() => loadEventsForSwiss(retriesLeft - 1), 5000);
      return;
    }
    if (data?.detail) return;

    const allEvents = Array.isArray(data.events) ? data.events : [];
    setEvents(allEvents);

    const active = data.active_event_id;
    setSelectedEventId(active == null ? "" : String(active));
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

  const selectedEventMeta = useMemo(
    () => events.find((event) => String(event.event_id) === String(selectedEventId)) || null,
    [events, selectedEventId]
  );

  useEffect(() => {
    // While the event's team list is still loading, filteredTeams is empty —
    // filtering then would wipe selections and stored sim results spuriously.
    if (filteredTeams.length === 0) return;
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
        <button className={swissTab === "hltv" ? "tab active" : "tab"} onClick={() => setSwissTab("hltv")}>
          HLTV Replica
        </button>
      </div>
      {swissTab === "group" && (
        <GroupStageTab
          teams={filteredTeams}
          teamLookup={teamLookup}
          selected={selectedTeamIds}
          setSelected={setSelectedTeamIds}
          bo={eventSwiss?.bo3_mode || "elim_qual"}
          setBo={setBoMode}
          eventSeeds={eventSwiss?.seed_by_team_id || null}
          eventSwissInfo={eventSwiss}
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
          bo={eventSwiss?.bo3_mode || "elim_qual"}
          sims={simCount}
          results={simResults}
          onOpenPlayer={onOpenPlayer}
        />
      )}
      {swissTab === "value" && <SwissPlayerValueTab results={simResults} players={players} />}
      {swissTab === "single" && <BracketTab teams={filteredTeams} teamLookup={teamLookup} />}
      {swissTab === "hltv" && (
        <HltvReplicaSimulatorPanel
          eventId={selectedEventId}
          hltvEventId={selectedEventMeta?.hltv_event_id}
          hltvEventUrl={selectedEventMeta?.hltv_event_url}
        />
      )}
    </div>
  );
}

function GroupsTab({ teams, teamLookup, players, refresh, groupVariant = null }) {
  const [groupsTab, setGroupsTab] = useState("stage");
  const [events, setEvents] = useState([]);
  const [selectedEventId, setSelectedEventId] = useState("");
  const [eventTeamNames, setEventTeamNames] = useState(new Set());
  const [groupCount, setGroupCount] = useState(2);
  const [groupFormat, setGroupFormat] = useState("gsl4");
  const seedsPerGroup = groupFormat.startsWith("de8") ? 8 : 4;
  const qualsPerGroup = { gsl4: 2, de8: 4, de8_top3: 3 }[groupFormat] || 2;
  const [combinedPlayoffs, setCombinedPlayoffs] = useState(false);
  const [playoffSims, setPlayoffSims] = useState("2000");
  const [playoffStopTeams, setPlayoffStopTeams] = useState("1");
  const [slots, setSlots] = useState(Array(8).fill(""));
  const [results, setResults] = useState(null);
  const [updatedAt, setUpdatedAt] = useState("");
  const [busy, setBusy] = useState(false);
  const [runMessage, setRunMessage] = useState("");
  const [runProgress, setRunProgress] = useState({ done: 0, total: 0 });
  const [autofillBusy, setAutofillBusy] = useState(false);
  const [autofillMessage, setAutofillMessage] = useState("");
  const [comboMode, setComboMode] = useState("average");
  const [comboSearch, setComboSearch] = useState("");
  const [sortKey, setSortKey] = useState("ev_desc");
  const [topTeams, setTopTeams] = useState(null);
  const [allTeams, setAllTeams] = useState(null);
  const [filteredCount, setFilteredCount] = useState(0);
  const [page, setPage] = useState(0);
  const [combosReady, setCombosReady] = useState(false);
  const [liveMode, setLiveMode] = useState(false);
  const [combosUpdatedAt, setCombosUpdatedAt] = useState("");
  const [poolReduction, setPoolReduction] = useState(null);
  const [comboProgress, setComboProgress] = useState({ done: 0, total: 0 });
  const [topMessage, setTopMessage] = useState("");
  const [completedPicks, setCompletedPicks] = useState({});
  const [completedResult, setCompletedResult] = useState(null);
  const [completedMessage, setCompletedMessage] = useState("");
  const pollingRef = useRef(false);
  const querySeqRef = useRef(0);

  const normalizeTeamName = (name) => String(name || "").trim().toLowerCase();
  const filteredTeams = useMemo(() => {
    if (!selectedEventId || !eventTeamNames || eventTeamNames.size === 0) return [];
    return teams.filter((t) => eventTeamNames.has(normalizeTeamName(t.name)));
  }, [teams, selectedEventId, eventTeamNames]);
  // Fantasy pools often cover only part of a big field (e.g. 35 of 64 open
  // qualifier teams); missing opponents are filled with generic rank-250
  // unknowns, selectable as many times as needed.
  const teamOptions = useMemo(() => {
    const base = [
      { value: "", label: "Select team" },
      { value: "unknown", label: "Unknown team (rank 250)" },
      ...filteredTeams.map((t) => ({ value: String(t.team_id), label: t.name || `Team ${t.team_id}` })),
    ];
    // Slots restored from a stored run may reference materialized Unknown-N
    // teams (or other non-event teams); keep them displayable.
    const known = new Set(base.map((o) => o.value));
    const extras = [];
    slots.forEach((s) => {
      if (!s || s === "unknown" || known.has(String(s))) return;
      known.add(String(s));
      extras.push({ value: String(s), label: `${teamLookup[Number(s)] || `Team ${s}`} (${s})` });
    });
    return [...base, ...extras];
  }, [filteredTeams, slots, teamLookup]);

  const setSlot = (idx, val) => {
    setSlots((prev) => {
      const next = [...prev];
      next[idx] = val;
      return next;
    });
  };
  const groupSlots = useMemo(() => {
    const out = [];
    for (let g = 0; g < groupCount; g++) out.push(slots.slice(g * seedsPerGroup, g * seedsPerGroup + seedsPerGroup));
    return out;
  }, [slots, groupCount, seedsPerGroup]);

  const autofillFromHltv = async () => {
    setAutofillBusy(true);
    setAutofillMessage("");
    try {
      const data = await api.post(
        "/groups/autofill-from-hltv-event",
        { group_format: groupFormat === "de8_top3" ? "de8" : groupFormat },
        90000
      );
      if (data?.detail) {
        setAutofillMessage(String(data.detail));
        return;
      }
      const groups = data.groups || [];
      if (groups.length === 0) {
        setAutofillMessage("No groups found on the event page.");
        return;
      }
      const nextSlots = groups.flatMap((grp) => (grp.team_ids || []).map((id) => (id > 0 ? String(id) : "unknown")));
      setGroupCount(groups.length);
      setSlots(nextSlots);
      const tbd = nextSlots.filter((s) => s === "unknown").length;
      setAutofillMessage(
        `Filled ${groups.length} groups from HLTV${tbd ? ` (${tbd} undecided slots set to Unknown)` : ""}.`
      );
      if (refresh) await refresh();
    } catch (e) {
      setAutofillMessage(e?.message || "Autofill failed.");
    } finally {
      setAutofillBusy(false);
    }
  };
  const allSlotsFilled =
    slots.length === groupCount * seedsPerGroup && slots.every((s) => s === "unknown" || Number(s) > 0);
  const teamName = (id) => {
    if (id === "unknown") return "Unknown team";
    return teamLookup[Number(id)] || (id ? `Team ${id}` : "TBD");
  };

  const loadEvents = async (retriesLeft = 3) => {
    let data = null;
    try {
      data = await api.get("/events/");
    } catch {
      if (retriesLeft > 0) setTimeout(() => loadEvents(retriesLeft - 1), 5000);
      return;
    }
    if (data?.detail) return;
    const allEvents = Array.isArray(data.events) ? data.events : [];
    setEvents(allEvents);
    const active = data.active_event_id;
    setSelectedEventId(active == null ? "" : String(active));
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
  const loadLatest = async () => {
    const data = await api.get("/groups/latest", 120000);
    if (!data?.exists) return false;
    const payload = data.payload || {};
    if (payload.group_format) {
      setGroupFormat(["de8", "de8_top3"].includes(payload.group_format) ? payload.group_format : "gsl4");
    }
    const savedGroups = payload.groups || [];
    if (savedGroups.length > 0) {
      setGroupCount(savedGroups.length);
      setSlots(savedGroups.flat().map((x) => String(x)));
    }
    setCombinedPlayoffs(Boolean(payload.combined_playoffs));
    if (payload.n_playoff_sims) setPlayoffSims(String(payload.n_playoff_sims));
    if (payload.playoff_stop_teams) setPlayoffStopTeams(String(payload.playoff_stop_teams));
    setResults(data.results || null);
    setUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
    return savedGroups.length > 0;
  };
  // Prefill the group seeds from what was detected + parsed at import time, so
  // the tab is ready with no manual "Autofill from HLTV event" click. Only used
  // when no stored simulation already populated the slots.
  const loadEventAutofill = async () => {
    try {
      const data = await api.get("/groups/event-autofill", 60000);
      if (data?.status !== "ok") return;
      const groups = data.groups || [];
      if (groups.length === 0) return;
      const fmt =
        data.group_format === "de8" ? (groupVariant === "de8_top3" ? "de8_top3" : "de8") : "gsl4";
      const nextSlots = groups.flatMap((grp) =>
        (grp.team_ids || []).map((id) => (id > 0 ? String(id) : "unknown"))
      );
      setGroupFormat(fmt);
      setGroupCount(groups.length);
      setSlots(nextSlots);
      setAutofillMessage(`Prefilled ${groups.length} groups from the imported event.`);
    } catch {
      /* best-effort; button remains available */
    }
  };
  const loadLatestCombos = async () => {
    const data = await api.get("/groups/best-team/latest", 60000);
    if (!data?.exists) {
      setLiveMode(false);
      return;
    }
    setLiveMode(Boolean(data.live));
    setCombosReady(true);
    setCombosUpdatedAt(data.updated_at ? new Date(Number(data.updated_at) * 1000).toISOString() : "");
    if (data.pool_reduced_to && data.pool_reduced_from && data.pool_reduced_to < data.pool_reduced_from) {
      setPoolReduction({ from: data.pool_reduced_from, to: data.pool_reduced_to });
    } else {
      setPoolReduction(null);
    }
  };

  useEffect(() => {
    loadEvents();
    loadLatestCombos();
    (async () => {
      const populated = await loadLatest();
      if (!populated) await loadEventAutofill();
    })();
  }, []);
  useEffect(() => {
    loadEventTeams(selectedEventId);
  }, [selectedEventId]);

  const run = async () => {
    if (!allSlotsFilled) return;
    setBusy(true);
    setRunMessage("");
    setRunProgress({ done: 0, total: 0 });
    try {
      const start = await api.post("/groups/start", {
        groups: groupSlots.map((g) => g.map((v) => (v === "unknown" ? "unknown" : Number(v)))),
        group_format: groupFormat,
        combined_playoffs: combinedPlayoffs,
        n_playoff_sims: Math.max(200, Math.min(20000, Number(playoffSims) || 2000)),
        playoff_stop_teams: groupFormat === "de8_top3" ? 1 : Number(playoffStopTeams) || 1,
      });
      if (start?.detail) {
        setRunMessage(String(start.detail));
        return;
      }
      let done = false;
      while (!done) {
        const status = await api.get(`/groups/job/${start.job_id}`);
        if (status?.detail) {
          setRunMessage(String(status.detail));
          return;
        }
        setRunProgress({ done: Number(status.processed_units || 0), total: Number(status.total_units || 0) });
        if (status.status === "failed") {
          setRunMessage(status.error || "Group simulation failed.");
          return;
        }
        if (status.status === "completed") {
          await loadLatest();
          setCombosReady(false);
          setTopTeams(null);
          setAllTeams(null);
          await loadLatestCombos();
          if (refresh) await refresh(); // pick up any newly created Unknown teams
          done = true;
          break;
        }
        await new Promise((resolve) => setTimeout(resolve, 300));
      }
    } catch (e) {
      setRunMessage(e?.message || "Failed to run group simulation.");
    } finally {
      setBusy(false);
    }
  };

  const resetStored = async () => {
    await api.delete("/groups/latest");
    setResults(null);
    setUpdatedAt("");
    setTopTeams(null);
    setAllTeams(null);
    setCombosReady(false);
    setCombosUpdatedAt("");
    setCompletedResult(null);
    setTopMessage("");
  };

  const runCombinations = async () => {
    setBusy(true);
    setTopMessage("");
    setComboProgress({ done: 0, total: 0 });
    try {
      const start = await api.post("/groups/best-team/start", {});
      if (start?.detail) {
        setTopMessage(String(start.detail));
        return;
      }
      if (pollingRef.current) return;
      pollingRef.current = true;
      try {
        let done = false;
        while (!done) {
          const status = await api.get(`/groups/best-team/job/${start.job_id}`);
          if (status?.detail) {
            setTopMessage(String(status.detail));
            return;
          }
          setComboProgress({
            done: Number(status.processed_combinations || 0),
            total: Number(status.total_combinations || 0),
          });
          if (status.status === "failed") {
            setTopMessage(status.error || "Combination job failed.");
            return;
          }
          if (status.status === "completed") {
            setCombosReady(true);
            setCombosUpdatedAt(new Date().toISOString());
            done = true;
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
      } finally {
        pollingRef.current = false;
      }
    } catch (e) {
      setTopMessage(e?.message || "Failed to run combinations.");
    } finally {
      setBusy(false);
    }
  };

  const queryCombos = async (nextPage = 0) => {
    if (!combosReady) return;
    const seq = ++querySeqRef.current;
    try {
      const data = await api.post(
        "/groups/best-team/query",
        { mode: comboMode, search: comboSearch, sort: sortKey, page: nextPage, page_size: 200 },
        120000
      );
      if (seq !== querySeqRef.current) return;
      setTopTeams(data.top_teams || []);
      setAllTeams(data.page_teams || []);
      setFilteredCount(Number(data.filtered_count || 0));
      setPage(Number(data.page || nextPage || 0));
      setTopMessage(
        data.exact === false
          ? "Search hit its time budget — showing the best rosters found so far (near-optimal, not proven optimal)."
          : ""
      );
    } catch (e) {
      if (seq !== querySeqRef.current) return;
      setTopMessage(e?.message || "Failed to load combinations.");
    }
  };
  useEffect(() => {
    if (!combosReady) return;
    queryCombos(0);
  }, [combosReady, comboMode, comboSearch, sortKey]);

  const completedMatchCount = groupFormat === "de8" ? 10 : 5;
  const other = (pair, winner) => (pair.length === 2 ? pair.find((t) => String(t) !== String(winner)) : undefined);
  const completedDerived = useMemo(() => {
    const empty = Array(5).fill("");
    return groupSlots.map((group, g) => {
      const picks = completedPicks[g] || empty;
      const [s1, s2, s3, s4] = group;
      const o1 = [s1, s2];
      const o2 = [s3, s4];
      const o1w = picks[0];
      const o2w = picks[1];
      const winnersPair = o1w && o2w ? [o1w, o2w] : [];
      const losersPair = o1w && o2w ? [other(o1, o1w), other(o2, o2w)] : [];
      const wWinner = picks[2];
      const eWinner = picks[3];
      const deciderPair =
        wWinner && eWinner && winnersPair.length ? [other(winnersPair, wWinner), eWinner] : [];
      const pairs = [o1, o2, winnersPair, losersPair, deciderPair];
      const complete = picks.every((p, i) => Boolean(p) && pairs[i].some((t) => String(t) === String(p)));
      return { pairs, picks, complete };
    });
  }, [groupSlots, completedPicks, groupFormat, completedMatchCount]);

  // de8 completed derivation (10 matches): opening 1-4, upper sf 1-2, lower r1 1-2, lower sf 1-2.
  const completedDerived8 = useMemo(() => {
    const empty = Array(10).fill("");
    return groupSlots.map((group, g) => {
      const picks = completedPicks[g] || empty;
      const o1 = [group[0], group[1]];
      const o2 = [group[2], group[3]];
      const o3 = [group[4], group[5]];
      const o4 = [group[6], group[7]];
      const [w1, w2, w3, w4] = [picks[0], picks[1], picks[2], picks[3]];
      const L1 = w1 ? other(o1, w1) : undefined;
      const L2 = w2 ? other(o2, w2) : undefined;
      const L3 = w3 ? other(o3, w3) : undefined;
      const L4 = w4 ? other(o4, w4) : undefined;
      const usf1 = w1 && w2 ? [w1, w2] : []; // upper sf 1
      const usf2 = w3 && w4 ? [w3, w4] : []; // upper sf 2
      const lr1 = w1 && w2 ? [L1, L2] : []; // lower r1 1
      const lr2 = w3 && w4 ? [L3, L4] : []; // lower r1 2
      const uw1 = picks[4]; // upper sf 1 winner
      const uw2 = picks[5]; // upper sf 2 winner
      const UL1 = uw1 && usf1.length ? other(usf1, uw1) : undefined;
      const UL2 = uw2 && usf2.length ? other(usf2, uw2) : undefined;
      const lw1 = picks[6]; // lower r1 1 winner
      const lw2 = picks[7]; // lower r1 2 winner
      const lsf1 = lw1 && UL2 !== undefined ? [lw1, UL2] : []; // crossed: lr1 winner vs other upper loser
      const lsf2 = lw2 && UL1 !== undefined ? [lw2, UL1] : [];
      const pairs = [o1, o2, o3, o4, usf1, usf2, lr1, lr2, lsf1, lsf2];
      const complete = picks.every((p, i) => Boolean(p) && pairs[i].length === 2 && pairs[i].some((t) => String(t) === String(p)));
      return { pairs, picks, complete };
    });
  }, [groupSlots, completedPicks]);

  const activeCompletedDerived = groupFormat === "de8" ? completedDerived8 : completedDerived;
  const allGroupsComplete =
    activeCompletedDerived.length > 0 && activeCompletedDerived.every((g) => g && g.complete);

  const setCompletedPick = (g, matchIdx, value) => {
    setCompletedPicks((prev) => {
      const picks = [...(prev[g] || Array(completedMatchCount).fill(""))];
      picks[matchIdx] = value;
      if (groupFormat === "de8") {
        // opening picks (0-3) reset upper sf, lower r1, lower sf; upper/lower r1 reset lower sf.
        if (matchIdx <= 3) {
          picks[4] = ""; picks[5] = ""; picks[6] = ""; picks[7] = ""; picks[8] = ""; picks[9] = "";
        } else if (matchIdx <= 7) {
          picks[8] = ""; picks[9] = "";
        }
      } else {
        if (matchIdx <= 1) {
          picks[2] = ""; picks[3] = ""; picks[4] = "";
        } else if (matchIdx <= 3) {
          picks[4] = "";
        }
      }
      return { ...prev, [g]: picks };
    });
    setCompletedResult(null);
  };

  const scoreCompleted = async () => {
    setCompletedMessage("");
    try {
      const data = await api.post(
        "/groups/best-team/completed-query",
        {
          group_winners: activeCompletedDerived.map((g) => g.picks.map(Number)),
          search: comboSearch,
          page: 0,
          page_size: 200,
        },
        120000
      );
      if (data?.detail) {
        setCompletedMessage(String(data.detail));
        return;
      }
      setCompletedResult(data);
    } catch (e) {
      setCompletedMessage(e?.message || "Failed to score completed groups.");
    }
  };

  const valueData = useMemo(() => buildPlayerValueRowsFromSimulation(results, players), [results, players]);
  const matchLabels =
    groupFormat === "de8"
      ? [
          "Opening 1",
          "Opening 2",
          "Opening 3",
          "Opening 4",
          "Upper semi 1",
          "Upper semi 2",
          "Lower round 1",
          "Lower round 2",
          "Lower semi 1",
          "Lower semi 2",
        ]
      : ["Opening 1", "Opening 2", "Winners' match", "Elimination", "Decider"];
  const matchPlaceholders =
    groupFormat === "de8"
      ? [
          ["Seed 1", "Seed 2"],
          ["Seed 3", "Seed 4"],
          ["Seed 5", "Seed 6"],
          ["Seed 7", "Seed 8"],
          ["Opening 1 winner", "Opening 2 winner"],
          ["Opening 3 winner", "Opening 4 winner"],
          ["Opening 1 loser", "Opening 2 loser"],
          ["Opening 3 loser", "Opening 4 loser"],
          ["Lower R1 winner", "Upper semi 2 loser"],
          ["Lower R2 winner", "Upper semi 1 loser"],
        ]
      : [
          ["Seed 1", "Seed 2"],
          ["Seed 3", "Seed 4"],
          ["Opening 1 winner", "Opening 2 winner"],
          ["Opening 1 loser", "Opening 2 loser"],
          ["Winners' loser", "Elimination winner"],
        ];
  const metricLabel = (team) =>
    comboMode === "single_outcome"
      ? `Ceiling ${Number(team?.ceiling_points || 0).toFixed(2)}`
      : comboMode === "most_outcomes"
      ? `Win ${(Number(team?.outcome_win_probability || 0) * 100).toFixed(1)}%`
      : `EV ${Number(team?.average_ev ?? team?.total_ev ?? 0).toFixed(2)}`;
  const groupTeamInitials = (teamId) => {
    if (teamId === "unknown") return "??";
    const name = teamLookup[Number(teamId)] || "";
    const parts = String(name).trim().split(/\s+/).filter(Boolean);
    if (parts.length >= 2) return `${parts[0][0] || ""}${parts[1][0] || ""}`.toUpperCase();
    return String(name || "?").slice(0, 2).toUpperCase();
  };
  const GroupTeamRow = ({ teamId, placeholder }) => (
    <div className={`playoff-team-row ${teamId ? "" : "muted"}`}>
      <span className={`playoff-team-badge ${teamId ? "" : "empty"}`}>{teamId ? groupTeamInitials(teamId) : "?"}</span>
      {teamId ? <span>{teamName(teamId)}</span> : <span className="playoff-team-tbd">{placeholder || "TBD"}</span>}
    </div>
  );
  const GroupMatchCard = ({ title, rows, className = "" }) => (
    <div className={`playoff-match-card ${className}`}>
      <div className="playoff-match-head">
        <strong>{title}</strong>
        <span>BO3</span>
      </div>
      <div className="playoff-match-teams">{rows}</div>
    </div>
  );
  const GroupPickRow = ({ teamId, selected, onSelect, placeholder }) => (
    <button
      type="button"
      className={`playoff-team-row completed-pick ${teamId ? "" : "muted"} ${selected ? "active" : ""}`}
      onClick={() => teamId && onSelect(String(teamId))}
      disabled={!teamId}
    >
      <span className={`playoff-team-badge ${teamId ? "" : "empty"}`}>{teamId ? groupTeamInitials(teamId) : "?"}</span>
      <span>{teamId ? teamName(teamId) : placeholder || "TBD"}</span>
    </button>
  );
  const qualificationOdds = useMemo(() => {
    const rates = results?.playoff?.advance_rate;
    if (!rates) return [];
    return Object.entries(rates)
      .map(([tid, rate]) => ({ teamId: Number(tid), rate: Number(rate) }))
      .sort((a, b) => b.rate - a.rate);
  }, [results?.playoff?.advance_rate]);

  // Per-team probability of qualifying out of its group, from the exact
  // enumerated outcomes (probabilities sum to 1 per group). Available for both
  // GSL and 8-team formats regardless of the combined-playoff toggle.
  const groupQualifyOdds = useMemo(() => {
    const outcomes = results?.outcomes;
    if (!Array.isArray(outcomes) || outcomes.length === 0) return [];
    const byTeam = {};
    const ensure = (tid, g) => {
      const key = Number(tid);
      if (!byTeam[key]) byTeam[key] = { teamId: key, rate: 0, group: Number(g) || 0 };
      return byTeam[key];
    };
    outcomes.forEach((o) => {
      const p = Number(o.probability) || 0;
      const g = Number(o.group) || 0;
      (o.qualified || []).forEach((tid) => (ensure(tid, g).rate += p));
      (o.eliminated || []).forEach((tid) => ensure(tid, g));
    });
    return Object.values(byTeam).sort((a, b) => a.group - b.group || b.rate - a.rate);
  }, [results]);

  // Per-player expected component split (rating/win/role/booster), keyed by
  // player id, so a clicked name can show where its points come from.
  const playerEvByPid = useMemo(() => {
    const map = {};
    Object.values(results?.teams || {}).forEach((t) => {
      Object.entries(t?.players || {}).forEach(([pid, comps]) => {
        map[Number(pid)] = comps || {};
      });
    });
    return map;
  }, [results]);

  const [breakdownPlayer, setBreakdownPlayer] = useState(null);
  const openGroupPlayer = (p) => {
    const comps = playerEvByPid[Number(p.player_id)] || {};
    setBreakdownPlayer({
      player_id: p.player_id,
      name: p.name,
      team_id: p.team_id,
      rating: Number(comps.rating_points_total || 0),
      win: Number(comps.win_points_total || 0),
      role: Number(comps.role_points_total || 0),
      booster: Number(comps.booster_points_total || 0),
      total: Number(comps.total_points || 0),
    });
  };
  const renderPlayerLinks = (list) =>
    (list || []).map((p, i) => (
      <span key={`${p.player_id}-${i}`}>
        {i > 0 ? ", " : ""}
        <button type="button" className="inline-link-btn" onClick={() => openGroupPlayer(p)}>
          {p.name}
        </button>{" "}
        ({teamLookup[p.team_id] || p.team_id})
      </span>
    ));
  // Completed Groups: components come from the exact-outcome player_values row,
  // so open with those concrete values and an outcome-specific note.
  const completedEvByPid = useMemo(() => {
    const map = {};
    (completedResult?.player_values || []).forEach((row) => {
      map[Number(row.player_id)] = row;
    });
    return map;
  }, [completedResult]);
  const openCompletedPlayer = (p) => {
    const row = completedEvByPid[Number(p.player_id)] || {};
    setBreakdownPlayer({
      player_id: p.player_id,
      name: p.name,
      team_id: p.team_id,
      rating: Number(row.rating || 0),
      win: Number(row.win || 0),
      role: Number(row.role || 0),
      booster: Number(row.booster || 0),
      total: Number(row.points || 0),
      note: "Points for this exact set of group results.",
    });
  };

  return (
    <Section title="Double-Elimination Groups (BO3)">
      <div className="stack">
        <div className="tab-bar small">
          <button className={groupsTab === "stage" ? "tab active" : "tab"} onClick={() => setGroupsTab("stage")}>
            Group Stage
          </button>
          <button className={groupsTab === "top5" ? "tab active" : "tab"} onClick={() => setGroupsTab("top5")}>
            Top 5 Teams
          </button>
          <button className={groupsTab === "completed" ? "tab active" : "tab"} onClick={() => setGroupsTab("completed")}>
            Completed Groups
          </button>
          <button className={groupsTab === "value" ? "tab active" : "tab"} onClick={() => setGroupsTab("value")}>
            Player Value
          </button>
        </div>

        {groupsTab === "stage" && (
          <>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="secondary" onClick={autofillFromHltv} disabled={busy || autofillBusy}>
                {autofillBusy ? "Fetching event..." : "Autofill from HLTV event"}
              </button>
              {autofillMessage && <span className="muted">{autofillMessage}</span>}
            </div>
            {groupSlots.map((group, g) => {
              const openingCount = seedsPerGroup / 2;
              const openings = Array.from({ length: openingCount }, (_, i) => [i * 2, i * 2 + 1]);
              const label = (idx) => {
                const v = group[idx];
                if (!v) return `Seed ${idx + 1}`;
                return teamName(v);
              };
              const seedSelect = (idx) => (
                <select
                  value={group[idx]}
                  onChange={(e) => setSlot(g * seedsPerGroup + idx, e.target.value)}
                  disabled={busy}
                >
                  {teamOptions.map((option) => (
                    <option key={option.value || "empty"} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              );
              return (
                <div className="card sub" key={`group-${g}`}>
                  <h3>Group {g + 1}</h3>
                  <p className="muted">
                    {groupFormat === "de8"
                      ? "Opening round pairings. Each winner advances in the upper bracket; each loser drops to the lower bracket."
                      : "Opening round pairings. Winners meet in the winners' match; losers meet in the elimination match."}
                  </p>
                  <div className="bracket-setup">
                    {openings.map(([a, b], mi) => (
                      <div className="match-row" key={`g${g}-o${mi}`}>
                        <span className="match-tag">Opening {mi + 1}</span>
                        <div className="match-side">{seedSelect(a)}</div>
                        <span className="vs">vs</span>
                        <div className="match-side">{seedSelect(b)}</div>
                      </div>
                    ))}
                  </div>
                  <div className="bracket-depiction">
                    <span className="bracket-title">Upper bracket</span>
                    {groupFormat === "de8" ? (
                      <>
                        <div className="ub-flow">
                          <div className="ub-col">
                            <div className="ub-pair">
                              <span>{label(0)}</span>
                              <span>{label(1)}</span>
                            </div>
                            <div className="ub-pair">
                              <span>{label(2)}</span>
                              <span>{label(3)}</span>
                            </div>
                            <div className="ub-pair">
                              <span>{label(4)}</span>
                              <span>{label(5)}</span>
                            </div>
                            <div className="ub-pair">
                              <span>{label(6)}</span>
                              <span>{label(7)}</span>
                            </div>
                          </div>
                          <div className="ub-arrow">→</div>
                          <div className="ub-col">
                            <div className="ub-node">Upper semi 1<small>Opening 1 W vs Opening 2 W</small></div>
                            <div className="ub-node">Upper semi 2<small>Opening 3 W vs Opening 4 W</small></div>
                          </div>
                          <div className="ub-arrow">→</div>
                          <div className="ub-col">
                            <div className="ub-qual">Qualify (1st &amp; 2nd)<small>the two upper-semi winners</small></div>
                          </div>
                        </div>
                        <p className="muted small">
                          Opening losers drop to lower round 1. The two upper-semi losers then cross over —
                          each faces the <em>other</em> semi's lower-round-1 winner — for the last two spots (3rd &amp; 4th).
                        </p>
                      </>
                    ) : (
                      <>
                        <div className="ub-flow">
                          <div className="ub-col">
                            <div className="ub-pair">
                              <span>{label(0)}</span>
                              <span>{label(1)}</span>
                            </div>
                            <div className="ub-pair">
                              <span>{label(2)}</span>
                              <span>{label(3)}</span>
                            </div>
                          </div>
                          <div className="ub-arrow">→</div>
                          <div className="ub-col">
                            <div className="ub-node">Winners' match<small>Opening 1 W vs Opening 2 W</small></div>
                          </div>
                          <div className="ub-arrow">→</div>
                          <div className="ub-col">
                            <div className="ub-qual">Qualify (1st)<small>winners'-match winner</small></div>
                          </div>
                        </div>
                        <p className="muted small">
                          Opening losers meet in the elimination match; its winner faces the winners'-match loser
                          in the decider for 2nd place.
                        </p>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
            <div className="actions">
              <label className="checkbox-inline">
                <input
                  type="checkbox"
                  checked={combinedPlayoffs}
                  onChange={(e) => setCombinedPlayoffs(e.target.checked)}
                  disabled={busy || !Number.isInteger(Math.log2(qualsPerGroup * groupCount))}
                />
                <span>Combined playoffs (qualifiers feed one single-elim bracket)</span>
              </label>
              <button className="primary" onClick={run} disabled={busy || !allSlotsFilled}>
                {busy ? "Running..." : "Run Groups And Store Valuations"}
              </button>
              <button className="danger" onClick={resetStored} disabled={busy || !results}>
                Reset Stored Valuations
              </button>
              {updatedAt && <p className="muted">Stored: {new Date(updatedAt).toLocaleString()}</p>}
            </div>
            {!Number.isInteger(Math.log2(qualsPerGroup * groupCount)) && (
              <p className="muted">Combined playoffs need 1, 2, 4, 8, or 16 groups (a power-of-two bracket).</p>
            )}
            {busy && runProgress.total > 0 && (
              <>
                <p className="muted">
                  {runProgress.done <= groupCount
                    ? `Enumerating groups: ${Math.min(runProgress.done, groupCount)} / ${groupCount}`
                    : `Playoff simulations: ${(runProgress.done - groupCount).toLocaleString()} / ${(
                        runProgress.total - groupCount
                      ).toLocaleString()}`}
                </p>
                <div className="progress">
                  <div
                    className="progress-bar determinate"
                    style={{ width: `${Math.min(100, (runProgress.done / runProgress.total) * 100)}%` }}
                  />
                </div>
              </>
            )}
            {results?.playoff && qualificationOdds.length > 0 && (
              <div className="card sub">
                <h3>
                  {Number(results.playoff.stop_teams || 1) > 1
                    ? `Qualification Odds (top ${results.playoff.stop_teams})`
                    : "Championship Odds"}
                </h3>
                <p className="muted">
                  From {Number(results.playoff.n_sims || 0).toLocaleString()} playoff simulations on top of the exact
                  group stage.
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Team</th>
                      <th>{Number(results.playoff.stop_teams || 1) > 1 ? "Qualify %" : "Win %"}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {qualificationOdds.slice(0, 16).map((row, idx) => (
                      <tr key={row.teamId}>
                        <td>{idx + 1}</td>
                        <td>{teamName(row.teamId)}</td>
                        <td>{(row.rate * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {results && groupQualifyOdds.length > 0 && (
              <div className="card sub">
                <h3>Group Qualification Odds</h3>
                <p className="muted">
                  Chance each team finishes in its group's qualifying {qualsPerGroup} spots, from the exact enumerated
                  outcomes.
                </p>
                <table>
                  <thead>
                    <tr>
                      <th>Group</th>
                      <th>Team</th>
                      <th>Qualify %</th>
                    </tr>
                  </thead>
                  <tbody>
                    {groupQualifyOdds.map((row) => (
                      <tr key={row.teamId}>
                        <td>{row.group + 1}</td>
                        <td>{teamName(row.teamId)}</td>
                        <td>{(row.rate * 100).toFixed(1)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {combinedPlayoffs && (
              <>
                <div className="grid two">
                  <Input label="Playoff Simulations" value={playoffSims} onChange={setPlayoffSims} placeholder="2000" />
                  <Select
                    label="Bracket Ends At"
                    value={playoffStopTeams}
                    onChange={setPlayoffStopTeams}
                    options={(() => {
                      const bracket = groupCount * 2;
                      const roundName = (teamsInRound) => {
                        if (teamsInRound === 2) return "grand final";
                        if (teamsInRound === 4) return "semi-finals";
                        if (teamsInRound === 8) return "quarter-finals";
                        return `round of ${teamsInRound}`;
                      };
                      const options = [{ value: "1", label: "Play out full bracket (champion)" }];
                      for (let t = 2; t < bracket; t *= 2) {
                        options.push({
                          value: String(t),
                          label: `Top ${t} qualify (last round: ${roundName(t * 2)})`,
                        });
                      }
                      return options;
                    })()}
                  />
                </div>
                <p className="muted">
                  Bracket seeding: group 1 winner vs group 2 runner-up (and vice versa), then onward in listed order.
                  Player valuations add a Monte Carlo playoff run on top of the exact group stage; teams reaching the
                  chosen end point qualify without playing further (and take no elimination penalty).
                </p>
              </>
            )}
            {runMessage && <p className="muted">{runMessage}</p>}
          </>
        )}

        {groupsTab === "top5" && (
          <>
            {!results && (
              <div className="card sub">
                <p className="muted">Run the group stage first.</p>
              </div>
            )}
            {results && (
              <>
                <div className="actions">
                  {!liveMode && (
                    <button className="primary" onClick={runCombinations} disabled={busy}>
                      {busy && comboProgress.total > 0
                        ? `Running Combinations... ${comboProgress.done.toLocaleString()} / ${comboProgress.total.toLocaleString()}`
                        : "Run Combinations"}
                    </button>
                  )}
                  {liveMode && (
                    <p className="muted">
                      Large event: rosters are optimized live per query (top 2,000 under current constraints) — no
                      precompute needed.
                    </p>
                  )}
                  {!liveMode && combosUpdatedAt && (
                    <p className="muted">Combinations stored: {new Date(combosUpdatedAt).toLocaleString()}</p>
                  )}
                  {!liveMode && poolReduction && (
                    <p className="muted">
                      Pruned pool from {poolReduction.from} to {poolReduction.to} players before enumerating (dominated
                      players removed — the optimal team is provably retained).
                    </p>
                  )}
                </div>
                <div className="tab-bar small">
                  <button className={comboMode === "average" ? "tab active" : "tab"} onClick={() => setComboMode("average")}>
                    Average Player Value
                  </button>
                  <button
                    className={comboMode === "single_outcome" ? "tab active" : "tab"}
                    onClick={() => setComboMode("single_outcome")}
                  >
                    Best Single Outcome
                  </button>
                  <button
                    className={comboMode === "most_outcomes" ? "tab active" : "tab"}
                    onClick={() => setComboMode("most_outcomes")}
                  >
                    Most Likely Winner
                  </button>
                </div>
                {results?.combined_playoffs && (
                  <p className="muted">
                    Average EVs cover the whole event (exact groups + {Number(results?.playoff?.n_sims || 0).toLocaleString()}-sim
                    playoff run). Ceiling and Completed Groups score the group stage only.
                  </p>
                )}
                <div className="grid two">
                  <Input label="Search Combos" value={comboSearch} onChange={setComboSearch} placeholder="Player/team name or id" />
                  <div className="field">
                    <span>Filtered / Stored</span>
                    <div className="pill">{filteredCount.toLocaleString()}</div>
                  </div>
                </div>
                {topTeams && topTeams.length > 0 && (
                  <div className="card sub">
                    <h3>Top Teams</h3>
                    {topTeams.map((team, idx) => (
                      <div key={idx} className="card sub">
                        <h4>
                          #{idx + 1} {metricLabel(team)} | Cost {team.cost}
                          {comboMode !== "single_outcome" && Number(team?.booster_ev) > 0 ? (
                            <span className="muted"> (incl. booster {Number(team.booster_ev).toFixed(1)})</span>
                          ) : null}
                        </h4>
                        <p className="muted">{renderPlayerLinks(team.players)}</p>
                      </div>
                    ))}
                  </div>
                )}
                {allTeams && allTeams.length > 0 && (
                  <div className="card sub">
                    <h3>All Filtered Teams ({filteredCount.toLocaleString()})</h3>
                    <div className="actions">
                      <button className="secondary" onClick={() => queryCombos(Math.max(0, page - 1))} disabled={page === 0}>
                        Prev 200
                      </button>
                      <button
                        className="secondary"
                        onClick={() => queryCombos((page + 1) * 200 < filteredCount ? page + 1 : page)}
                        disabled={(page + 1) * 200 >= filteredCount}
                      >
                        Next 200
                      </button>
                      <p className="muted">Page {page + 1}</p>
                    </div>
                    <table>
                      <thead>
                        <tr>
                          <th>#</th>
                          <th>
                            {comboMode === "single_outcome" ? "Ceiling" : comboMode === "most_outcomes" ? "Win %" : "Avg EV"}
                          </th>
                          <th>Cost</th>
                          <th>Players</th>
                        </tr>
                      </thead>
                      <tbody>
                        {allTeams.map((team, idx) => (
                          <tr key={idx + page * 200}>
                            <td>{idx + 1 + page * 200}</td>
                            <td>
                              {comboMode === "most_outcomes"
                                ? `${(Number(team.outcome_win_probability || 0) * 100).toFixed(1)}%`
                                : Number(
                                    comboMode === "single_outcome"
                                      ? team.ceiling_points || 0
                                      : team.average_ev ?? team.total_ev ?? 0
                                  ).toFixed(2)}
                            </td>
                            <td>{team.cost}</td>
                            <td>{renderPlayerLinks(team.players)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
                {topMessage && <p className="muted">{topMessage}</p>}
              </>
            )}
          </>
        )}

        {groupsTab === "completed" && groupFormat === "de8_top3" && (
          <p className="muted">
            Completed-bracket scoring for the top-3 double-elim variant isn't supported yet — use the Group
            Stage simulation.
          </p>
        )}
        {groupsTab === "completed" && groupFormat !== "de8_top3" && (
          <>
            {!results && (
              <div className="card sub">
                <p className="muted">Run the group stage first.</p>
              </div>
            )}
            {results &&
              activeCompletedDerived.map((group, g) => {
                const pickRow = (matchIdx) => {
                  const placeholders = matchPlaceholders[matchIdx] || ["", ""];
                  const ready = group.pairs[matchIdx].length === 2 && group.pairs[matchIdx].every(Boolean);
                  return (
                    <div className="gsl-match" key={`c-${g}-${matchIdx}`}>
                      <span className="gsl-label">{matchLabels[matchIdx]}</span>
                      <span className="gsl-pick">
                        {[0, 1].map((rowIdx) => {
                          const tid = ready ? group.pairs[matchIdx][rowIdx] : "";
                          const selected = ready && String(group.picks[matchIdx]) === String(tid);
                          return (
                            <button
                              key={rowIdx}
                              disabled={!tid}
                              className={selected ? "active" : ""}
                              onClick={() => tid && setCompletedPick(g, matchIdx, String(tid))}
                            >
                              {tid ? teamName(tid) : placeholders[rowIdx]}
                            </button>
                          );
                        })}
                      </span>
                    </div>
                  );
                };
                return (
                  <div className="card sub" key={`completed-${g}`}>
                    <h3>Group {g + 1} Results</h3>
                    <p className="muted">Click each match winner; later matchups fill in from earlier picks.</p>
                    <div className="gsl-flow">
                      {matchLabels.map((_, matchIdx) => pickRow(matchIdx))}
                    </div>
                  </div>
                );
              })}
            {results && (
              <div className="actions">
                <button className="primary" onClick={scoreCompleted} disabled={!allGroupsComplete || !combosReady}>
                  Score Completed Groups
                </button>
                {!combosReady && <span className="muted">Run Combinations in Top 5 Teams first.</span>}
                {combosReady && !allGroupsComplete && <span className="muted">Pick every match winner first.</span>}
              </div>
            )}
            {completedMessage && <p className="muted">{completedMessage}</p>}
            {completedResult && (
              <>
                <div className="card sub">
                  <h3>Best Team For These Results</h3>
                  <p className="muted">
                    Outcome probability {(Number(completedResult.outcome_probability || 0) * 100).toFixed(3)}% of{" "}
                    {Number(completedResult.outcomes_count || 0).toLocaleString()} stored outcomes.
                  </p>
                  {(completedResult.top_teams || []).slice(0, 10).map((team, idx) => (
                    <div key={idx} className="card sub">
                      <h4>
                        #{idx + 1} Score {Number(team.bracket_score || 0).toFixed(2)} | Cost {team.cost}
                      </h4>
                      <p className="muted">
                        {(team.players || []).map((p, i) => (
                          <span key={`${p.player_id}-${i}`}>
                            {i > 0 ? ", " : ""}
                            <button type="button" className="inline-link-btn" onClick={() => openCompletedPlayer(p)}>
                              {p.name}
                            </button>{" "}
                            {Number(p.mode_score || 0).toFixed(1)}
                          </span>
                        ))}
                      </p>
                    </div>
                  ))}
                </div>
                <div className="card sub">
                  <h3>Player Scores</h3>
                  <table>
                    <thead>
                      <tr>
                        <th>Player</th>
                        <th>Cost</th>
                        <th>Points</th>
                        <th>Rating</th>
                        <th>Win</th>
                        <th>Role</th>
                        <th>Booster</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(completedResult.player_values || []).map((row) => (
                        <tr key={row.player_id}>
                          <td>{row.name}</td>
                          <td>{Number(row.price || 0).toLocaleString()}</td>
                          <td>{Number(row.points || 0).toFixed(2)}</td>
                          <td>{Number(row.rating || 0).toFixed(2)}</td>
                          <td>{Number(row.win || 0).toFixed(2)}</td>
                          <td>{Number(row.role || 0).toFixed(2)}</td>
                          <td>{Number(row.booster || 0).toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </>
        )}

        {groupsTab === "value" && results && (
          <PriceVsPointsPanel
            title="Player Price vs Points (Groups)"
            rows={valueData.rows}
            slope={valueData.slope}
            intercept={valueData.intercept}
          />
        )}
        {groupsTab === "value" && !results && (
          <div className="card sub">
            <p className="muted">Run the group stage first.</p>
          </div>
        )}
      </div>
      <GroupPlayerBreakdownModal
        player={breakdownPlayer}
        teamLookup={teamLookup}
        onClose={() => setBreakdownPlayer(null)}
      />
    </Section>
  );
}

function BountyTab(props) {
  const [bountyTab, setBountyTab] = useState("playoffs");
  return (
    <div className="stack">
      <div className="tab-bar small">
        <button className={bountyTab === "playoffs" ? "tab active" : "tab"} onClick={() => setBountyTab("playoffs")}>
          Playoffs
        </button>
        <button className="tab" disabled title="Online stage (32 teams) coming later">
          Online Stage
        </button>
      </div>
      {bountyTab === "playoffs" && <PlayoffTab {...props} variant="bounty" />}
    </div>
  );
}

function TournamentTab({ teams, teamLookup, players, sortTeams, applyFilters, onOpenPlayer, refresh, notify }) {
  // One adaptive page: the active fantasy event's kind (per-stage, e.g. a
  // "Cologne Groups" event shows only the group stage) picks which tournament
  // UI renders. Auto-detected on the backend; a manual override is stored on
  // the event for the rare ambiguous case (and to reach Bounty mode).
  const [kindInfo, setKindInfo] = useState(null);
  const [message, setMessage] = useState("");

  const loadKind = async () => {
    setMessage("");
    try {
      const evs = await api.get("/events/");
      const active = evs?.active_event_id ?? null;
      if (active == null) {
        setKindInfo(null);
        setMessage("No active fantasy event. Set one active in the Events tab.");
        return;
      }
      const info = await api.get(`/events/${active}/kind`);
      if (info?.detail) {
        setKindInfo(null);
        setMessage(`Format detection failed for event ${active}: ${info.detail}`);
        return;
      }
      if (!info?.kind || info.kind === "unknown") {
        setKindInfo(null);
        setMessage(`Event ${active} has no detectable tournament format (no structured data on its event page).`);
        return;
      }
      setKindInfo(info);
    } catch (e) {
      setMessage(e?.message || "Could not detect the event's tournament kind.");
    }
  };

  useEffect(() => {
    loadKind();
  }, []);

  const kind = kindInfo?.kind || null;
  const sharedProps = { teams, teamLookup, players, sortTeams, applyFilters, onOpenPlayer };
  return (
    <div className="stack">
      {message && <p className="muted">{message}</p>}
      {kind && !["swiss", "groups", "playoff", "bounty"].includes(kind) && (
        <p className="muted">
          Detected format: {kindInfo?.label || kind} — no simulator supports this format yet.
        </p>
      )}
      {kind === "swiss" && <SwissTab {...sharedProps} />}
      {kind === "groups" && (
        <GroupsTab
          teams={teams}
          teamLookup={teamLookup}
          players={players}
          refresh={refresh}
          groupVariant={kindInfo?.group_variant}
        />
      )}
      {kind === "playoff" && <PlayoffTab {...sharedProps} detectedBracketSize={kindInfo?.playoff_size} />}
      {kind === "bounty" && <BountyTab {...sharedProps} />}
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

  // Toast popups removed by request — components still call notify(), it just
  // no longer surfaces anything.
  const notify = () => {};

  const mapStatsModalRefreshRef = useRef(null);
  const mapStatsJob = useMapStatsJob({ refresh: load, notify, modalRefreshRef: mapStatsModalRefreshRef });

  const handleOpenPlayerFromAnywhere = (playerId) => {
    const pid = Number(playerId);
    if (!Number.isFinite(pid) || pid <= 0) return;
    setActive("view");
    setOpenPlayerId(pid);
  };

  const [openTeamId, setOpenTeamId] = useState(null);
  const handleOpenTeamFromAnywhere = (teamId) => {
    const tid = Number(teamId);
    if (!Number.isFinite(tid) || tid <= 0) return;
    setActive("view");
    setOpenTeamId(tid);
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
        openTeamId={openTeamId}
        onOpenTeamHandled={() => setOpenTeamId(null)}
        mapStats={mapStatsJob}
        mapStatsModalRefreshRef={mapStatsModalRefreshRef}
      />
    ),
    events: (
      <EventsTab
        refreshData={load}
        notify={notify}
        players={players}
        teams={teams}
        onOpenPlayer={handleOpenPlayerFromAnywhere}
        onOpenTeam={handleOpenTeamFromAnywhere}
      />
    ),
    devlab: <DevLabTab players={players} />,
    tournament: (
      <TournamentTab
        teams={teams}
        teamLookup={teamLookup}
        players={players}
        sortTeams={sortTeams}
        applyFilters={applyFilters}
        onOpenPlayer={handleOpenPlayerFromAnywhere}
        refresh={load}
        notify={notify}
      />
    ),
    scheduling: <SchedulingTab notify={notify} players={players} refresh={load} mapStats={mapStatsJob} teams={teams} />,
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

      <main className="content">{contentMap[active]}</main>
    </div>
  );
}

