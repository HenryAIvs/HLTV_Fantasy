import React, { useEffect, useMemo, useRef, useState } from "react";

const tabs = [
  { key: "view", label: "Database" },
  { key: "events", label: "Events" },
  { key: "sim", label: "Swiss Group Stage" },
  { key: "playoff", label: "Playoff Bracket" },
  { key: "admin", label: "Data Management" },
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
                          {playerLookup[Number(pid)] || pid}
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

function TopTeamsTab({ teamLookup, selected, bo, sims, results }) {
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
                        <td>{p.name}</td>
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

function PlayoffTab({ teams, teamLookup, players, sortTeams, applyFilters }) {
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
                      <td>{p.name}</td>
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
                ✕
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

  const hasTopTierRatings = (player) => {
    if (!player) return false;
    const tiers = [5, 10, 20, 30, 50];
    return tiers.every((tier) => {
      const rating = Number(player[`rating_top${tier}`]);
      const maps = Number(player[`maps_top${tier}`]);
      return Number.isFinite(rating) && Number.isFinite(maps) && maps > 0;
    });
  };

  const isPlayerComplete = (playerId) => {
    const p = playerById[Number(playerId)];
    if (!p) return false;
    const hasCore = typeof p.name === "string" && p.name.trim().length > 0 && Number.isFinite(Number(p.rating));
    const hasRolesAndBoosters = hasJsonEntries(p.boosters_json) && hasJsonEntries(p.roles_json);
    return hasCore && hasRolesAndBoosters && hasTopTierRatings(p);
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

  const fetchTopRatingsForEventPlayers = async () => {
    const ids = Array.from(
      new Set(
        (selectedEvent?.players || [])
          .map((p) => Number(p.player_id))
          .filter((x) => Number.isFinite(x) && x > 0)
      )
    );
    if (ids.length === 0) {
      setMessage("No players found in selected event.");
      return;
    }

    setBusy(true);
    setMessage("");
    try {
      const res = await api.post("/players/fetch-top-ratings-batch", {
        player_ids: ids,
        headless: true,
        min_delay_seconds: 10,
        max_delay_seconds: 18,
        retries: 2,
        retry_backoff_seconds: 25,
      });
      if (res?.detail) {
        setMessage(String(res.detail));
        return;
      }

      const ok = Number(res.ok || 0);
      const failed = Number(res.failed || 0);
      setMessage(`Top-X batch complete for event ${selectedEvent?.event_id}: ${ok} succeeded, ${failed} failed.`);
      notify(`Top-X batch done: ${ok} ok, ${failed} failed`);

      await refreshData();
      if (selectedEventId) {
        await loadEventDetail(selectedEventId);
      }
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
          <p className="muted">
            Import an HLTV event id to store event teams and event-specific player prices. The active event price is used across simulations.
          </p>
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
            <button
              className="primary"
              onClick={fetchTopRatingsForEventPlayers}
              disabled={busy || !selectedEvent || (selectedEvent?.players || []).length === 0}
            >
              {busy ? "Working..." : "Fetch Top X For Event Players"}
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

function DatabaseTab({ players, teams, loading, error, refresh, notify }) {
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
  const [topFetchBusyByPlayer, setTopFetchBusyByPlayer] = useState({});

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

  const fetchTopRatingsForPlayer = async (playerId, playerName) => {
    setTopFetchBusyByPlayer((prev) => ({ ...prev, [playerId]: true }));
    try {
      const res = await api.post(`/players/${playerId}/fetch-top-ratings`, {});
      if (res?.detail) {
        notify(`Top X fetch failed for ${playerName || `player ${playerId}`}: ${res.detail}`);
        return;
      }
      await refresh();
      notify(`Top X ratings fetched for ${playerName || `player ${playerId}`}`);
    } catch (e) {
      notify(`Top X fetch failed for ${playerName || `player ${playerId}`}`);
    } finally {
      setTopFetchBusyByPlayer((prev) => ({ ...prev, [playerId]: false }));
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

  const hasTopXRatings = (player) => {
    const tiers = [5, 10, 20, 30, 50];
    return tiers.some((tier) => {
      const rating = player[`rating_top${tier}`];
      const maps = player[`maps_top${tier}`];
      return rating !== null && rating !== undefined && rating !== "" && maps !== null && maps !== undefined && maps !== "";
    });
  };

  const hasBoostersAndRoles = (player) => hasJsonEntries(player.boosters_json) && hasJsonEntries(player.roles_json);
  const toNum = (v, fallback = 0) => {
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
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
        case "price_desc":
          return toNum(b.price) - toNum(a.price);
        case "price_asc":
          return toNum(a.price) - toNum(b.price);
        case "team_asc":
          return ((playerTeamLookup[a.player_id] || [])[0] || "").localeCompare(((playerTeamLookup[b.player_id] || [])[0] || ""));
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
      </div>

      {dbTab === "players" && <Section title="Players">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <>
            <p className="muted">Click a player row to view/edit details.</p>
            <div className="grid two">
              <Input label="Search Players" value={playerSearch} onChange={setPlayerSearch} placeholder="Name, ID, or team" />
              <Select
                label="Sort Players"
                value={playerSort}
                onChange={setPlayerSort}
                options={[
                  { value: "name_asc", label: "Name A-Z" },
                  { value: "name_desc", label: "Name Z-A" },
                  { value: "id_asc", label: "ID low-high" },
                  { value: "id_desc", label: "ID high-low" },
                  { value: "rating_desc", label: "Rating high-low" },
                  { value: "rating_asc", label: "Rating low-high" },
                  { value: "price_desc", label: "Price high-low" },
                  { value: "price_asc", label: "Price low-high" },
                  { value: "team_asc", label: "Team A-Z" },
                  { value: "boost_desc", label: "Boost/Role imported first" },
                ]}
              />
            </div>
            <p className="muted status-legend">
              <span>
                <span className="status-dot ok" /> imported
              </span>
              <span>
                <span className="status-dot missing" /> missing
              </span>
            </p>
            <table>
              <colgroup>
                <col style={{ width: "12%" }} />
                <col style={{ width: "20%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "22%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Team</th>
                  <th>Rating</th>
                  <th>Price</th>
                  <th title="Boosters and Roles imported">Boost/Role</th>
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
                    <td>{p.price}</td>
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
      </Section>}

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
                <div className="stack">
                  <div className="actions" style={{ marginTop: 0 }}>
                    <button
                      className="primary"
                      onClick={() => fetchTopRatingsForPlayer(Number(playerForm.player_id), playerForm.name)}
                      disabled={!playerForm.player_id || Boolean(topFetchBusyByPlayer[Number(playerForm.player_id)])}
                    >
                      {topFetchBusyByPlayer[Number(playerForm.player_id)] ? "Fetching..." : "Fetch Top X"}
                    </button>
                    <span className="muted" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <span
                        className={hasTopXRatings(playerForm) ? "status-dot ok" : "status-dot missing"}
                        title={hasTopXRatings(playerForm) ? "Top X ratings imported" : "Top X ratings missing"}
                      />
                      {hasTopXRatings(playerForm) ? "Top X ratings imported" : "Top X ratings missing"}
                    </span>
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

      {dbTab === "teams" && <Section title="Teams">
        {loading ? (
          <p>Loading...</p>
        ) : error ? (
          <p className="error">{error}</p>
        ) : (
          <>
            <div className="grid two">
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
            <table>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>HLTV Rank</th>
                  <th>VRS Rank</th>
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
          </>
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
      </Section>}

    </div>
  );
}

function AdminTab({ refresh, notify, teams, players }) {
  const [dataTab, setDataTab] = useState("trigger");
  const [triggerJson, setTriggerJson] = useState("");
  const [triggerUpdatedPlayers, setTriggerUpdatedPlayers] = useState([]);
  const [importResult, setImportResult] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [wipeBusy, setWipeBusy] = useState(false);
  const [selectedTopTeamIds, setSelectedTopTeamIds] = useState([]);
  const [topTeamIndex, setTopTeamIndex] = useState(0);
  const [topRatingsByPlayer, setTopRatingsByPlayer] = useState({});
  const [topBusy, setTopBusy] = useState(false);
  const [fitBusy, setFitBusy] = useState(false);
  const [fitRows, setFitRows] = useState([{ rankA: "", oddsA: "", rankB: "", oddsB: "" }]);
  const playerNameLookup = useMemo(() => {
    const map = {};
    players.forEach((p) => (map[p.player_id] = p.name));
    return map;
  }, [players]);
  const selectedTopTeams = useMemo(() => {
    const selected = teams.filter((t) => selectedTopTeamIds.includes(t.team_id));
    return selected.sort((a, b) => String(a.name || "").localeCompare(String(b.name || "")));
  }, [teams, selectedTopTeamIds]);
  const currentTopTeam = selectedTopTeams[topTeamIndex] || null;
  const currentTopRoster = currentTopTeam
    ? [currentTopTeam.player1_id, currentTopTeam.player2_id, currentTopTeam.player3_id, currentTopTeam.player4_id, currentTopTeam.player5_id]
        .filter(Boolean)
    : [];

  useEffect(() => {
    if (topTeamIndex >= selectedTopTeams.length) {
      setTopTeamIndex(Math.max(0, selectedTopTeams.length - 1));
    }
  }, [topTeamIndex, selectedTopTeams.length]);

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

  const toggleTopTeam = (teamId) => {
    setSelectedTopTeamIds((prev) =>
      prev.includes(teamId) ? prev.filter((x) => x !== teamId) : [...prev, teamId]
    );
  };

  const selectAllTopTeams = () => {
    setSelectedTopTeamIds(teams.map((t) => t.team_id));
    setTopTeamIndex(0);
  };

  const clearTopTeams = () => {
    setSelectedTopTeamIds([]);
    setTopTeamIndex(0);
  };

  const importTopRatingsForCurrentTeam = async () => {
    if (!currentTopTeam) {
      setImportResult("Select teams first.");
      return;
    }
    setTopBusy(true);
    setImportResult("");
    let updatedPlayers = 0;
    const errors = [];
    try {
      for (const pid of currentTopRoster) {
        const text = (topRatingsByPlayer[pid] || "").trim();
        if (!text) continue;
        const res = await api.post("/admin/import-top-ratings", { player_id: pid, text });
        if (res?.updated_fields?.length) updatedPlayers += 1;
      }

      if (updatedPlayers === 0) {
        setImportResult(`No player fields were filled for ${currentTopTeam.name}.`);
      } else {
        setImportResult(`Imported top ratings for ${updatedPlayers} player(s) on ${currentTopTeam.name}.`);
      }

      const nextIndex = topTeamIndex + 1;
      if (nextIndex < selectedTopTeams.length) {
        setTopTeamIndex(nextIndex);
      } else {
        notify("Top ratings import workflow complete");
      }
      refresh();
    } catch (e) {
      errors.push("One or more player imports failed.");
      setImportResult(errors.join(" "));
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
      <Section title="Data Management Tools">
        <div className="tab-bar small">
          <button className={dataTab === "trigger" ? "tab active" : "tab"} onClick={() => setDataTab("trigger")}>
            Trigger Rates
          </button>
          <button className={dataTab === "top" ? "tab active" : "tab"} onClick={() => setDataTab("top")}>
            Top Ratings
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
            <p className="muted">Paste the triggerRates JSON to update boosters and roles for all players.</p>
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

        {dataTab === "top" && (
          <div className="stack">
            <p className="muted">Select teams, then import top-X ratings team-by-team using five player fields.</p>
            <div className="actions" style={{ marginTop: 0 }}>
              <button className="secondary" onClick={selectAllTopTeams}>
                Select All Teams
              </button>
              <button className="secondary" onClick={clearTopTeams} disabled={selectedTopTeamIds.length === 0}>
                Clear Teams
              </button>
            </div>
            <div className="chips">
              {teams.map((t) => (
                <button
                  key={t.team_id}
                  className={selectedTopTeamIds.includes(t.team_id) ? "chip active" : "chip"}
                  onClick={() => toggleTopTeam(t.team_id)}
                >
                  {t.name} <Badge>id {t.team_id}</Badge>
                </button>
              ))}
            </div>

            {selectedTopTeams.length > 0 && currentTopTeam && (
              <div className="card sub">
                <h4>
                  Team {topTeamIndex + 1} / {selectedTopTeams.length}: {currentTopTeam.name}
                </h4>
                {currentTopRoster.map((pid, idx) => (
                  <label key={pid} className="field">
                    <span>
                      Player {idx + 1}: {playerNameLookup[pid] || `Player ${pid}`} (ID {pid})
                    </span>
                    <textarea
                      rows={6}
                      value={topRatingsByPlayer[pid] || ""}
                      onChange={(e) =>
                        setTopRatingsByPlayer((prev) => ({
                          ...prev,
                          [pid]: e.target.value,
                        }))
                      }
                      placeholder="Paste the top 5/10/20/30/50 block here"
                    />
                  </label>
                ))}
                <div className="actions">
                  <button
                    className="secondary"
                    onClick={() => setTopTeamIndex((i) => Math.max(0, i - 1))}
                    disabled={topTeamIndex === 0 || topBusy}
                  >
                    Previous Team
                  </button>
                  <button className="primary" onClick={importTopRatingsForCurrentTeam} disabled={topBusy}>
                    {topBusy ? "Importing..." : topTeamIndex + 1 < selectedTopTeams.length ? "Save & Next Team" : "Save Final Team"}
                  </button>
                  <button
                    className="secondary"
                    onClick={() => setTopTeamIndex((i) => Math.min(selectedTopTeams.length - 1, i + 1))}
                    disabled={topTeamIndex >= selectedTopTeams.length - 1 || topBusy}
                  >
                    Next Team
                  </button>
                </div>
              </div>
            )}

            {selectedTopTeams.length === 0 && (
              <p className="muted">No teams selected.</p>
            )}
            <div className="actions">
              <p className="muted">Tip: You can leave a player field blank to skip that player.</p>
            </div>
          </div>
        )}

        {dataTab === "fit" && (
          <div className="stack">
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
  const [rating, setRating] = useState("1.05");
  const [majorPct, setMajorPct] = useState("0.30");
  const [minorPct, setMinorPct] = useState("0.20");
  const [winProb, setWinProb] = useState("0.50");
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

      const payload = {
        rating: Number(rating),
        major_pct: Number(majorPct),
        minor_pct: Number(minorPct),
        win_prob: Number(winProb),
        booster_rates: rates,
        matches: Number(matches),
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
        <div className="grid three">
          <Input label="Rating" value={rating} onChange={setRating} />
          <Input label="Role Major %" value={majorPct} onChange={setMajorPct} />
          <Input label="Role Minor %" value={minorPct} onChange={setMinorPct} />
          <Input label="Win Probability (0-1)" value={winProb} onChange={setWinProb} />
          <Input label="# Matches" value={matches} onChange={setMatches} />
          <Input label="Expected Games (optional)" value={expectedGames} onChange={setExpectedGames} />
        </div>
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

function SwissTab({ teams, teamLookup }) {
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
        />
      )}
      {swissTab === "top5" && (
        <TopTeamsTab teamLookup={teamLookup} selected={selectedTeamIds} bo={boMode} sims={simCount} results={simResults} />
      )}
      {swissTab === "single" && <BracketTab teams={filteredTeams} teamLookup={teamLookup} />}
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
    events: <EventsTab refreshData={load} notify={notify} players={players} />,
    sim: <SwissTab teams={teams} teamLookup={teamLookup} />,
    playoff: <PlayoffTab teams={teams} teamLookup={teamLookup} players={players} sortTeams={sortTeams} applyFilters={applyFilters} />,
    admin: <AdminTab refresh={load} notify={notify} teams={teams} players={players} />,
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
