import React, { useEffect, useMemo, useState } from "react";

const tabs = [
  { key: "view", label: "Database" },
  { key: "sim", label: "Run Simulation" },
  { key: "bracket", label: "Bracket" },
  { key: "playoff", label: "Playoff Bracket" },
  { key: "admin", label: "Admin" },
];

const api = window.api || {
  get: (path) => fetch(`http://127.0.0.1:8000${path}`).then((r) => r.json()),
  post: (path, body) =>
    fetch(`http://127.0.0.1:8000${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json()),
  delete: (path) =>
    fetch(`http://127.0.0.1:8000${path}`, {
      method: "DELETE",
    }).then((r) => r.json()),
};

const TabButton = ({ active, onClick, children }) => (
  <button className={active ? "tab active" : "tab"} onClick={onClick}>
    {children}
  </button>
);

const Pill = ({ children }) => <span className="pill">{children}</span>;

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

function SimulationTab({ teams, teamLookup }) {
  const [selected, setSelected] = useState([]);
  const [bo, setBo] = useState("elim_qual");
  const [sims, setSims] = useState("200");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [topTeams, setTopTeams] = useState(null);
  const [exclude, setExclude] = useState(new Set());
  const [playerLookup, setPlayerLookup] = useState({});
  const toggle = (tid) =>
    setSelected((prev) => (prev.includes(tid) ? prev.filter((x) => x !== tid) : [...prev, tid]));

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

  const run = async () => {
    if (selected.length < 2) return;
    setBusy(true);
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
    const data = await api.post("/simulate/", body);
    setResults(data);
    setTopTeams(null);
    setBusy(false);
  };

  const toggleExclude = (pid) => {
    setExclude((prev) => {
      const next = new Set(Array.from(prev));
      if (next.has(pid)) next.delete(pid);
      else next.add(pid);
      return next;
    });
  };

  const findTopTeams = async () => {
    if (!results || selected.length < 2) return;
    setBusy(true);
    const vrs = {};
    teams.forEach((t) => {
      if (selected.includes(t.team_id)) vrs[t.team_id] = t.vrs_rank ?? 999;
    });
    const payload = {
      team_ids: selected,
      vrs_ranks: vrs,
      bo3_mode: bo,
      n_sims: Number(sims || 0),
      exclude_player_ids: Array.from(exclude),
    };
    const data = await api.post("/best-team/", payload);
    setTopTeams(data.top_teams || []);
    setBusy(false);
  };

  return (
    <Section title="Swiss Simulation">
      <div className="stack">
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
              { value: "elim_qual", label: "elim_qual" },
              { value: "all", label: "all" },
              { value: "none", label: "none" },
            ]}
          />
          <Input label="# Sims" value={sims} onChange={setSims} />
          <div className="field">
            <span>Run</span>
            <button className="primary" onClick={run} disabled={busy || selected.length < 2}>
              {busy ? "Running..." : "Run Simulation"}
            </button>
          </div>
        </div>
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
                <table>
                  <thead>
                    <tr>
                      <th>Player</th>
                      <th>Total</th>
                      <th>Rating</th>
                      <th>Win</th>
                      <th>Role</th>
                      <th>Booster</th>
                      <th>Exclude</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(data.players || {}).map(([pid, comps]) => (
                      <tr key={pid}>
                        <td>{playerLookup[Number(pid)] || pid}</td>
                        <td>{comps.total.toFixed(2)}</td>
                        <td>{comps.rating.toFixed(2)}</td>
                        <td>{comps.win.toFixed(2)}</td>
                        <td>{comps.role.toFixed(2)}</td>
                        <td>{comps.booster.toFixed(2)}</td>
                        <td>
                          <button
                            className={exclude.has(Number(pid)) ? "chip active" : "chip"}
                            onClick={() => toggleExclude(Number(pid))}
                          >
                            {exclude.has(Number(pid)) ? "Excluded" : "Exclude"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ))}
          </div>
        )}
        {results && (
          <div className="actions">
            <button className="primary" onClick={findTopTeams} disabled={busy}>
              {busy ? "Working..." : "Find Best 5 Team"}
            </button>
          </div>
        )}
        {topTeams && topTeams.length > 0 && (
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
                        <td>{p.name}</td>
                        <td>{teamLookup[p.team_id] || p.team_id}</td>
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
      </div>
    </Section>
  );
}

function BracketTab({ teams, teamLookup }) {
  const [selected, setSelected] = useState([]);
  const [bo, setBo] = useState("elim_qual");
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);

  const toggle = (tid) =>
    setSelected((prev) => (prev.includes(tid) ? prev.filter((x) => x !== tid) : [...prev, tid]));

  const run = async () => {
    if (selected.length < 2) return;
    setBusy(true);
    const vrs = {};
    teams.forEach((t) => {
      if (selected.includes(t.team_id)) vrs[t.team_id] = t.vrs_rank ?? 999;
    });
    const data = await api.post("/bracket/swiss-run", {
      team_ids: selected,
      vrs_ranks: vrs,
      bo3_mode: bo,
    });
    setResults(data);
    setBusy(false);
  };

  return (
    <Section title="Swiss Bracket (single run)">
      <div className="stack">
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
        <div className="grid two">
          <Select
            label="BO Mode"
            value={bo}
            onChange={setBo}
            options={[
              { value: "elim_qual", label: "elim_qual" },
              { value: "all", label: "all" },
              { value: "none", label: "none" },
            ]}
          />
          <div className="field">
            <span>Run</span>
            <button className="primary" onClick={run} disabled={busy || selected.length < 2}>
              {busy ? "Running..." : "Run Bracket"}
            </button>
          </div>
        </div>
        {results && (
          <div className="grid two">
            {Object.entries(results).map(([tid, data]) => (
              <div key={tid} className="card sub">
                <h3>{teamLookup[Number(tid)] || `Team ${tid}`}</h3>
                <p className="muted">
                  Record: {data.wins}-{data.losses} {data.qualified ? "(qualified)" : data.eliminated ? "(eliminated)" : ""}
                </p>
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
                        <td>{pid}</td>
                        <td>{comps.total_points?.toFixed(2) ?? comps.total?.toFixed?.(2)}</td>
                        <td>{comps.rating_points_total?.toFixed(2) ?? comps.rating?.toFixed?.(2)}</td>
                        <td>{comps.win_points_total?.toFixed(2) ?? comps.win?.toFixed?.(2)}</td>
                        <td>{comps.role_points_total?.toFixed(2) ?? comps.role?.toFixed?.(2)}</td>
                        <td>{comps.booster_points_total?.toFixed(2) ?? comps.booster?.toFixed?.(2)}</td>
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

function PlayoffTab({ teams, teamLookup, players, sortTeams, applyFilters }) {
  const [slots, setSlots] = useState(Array(8).fill(""));
  const [results, setResults] = useState(null);
  const [busy, setBusy] = useState(false);
  const [topTeams, setTopTeams] = useState(null);
  const [allTeams, setAllTeams] = useState(null);
  const [baseTeams, setBaseTeams] = useState(null);
  const [page, setPage] = useState(0);
  const [topMessage, setTopMessage] = useState("");
  const [sims, setSims] = useState("200");
  const [includeSet, setIncludeSet] = useState(new Set());
  const [excludeSet, setExcludeSet] = useState(new Set());
  const [showFilterModal, setShowFilterModal] = useState(false);
  const [sortKey, setSortKey] = useState("ev_desc");
  const playerLookup = useMemo(() => {
    const m = {};
    players.forEach((p) => (m[p.player_id] = p.name));
    return m;
  }, [players]);

  const setSlot = (idx, val) => {
    setSlots((prev) => {
      const next = [...prev];
      next[idx] = val;
      return next;
    });
  };

  const run = async () => {
    const ids = slots.map((s) => Number(s));
    if (ids.some((id) => !id)) return;
    setBusy(true);
    const data = await api.post("/playoff/run", { team_slots: ids, n_sims: Number(sims || 1) });
    setResults(data);
    setTopTeams(null);
    setBusy(false);
  };

  const findTopTeams = async () => {
    const ids = slots.map((s) => Number(s));
    if (ids.some((id) => !id)) return;
    setBusy(true);
    setTopMessage("");
    setAllTeams(null);
    setBaseTeams(null);
    setPage(0);
    const data = await api.post("/playoff/best-team", {
      team_slots: ids,
      n_sims: Number(sims || 200),
    });
    if (data.error) {
      setTopMessage(data.error);
      setTopTeams([]);
      setAllTeams([]);
      setBaseTeams([]);
    } else if (data.top_teams && data.top_teams.length > 0) {
      const all = data.all_teams || [];
      setBaseTeams(all);
      // apply filters after fetch
      const filtered = applyFilters(all, includeSet, excludeSet);
      if (filtered.length === 0) {
        setTopTeams([]);
        setAllTeams([]);
        setTopMessage("No teams after filters. Adjust include/exclude selections.");
      } else {
        const sorted = sortTeams(filtered, sortKey);
        setAllTeams(sorted);
        setTopTeams(sorted.slice(0, 10));
        setTopMessage("");
      }
    } else {
      setTopTeams([]);
      setAllTeams([]);
      setBaseTeams([]);
      setTopMessage("No valid teams found. Try lowering budget/max-per-team or adjusting bracket slots.");
    }
    setBusy(false);
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

  return (
    <Section title="Playoff Bracket (BO3)">
      <div className="grid two">
        {slots.map((val, idx) => (
          <Select
            key={idx}
            label={slotLabels[idx]}
            value={val}
            onChange={(v) => setSlot(idx, v)}
            options={[{ value: "", label: "Select team" }, ...teams.map((t) => ({ value: t.team_id, label: `${t.name} (${t.team_id})` }))]}
          />
        ))}
      </div>
      <div className="grid three">
        <Input label="# Sims" value={sims} onChange={setSims} />
        <div className="field">
          <span>Include/Exclude</span>
          <button className="secondary" onClick={() => setShowFilterModal(true)} disabled={slots.some((s) => !s)}>
            Select Players
          </button>
          <p className="muted">
            Include {includeSet.size} | Exclude {excludeSet.size}
          </p>
        </div>
      </div>
      <div className="grid two">
        <Select
          label="Sort by"
          value={sortKey}
          onChange={(v) => {
            setSortKey(v);
            if (allTeams) {
              const sorted = sortTeams(allTeams, v);
              setAllTeams(sorted);
              setTopTeams(sorted.slice(0, 10));
              setPage(0);
            }
          }}
          options={[
            { value: "ev_desc", label: "EV desc" },
            { value: "ev_asc", label: "EV asc" },
            { value: "cost_asc", label: "Cost asc" },
            { value: "cost_desc", label: "Cost desc" },
            { value: "cpp_desc", label: "Value (EV/Cost) desc" },
          ]}
        />
      </div>
      <div className="actions">
        <button className="primary" onClick={run} disabled={busy || slots.some((s) => !s)}>
          {busy ? "Running..." : "Run Playoff"}
        </button>
        <button className="primary" onClick={findTopTeams} disabled={busy || slots.some((s) => !s)}>
          {busy ? "Working..." : "Find Best 5 Team"}
        </button>
      </div>
      {busy && (
        <div className="card sub">
          <p className="muted">Simulating bracket and evaluating lineups...</p>
          <div className="progress">
            <div className="progress-bar" />
          </div>
        </div>
      )}
      {results && (
        <div className="stack">
          <div className="card sub">
            <h3>Quarterfinals</h3>
            {results.bracket.quarters.map((m, i) => (
              <p key={i}>
                {teamLookup[m.teams[0]]} vs {teamLookup[m.teams[1]]} — Winner: {teamLookup[m.winner]}
              </p>
            ))}
          </div>
          <div className="card sub">
            <h3>Semifinals</h3>
            {results.bracket.semis.map((m, i) => (
              <p key={i}>
                {teamLookup[m.teams[0]]} vs {teamLookup[m.teams[1]]} — Winner: {teamLookup[m.winner]}
              </p>
            ))}
          </div>
          <div className="card sub">
            <h3>Final</h3>
            {results.bracket.final.map((m, i) => (
              <p key={i}>
                {teamLookup[m.teams[0]]} vs {teamLookup[m.teams[1]]} — Winner: {teamLookup[m.winner]}
              </p>
            ))}
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
                        <td>{playerLookup[Number(pid)] || pid}</td>
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
      {topTeams && topTeams.length > 0 && (
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
                      <td>{p.name}</td>
                      <td>{teamLookup[p.team_id] || p.team_id}</td>
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
      {allTeams && allTeams.length > 0 && (
        <div className="card sub">
          <h3>All Teams ({allTeams.length})</h3>
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
              Page {page + 1} showing {Math.min(200, allTeams.length - page * 200)} of {allTeams.length}
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
                  <td>{team.players.map((p) => `${p.name} (${teamLookup[p.team_id] || p.team_id})`).join(", ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {topMessage && (
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
                ✕
              </button>
            </header>
            <div className="modal-body">
              <p className="muted">Choose from teams in the current bracket slots.</p>
              <div className="grid two">
                {slots
                  .map((s) => Number(s))
                  .filter(Boolean)
                  .map((tid) => {
                    const team = teams.find((t) => t.team_id === tid);
                    const roster = [team?.player1_id, team?.player2_id, team?.player3_id, team?.player4_id, team?.player5_id].filter(Boolean);
                    return (
                      <div key={tid} className="card sub">
                        <h4>{teamLookup[tid] || `Team ${tid}`}</h4>
                        {roster.map((pid) => (
                          <div key={pid} className="field" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <div style={{ flex: 1 }}>
                              {playerLookup[pid] || pid} <span className="muted">({pid})</span>
                            </div>
                            <label>
                              <input
                                type="checkbox"
                                checked={includeSet.has(pid)}
                                onChange={(e) => {
                                  setIncludeSet((prev) => {
                                    const next = new Set(prev);
                                    if (e.target.checked) next.add(pid);
                                    else next.delete(pid);
                                    return next;
                                  });
                                }}
                              />{" "}
                              Include
                            </label>
                            <label>
                              <input
                                type="checkbox"
                                checked={excludeSet.has(pid)}
                                onChange={(e) => {
                                  setExcludeSet((prev) => {
                                    const next = new Set(prev);
                                    if (e.target.checked) next.add(pid);
                                    else next.delete(pid);
                                    return next;
                                  });
                                }}
                              />{" "}
                              Exclude
                            </label>
                          </div>
                        ))}
                      </div>
                    );
                  })}
              </div>
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

function DatabaseTab({ players, teams, loading, error, refresh, notify }) {
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [showPlayerModal, setShowPlayerModal] = useState(false);
  const [playerTab, setPlayerTab] = useState("info");
  const [playerForm, setPlayerForm] = useState({
    player_id: "",
    name: "",
    rating: "",
    price: "",
    best_role: "",
    major_win_pct: "",
    minor_win_pct: "",
    boosters_json: "",
    roles_json: "",
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

  const [selectedTeam, setSelectedTeam] = useState(null);
  const [showTeamModal, setShowTeamModal] = useState(false);
  const [teamForm, setTeamForm] = useState({
    team_id: "",
    name: "",
    hltv_rank: "",
    vrs_rank: "",
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

        // Derive best role/major/minor if missing from DB but present in roles JSON
        let bestRole = p.best_role || "";
        let majorPct = p.major_win_pct || "";
        let minorPct = p.minor_win_pct || "";
        if (rolesObj && Object.keys(rolesObj).length > 0) {
          const best = Object.entries(rolesObj).reduce(
            (acc, [rid, vals]) => {
              const maj = Number(vals?.major ?? vals?.major_win_pct ?? 0);
              if (maj > acc.maxMajor) {
                return { role: rid, maxMajor: maj, minor: Number(vals?.minor ?? vals?.minor_win_pct ?? 0) };
              }
              return acc;
            },
            { role: bestRole || "", maxMajor: Number(majorPct || 0), minor: Number(minorPct || 0) }
          );
          bestRole = best.role;
          majorPct = best.maxMajor || "";
          minorPct = best.minor || "";
        }

        setPlayerForm({
          player_id: p.player_id,
          name: p.name || "",
          rating: p.rating || "",
          price: p.price || "",
          best_role: bestRole,
          major_win_pct: majorPct,
          minor_win_pct: minorPct,
          boosters_json: p.boosters_json || "",
          roles_json: p.roles_json || "",
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
        setPlayerTab("info");
      }
    }
  }, [selectedPlayer, players]);

  useEffect(() => {
    if (selectedTeam) {
      const t = teams.find((x) => x.team_id === selectedTeam);
      if (t) {
        setTeamForm({
          team_id: t.team_id,
          name: t.name || "",
          hltv_rank: t.hltv_rank || "",
          vrs_rank: t.vrs_rank || "",
          win_rate: t.win_rate || "",
          p1: t.player1_id || "",
          p2: t.player2_id || "",
          p3: t.player3_id || "",
          p4: t.player4_id || "",
          p5: t.player5_id || "",
        });
      }
    }
  }, [selectedTeam, teams]);

  const savePlayer = async () => {
    if (!playerForm.player_id || !playerForm.name) return;
    const num = (v) => (v === "" ? undefined : Number(v));
    await api.post("/players/", {
      player_id: Number(playerForm.player_id),
      name: playerForm.name,
      rating: num(playerForm.rating),
      price: playerForm.price === "" ? undefined : Number(playerForm.price),
      best_role: playerForm.best_role,
      major_win_pct: num(playerForm.major_win_pct),
      minor_win_pct: num(playerForm.minor_win_pct),
      boosters_json: JSON.stringify(boosterForm || {}),
      roles_json: JSON.stringify(roleForm || {}),
      rating_top5: num(playerForm.rating_top5),
      maps_top5: num(playerForm.maps_top5),
      rating_top10: num(playerForm.rating_top10),
      maps_top10: num(playerForm.maps_top10),
      rating_top20: num(playerForm.rating_top20),
      maps_top20: num(playerForm.maps_top20),
      rating_top30: num(playerForm.rating_top30),
      maps_top30: num(playerForm.maps_top30),
      rating_top50: num(playerForm.rating_top50),
      maps_top50: num(playerForm.maps_top50),
    });
    notify("Player saved");
    refresh();
    setShowPlayerModal(false);
  };

  const deletePlayer = async () => {
    if (!selectedPlayer) return;
    await api.delete(`/players/${selectedPlayer}`);
    notify("Player deleted");
    setSelectedPlayer(null);
    setPlayerForm({
      player_id: "",
      name: "",
      rating: "",
      price: "",
      best_role: "",
      major_win_pct: "",
      minor_win_pct: "",
      boosters_json: "",
      roles_json: "",
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
    refresh();
    setShowPlayerModal(false);
  };

  const saveTeam = async () => {
    if (!teamForm.name) return;
    const ids = [teamForm.p1, teamForm.p2, teamForm.p3, teamForm.p4, teamForm.p5].map((x) => Number(x || 0));
    await api.post("/teams/", {
      name: teamForm.name,
      hltv_rank: teamForm.hltv_rank === "" ? undefined : Number(teamForm.hltv_rank),
      vrs_rank: teamForm.vrs_rank === "" ? undefined : Number(teamForm.vrs_rank),
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
      vrs_rank: "",
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

  const newPlayer = () => {
    setSelectedPlayer(null);
    setPlayerForm({
      player_id: "",
      name: "",
      rating: "",
      price: "",
      best_role: "",
      major_win_pct: "",
      minor_win_pct: "",
      boosters_json: "",
      roles_json: "",
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
    setBoosterForm({});
    setRoleForm({});
    setShowPlayerModal(true);
  };

  const newTeam = () => {
    setSelectedTeam(null);
    setTeamForm({
      team_id: "",
      name: "",
      hltv_rank: "",
      vrs_rank: "",
      win_rate: "",
      p1: "",
      p2: "",
      p3: "",
      p4: "",
      p5: "",
    });
    setShowTeamModal(true);
  };

  return (
    <div className="grid two">
      <Section title="Players">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <>
            <p className="muted">Click a player row to view/edit details.</p>
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Name</th>
                  <th>Rating</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {players.map((p) => (
                  <tr
                    key={p.player_id}
                    className={selectedPlayer === p.player_id ? "row-active" : ""}
                    onClick={() => {
                      setSelectedPlayer(p.player_id);
                      setShowPlayerModal(true);
                    }}
                  >
                    <td>{p.player_id}</td>
                    <td>{p.name}</td>
                    <td>{Number(p.rating || 0).toFixed(2)}</td>
                    <td>{p.price}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
        <div className="card sub" style={{ display: "none" }}>
          <div className="actions">
            <button className="primary" onClick={newPlayer}>
              New Player
            </button>
          </div>
          <div className="grid two">
            <Input label="Player ID" value={playerForm.player_id} onChange={(v) => setPlayerForm((p) => ({ ...p, player_id: v }))} />
            <Input label="Name" value={playerForm.name} onChange={(v) => setPlayerForm((p) => ({ ...p, name: v }))} />
            <Input label="Rating" value={playerForm.rating} onChange={(v) => setPlayerForm((p) => ({ ...p, rating: v }))} />
            <Input label="Price" value={playerForm.price} onChange={(v) => setPlayerForm((p) => ({ ...p, price: v }))} />
            <Input label="Best Role" value={playerForm.best_role} onChange={(v) => setPlayerForm((p) => ({ ...p, best_role: v }))} />
            <Input label="Major Win %" value={playerForm.major_win_pct} onChange={(v) => setPlayerForm((p) => ({ ...p, major_win_pct: v }))} />
            <Input label="Minor Win %" value={playerForm.minor_win_pct} onChange={(v) => setPlayerForm((p) => ({ ...p, minor_win_pct: v }))} />
          </div>
          <div className="grid three">
            <Input label="Rating Top5" value={playerForm.rating_top5} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top5: v }))} />
            <Input label="Maps Top5" value={playerForm.maps_top5} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top5: v }))} />
            <Input label="Rating Top10" value={playerForm.rating_top10} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top10: v }))} />
            <Input label="Maps Top10" value={playerForm.maps_top10} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top10: v }))} />
            <Input label="Rating Top20" value={playerForm.rating_top20} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top20: v }))} />
            <Input label="Maps Top20" value={playerForm.maps_top20} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top20: v }))} />
            <Input label="Rating Top30" value={playerForm.rating_top30} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top30: v }))} />
            <Input label="Maps Top30" value={playerForm.maps_top30} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top30: v }))} />
            <Input label="Rating Top50" value={playerForm.rating_top50} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top50: v }))} />
            <Input label="Maps Top50" value={playerForm.maps_top50} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top50: v }))} />
          </div>
          <div className="grid two">
            <label className="field">
              <span>Boosters JSON</span>
              <textarea
                value={playerForm.boosters_json}
                onChange={(e) => setPlayerForm((p) => ({ ...p, boosters_json: e.target.value }))}
                rows={3}
              />
            </label>
            <label className="field">
              <span>Roles JSON</span>
              <textarea
                value={playerForm.roles_json}
                onChange={(e) => setPlayerForm((p) => ({ ...p, roles_json: e.target.value }))}
                rows={3}
              />
            </label>
          </div>
          <div className="actions">
            <button className="primary" onClick={savePlayer} disabled={!playerForm.player_id || !playerForm.name}>
              Save Player
            </button>
            <button className="danger" onClick={deletePlayer} disabled={!selectedPlayer}>
              Delete Player
            </button>
          </div>
        </div>
      </Section>

      {showPlayerModal && (
        <div className="modal-backdrop" onClick={() => setShowPlayerModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>{selectedPlayer ? "Edit Player" : "New Player"}</h3>
              <button className="close" onClick={() => setShowPlayerModal(false)}>
                ✕
              </button>
            </header>
            <div className="modal-body">
              <div className="grid two">
                <Input label="Player ID" value={playerForm.player_id} onChange={(v) => setPlayerForm((p) => ({ ...p, player_id: v }))} />
                <Input label="Name" value={playerForm.name} onChange={(v) => setPlayerForm((p) => ({ ...p, name: v }))} />
                <Input label="Rating" value={playerForm.rating} onChange={(v) => setPlayerForm((p) => ({ ...p, rating: v }))} />
                <Input label="Price" value={playerForm.price} onChange={(v) => setPlayerForm((p) => ({ ...p, price: v }))} />
                <Input label="Best Role" value={playerForm.best_role} onChange={(v) => setPlayerForm((p) => ({ ...p, best_role: v }))} />
                <Input label="Major Win %" value={playerForm.major_win_pct} onChange={(v) => setPlayerForm((p) => ({ ...p, major_win_pct: v }))} />
                <Input label="Minor Win %" value={playerForm.minor_win_pct} onChange={(v) => setPlayerForm((p) => ({ ...p, minor_win_pct: v }))} />
              </div>
              <div className="tab-bar small">
                <button className={playerTab === "info" ? "tab active" : "tab"} onClick={() => setPlayerTab("info")}>
                  Ratings
                </button>
                <button className={playerTab === "boosters" ? "tab active" : "tab"} onClick={() => setPlayerTab("boosters")}>
                  Boosters
                </button>
                <button className={playerTab === "roles" ? "tab active" : "tab"} onClick={() => setPlayerTab("roles")}>
                  Roles
                </button>
              </div>
              {playerTab === "info" && (
                <div className="grid three">
                  <Input label="Rating Top5" value={playerForm.rating_top5} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top5: v }))} />
                  <Input label="Maps Top5" value={playerForm.maps_top5} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top5: v }))} />
                  <Input label="Rating Top10" value={playerForm.rating_top10} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top10: v }))} />
                  <Input label="Maps Top10" value={playerForm.maps_top10} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top10: v }))} />
                  <Input label="Rating Top20" value={playerForm.rating_top20} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top20: v }))} />
                  <Input label="Maps Top20" value={playerForm.maps_top20} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top20: v }))} />
                  <Input label="Rating Top30" value={playerForm.rating_top30} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top30: v }))} />
                  <Input label="Maps Top30" value={playerForm.maps_top30} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top30: v }))} />
                  <Input label="Rating Top50" value={playerForm.rating_top50} onChange={(v) => setPlayerForm((p) => ({ ...p, rating_top50: v }))} />
                  <Input label="Maps Top50" value={playerForm.maps_top50} onChange={(v) => setPlayerForm((p) => ({ ...p, maps_top50: v }))} />
                </div>
              )}
              {playerTab === "boosters" && (
                <div className="grid two">
                  {Object.entries(boosterNames).map(([id, label]) => (
                    <Input
                      key={id}
                      label={`${label} (id ${id})`}
                      value={boosterForm[id] ?? ""}
                      onChange={(v) => setBoosterForm((prev) => ({ ...prev, [id]: v }))}
                    />
                  ))}
                </div>
              )}
              {playerTab === "roles" && (
                <div className="grid two">
                  {Object.entries(roleNames).map(([id, label]) => (
                    <div key={id} className="stack">
                      <Input
                        label={`${label} (id ${id}) - Major %`}
                        value={roleForm[id]?.major ?? roleForm[id]?.major_win_pct ?? ""}
                        onChange={(v) =>
                          setRoleForm((prev) => ({
                            ...prev,
                            [id]: { ...(prev[id] || {}), major: v },
                          }))
                        }
                      />
                      <Input
                        label={`${label} (id ${id}) - Minor %`}
                        value={roleForm[id]?.minor ?? roleForm[id]?.minor_win_pct ?? ""}
                        onChange={(v) =>
                          setRoleForm((prev) => ({
                            ...prev,
                            [id]: { ...(prev[id] || {}), minor: v },
                          }))
                        }
                      />
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="actions">
              <button className="primary" onClick={savePlayer} disabled={!playerForm.player_id || !playerForm.name}>
                Save Player
              </button>
              <button className="danger" onClick={deletePlayer} disabled={!selectedPlayer}>
                Delete Player
              </button>
              <button className="secondary" onClick={() => setShowPlayerModal(false)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {showTeamModal && (
        <div className="modal-backdrop" onClick={() => setShowTeamModal(false)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <header className="modal-header">
              <h3>{selectedTeam ? "Edit Team" : "New Team"}</h3>
              <button className="close" onClick={() => setShowTeamModal(false)}>
                ✕
              </button>
            </header>
            <div className="modal-body">
              <div className="grid two">
                <Input label="Name" value={teamForm.name} onChange={(v) => setTeamForm((p) => ({ ...p, name: v }))} />
                <Input label="HLTV Rank" value={teamForm.hltv_rank} onChange={(v) => setTeamForm((p) => ({ ...p, hltv_rank: v }))} />
                <Input label="VRS Rank" value={teamForm.vrs_rank} onChange={(v) => setTeamForm((p) => ({ ...p, vrs_rank: v }))} />
                <Input label="Win Rate" value={teamForm.win_rate} onChange={(v) => setTeamForm((p) => ({ ...p, win_rate: v }))} />
                <Input label="Player 1 ID" value={teamForm.p1} onChange={(v) => setTeamForm((p) => ({ ...p, p1: v }))} />
                <Input label="Player 2 ID" value={teamForm.p2} onChange={(v) => setTeamForm((p) => ({ ...p, p2: v }))} />
                <Input label="Player 3 ID" value={teamForm.p3} onChange={(v) => setTeamForm((p) => ({ ...p, p3: v }))} />
                <Input label="Player 4 ID" value={teamForm.p4} onChange={(v) => setTeamForm((p) => ({ ...p, p4: v }))} />
                <Input label="Player 5 ID" value={teamForm.p5} onChange={(v) => setTeamForm((p) => ({ ...p, p5: v }))} />
              </div>
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

      <Section title="Teams">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <table>
            <thead>
              <tr>
                <th>ID</th>
                <th>Name</th>
                <th>HLTV Rank</th>
                <th>VRS Rank</th>
                <th>Players</th>
              </tr>
            </thead>
            <tbody>
                {teams.map((t) => (
                  <tr
                    key={t.team_id}
                    className={selectedTeam === t.team_id ? "row-active" : ""}
                    onClick={() => {
                      setSelectedTeam(t.team_id);
                      setShowTeamModal(true);
                    }}
                  >
                  <td>{t.team_id}</td>
                  <td>{t.name}</td>
                  <td>{t.hltv_rank}</td>
                  <td>{t.vrs_rank}</td>
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
        )}
        <div className="card sub" style={{ display: "none" }}>
          <div className="actions">
            <button className="primary" onClick={newTeam}>
              New Team
            </button>
          </div>
          <div className="grid two">
            <Input label="Name" value={teamForm.name} onChange={(v) => setTeamForm((p) => ({ ...p, name: v }))} />
            <Input label="HLTV Rank" value={teamForm.hltv_rank} onChange={(v) => setTeamForm((p) => ({ ...p, hltv_rank: v }))} />
            <Input label="VRS Rank" value={teamForm.vrs_rank} onChange={(v) => setTeamForm((p) => ({ ...p, vrs_rank: v }))} />
            <Input label="Win Rate" value={teamForm.win_rate} onChange={(v) => setTeamForm((p) => ({ ...p, win_rate: v }))} />
            <Input label="Player 1 ID" value={teamForm.p1} onChange={(v) => setTeamForm((p) => ({ ...p, p1: v }))} />
            <Input label="Player 2 ID" value={teamForm.p2} onChange={(v) => setTeamForm((p) => ({ ...p, p2: v }))} />
            <Input label="Player 3 ID" value={teamForm.p3} onChange={(v) => setTeamForm((p) => ({ ...p, p3: v }))} />
            <Input label="Player 4 ID" value={teamForm.p4} onChange={(v) => setTeamForm((p) => ({ ...p, p4: v }))} />
            <Input label="Player 5 ID" value={teamForm.p5} onChange={(v) => setTeamForm((p) => ({ ...p, p5: v }))} />
          </div>
          <div className="actions">
            <button className="primary" onClick={saveTeam} disabled={!teamForm.name}>
              Save Team
            </button>
            <button className="danger" onClick={deleteTeam} disabled={!selectedTeam}>
              Delete Team
            </button>
          </div>
        </div>
      </Section>

    </div>
  );
}

function AdminTab({ refresh, notify }) {
  const [triggerJson, setTriggerJson] = useState("");
  const [eventId, setEventId] = useState("");
  const [importResult, setImportResult] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [eventBusy, setEventBusy] = useState(false);
  const [wipeBusy, setWipeBusy] = useState(false);
  const [topPlayerId, setTopPlayerId] = useState("");
  const [topText, setTopText] = useState("");
  const [topBusy, setTopBusy] = useState(false);
  const [fitBusy, setFitBusy] = useState(false);
  const [fitRows, setFitRows] = useState([{ rankA: "", oddsA: "", rankB: "", oddsB: "" }]);

  const importTriggers = async () => {
    if (!triggerJson.trim()) {
      setImportResult("Paste triggerRates JSON first.");
      return;
    }
    setImportBusy(true);
    setImportResult("");
    try {
      const res = await api.post("/admin/import-trigger-rates", { trigger_json: triggerJson });
      const msg = `Updated players: ${res.updated_players ?? 0}`;
      setImportResult(msg);
      notify("Trigger rates imported");
      refresh();
    } finally {
      setImportBusy(false);
    }
  };

  const importByEvent = async () => {
    if (!eventId.trim()) {
      setImportResult("Enter an event id first.");
      return;
    }
    setEventBusy(true);
    setImportResult("");
    try {
      const res = await api.post("/admin/import-hltv-event", { event_id: eventId.trim() });
      const msg = `Imported players: ${res.imported_players ?? 0}, teams: ${res.imported_teams ?? 0}`;
      setImportResult(msg);
      notify("Fantasy import complete");
      refresh();
    } finally {
      setEventBusy(false);
    }
  };

  const wipeDb = async () => {
    setWipeBusy(true);
    await api.post("/admin/wipe", {});
    notify("Database wiped");
    setWipeBusy(false);
    refresh();
  };

  const importTopRatings = async () => {
    if (!topPlayerId.trim() || !topText.trim()) {
      setImportResult("Enter player id and paste the top-X text.");
      return;
    }
    setTopBusy(true);
    setImportResult("");
    try {
      const res = await api.post("/admin/import-top-ratings", { player_id: topPlayerId.trim(), text: topText });
      const msg = `Updated: ${res.updated_fields?.join(", ") || "none"}`;
      setImportResult(msg);
      notify("Top ratings imported");
      refresh();
    } finally {
      setTopBusy(false);
    }
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
      <Section title="Admin Tools">
        <div className="grid two">
          <label className="field">
            <span>Trigger Rates JSON (playerTriggerRates)</span>
            <textarea
              rows={12}
              value={triggerJson}
              onChange={(e) => setTriggerJson(e.target.value)}
              placeholder="Paste the triggerRates JSON here"
            />
          </label>
          <div className="stack">
            <p className="muted">Paste the triggerRates JSON to update boosters and roles for all players.</p>
            <button className="primary" onClick={importTriggers} disabled={importBusy}>
              {importBusy ? "Importing..." : "Import Trigger Rates"}
            </button>
            <hr />
            <Input label="HLTV Event ID" value={eventId} onChange={setEventId} placeholder="e.g. 12345" />
            <button className="primary" onClick={importByEvent} disabled={eventBusy}>
              {eventBusy ? "Importing..." : "Import by Event ID"}
            </button>
            <hr />
            <Input label="Player ID for Top Ratings" value={topPlayerId} onChange={setTopPlayerId} placeholder="player id" />
            <label className="field">
              <span>Top opponents text (e.g. '0.88 vs top 5 opponents (37 maps)')</span>
              <textarea
                rows={8}
                value={topText}
                onChange={(e) => setTopText(e.target.value)}
                placeholder="Paste the top 5/10/20/30/50 block here"
              />
            </label>
            <button className="primary" onClick={importTopRatings} disabled={topBusy}>
              {topBusy ? "Importing..." : "Import Top Ratings"}
            </button>
            <hr />
            <div className="card sub">
              <h4>Winrate Fit Samples</h4>
              <p className="muted">Enter rank and odds per row. oddsA is used to imply P(A)=1/oddsA.</p>
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
            <button className="primary" onClick={fitWinrate} disabled={fitBusy}>
              {fitBusy ? "Fitting..." : "Fit Winrate Params"}
            </button>
            {importResult && <p className="muted">{importResult}</p>}
            <hr />
            <button className="danger" onClick={wipeDb} disabled={wipeBusy}>
              {wipeBusy ? "Wiping..." : "Wipe Database"}
            </button>
            <p className="muted">Deletes all players and teams (schema is kept).</p>
          </div>
        </div>
      </Section>
    </div>
  );
}

export default function App() {
  const [active, setActive] = useState("view");
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
    view: <DatabaseTab players={players} teams={teams} loading={loading} error={error} refresh={load} notify={notify} />,
    sim: <SimulationTab teams={teams} teamLookup={teamLookup} />,
    bracket: <BracketTab teams={teams} teamLookup={teamLookup} />,
    playoff: <PlayoffTab teams={teams} teamLookup={teamLookup} players={players} sortTeams={sortTeams} applyFilters={applyFilters} />,
    admin: <AdminTab refresh={load} notify={notify} />,
  };

  return (
    <div className="layout">
      <header className="hero">
        <div>
          <p className="eyebrow">CS Fantasy Toolkit</p>
          <h1>Electron + FastAPI</h1>
          <p className="muted">Modern UI for the existing Swiss simulation and bracket tools.</p>
        </div>
        <div className="pills">
          <Pill>FastAPI</Pill>
          <Pill>Electron</Pill>
          <Pill>React</Pill>
        </div>
      </header>

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
