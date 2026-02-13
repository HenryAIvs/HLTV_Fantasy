import React, { useEffect, useMemo, useRef, useState } from "react";

const tabs = [
  { key: "view", label: "Database" },
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
    setTotalSims(Number(sims || 0));
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
        case "topx_desc":
          return Number(hasTopXRatings(b)) - Number(hasTopXRatings(a));
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
                  { value: "topx_desc", label: "Top X imported first" },
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
                <col style={{ width: "10%" }} />
                <col style={{ width: "12%" }} />
              </colgroup>
              <thead>
                <tr>
                  <th>Name</th>
                  <th>ID</th>
                  <th>Team</th>
                  <th>Rating</th>
                  <th>Price</th>
                  <th>Top X</th>
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
                        className={hasTopXRatings(p) ? "status-dot ok" : "status-dot missing"}
                        title={hasTopXRatings(p) ? "Top X ratings imported" : "Top X ratings missing"}
                      />
                    </td>
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
  const [eventId, setEventId] = useState("");
  const [importResult, setImportResult] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [eventBusy, setEventBusy] = useState(false);
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
          <button className={dataTab === "event" ? "tab active" : "tab"} onClick={() => setDataTab("event")}>
            HLTV Event
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

        {dataTab === "event" && (
          <div className="stack">
            <Input label="HLTV Event ID" value={eventId} onChange={setEventId} placeholder="e.g. 12345" />
            <div className="actions">
              <button className="primary" onClick={importByEvent} disabled={eventBusy}>
                {eventBusy ? "Importing..." : "Import by Event ID"}
              </button>
            </div>
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
  const [selectedTeamIds, setSelectedTeamIds] = useState([]);
  const [boMode, setBoMode] = useState("elim_qual");
  const [simCount, setSimCount] = useState("200");
  const [simResults, setSimResults] = useState(null);
  const [simUpdatedAt, setSimUpdatedAt] = useState("");

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

  useEffect(() => {
    loadStoredSimulation();
  }, []);

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
        <button className={swissTab === "single" ? "tab active" : "tab"} onClick={() => setSwissTab("single")}>
          Bracket Simulator
        </button>
        <button className={swissTab === "booster" ? "tab active" : "tab"} onClick={() => setSwissTab("booster")}>
          Booster Calculator
        </button>
      </div>
      {swissTab === "group" && (
        <GroupStageTab
          teams={teams}
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
      {swissTab === "single" && <BracketTab teams={teams} teamLookup={teamLookup} />}
      {swissTab === "booster" && <BoosterCalculatorTab />}
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
