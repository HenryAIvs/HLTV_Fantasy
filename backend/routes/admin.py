import json
import re
import math
import time
from html import unescape
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException

from backend.data.db_admin import wipe_database
from backend.data.event_db import set_active_event, upsert_event_snapshot
from backend.data.player_db import add_or_update_player, get_player
from backend.data.team_db import add_or_update_team, get_team_by_name
from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_html, fetch_hltv_json, run_hltv_browser_session
from backend.services.rating_picker import pick_match_rating
from swiss_stage.fantasy_scoring import (
    compute_rating_points,
    compute_role_points,
    compute_win_points,
    compute_booster_points,
)
from swiss_stage.swiss_models import PlayerState

router = APIRouter()

# Maps from our internal booster/role indices to HLTV IDs used in triggerRates JSON
BOOSTER_SOURCE_IDS = [
    2, 3, 5, 8, 9, 13, 16, 18, 19, 20, 21, 22, 23, 26, 27, 28, 29, 30
]
ROLE_SOURCE_IDS = [
    0, 1, 2, 5, 6, 7, 8, 9, 10, 11, 12, 13
]

HLTV_EVENT_URL_RE = re.compile(r"^https?://(?:www\.)?hltv\.org/events/\d+/[A-Za-z0-9_-]+(?:#simulator)?/?$")
HLTV_MATCH_LINE_RE = re.compile(
    r"(?P<date>\d{2}/\d{2}/\d{2})\s+"
    r"(?P<team_a>.+?)\s+"
    r"(?P<time>\d{1,2}:\d{2})\s+"
    r"(?P<team_b>.+?)\s+"
    r"(?P<format>bo\d)\b",
    re.IGNORECASE,
)
HLTV_SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"'](?P<src>[^\"']+\.js[^\"']*)[\"']", re.IGNORECASE)
HLTV_TEAM_RANK_RE = re.compile(r"^\s*(?P<team>.+?)\s+#\d+\s+#(?P<rank>\d+)\s*$")
HLTV_TEAM_LINK_RE = re.compile(r"<a\b[^>]*href=[\"']/team/\d+/[^\"']+[\"'][^>]*>(?P<body>.*?)</a>", re.IGNORECASE | re.DOTALL)
HLTV_IMAGE_NAME_RE = re.compile(r"<img\b[^>]*(?:alt|title)=[\"'](?P<name>[^\"']+)[\"'][^>]*>", re.IGNORECASE)

TIER_FIELD_MAP = {
    5: ("rating_top5", "maps_top5"),
    10: ("rating_top10", "maps_top10"),
    20: ("rating_top20", "maps_top20"),
    30: ("rating_top30", "maps_top30"),
    50: ("rating_top50", "maps_top50"),
}


def _html_to_lines(html: str) -> List[str]:
    text = re.sub(r"(?is)<script\b.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style\b.*?</style>", " ", text)
    text = re.sub(r"(?is)<br\s*/?>", "\n", text)
    text = re.sub(r"(?is)</(?:div|p|li|tr|td|th|h[1-6]|section|article|a)>", "\n", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = unescape(text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line:
            lines.append(line)
    return lines


def _ordered_unique(values: List[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _inspect_hltv_simulator_html(url: str, html: str) -> Dict[str, Any]:
    lines = _html_to_lines(html)
    line_text = "\n".join(lines)

    script_assets = []
    for match in HLTV_SCRIPT_SRC_RE.finditer(html or ""):
        src = match.group("src")
        if src.startswith("/"):
            src = f"https://www.hltv.org{src}"
        script_assets.append(src)

    format_lines = []
    for idx, line in enumerate(lines):
        lower = line.lower()
        if lower == "formats":
            format_lines.extend(lines[idx + 1 : idx + 4])
        elif "group stage swiss" in lower or "progression matches" in lower or "elimination matches" in lower:
            format_lines.append(line)
    format_lines = _ordered_unique(format_lines)

    buckets = _ordered_unique(re.findall(r"\b[0-3]:[0-3]\b", line_text))
    matchups = []
    seen_matchups = set()
    for line in lines:
        match = HLTV_MATCH_LINE_RE.search(line)
        if not match:
            continue
        team_a = match.group("team_a").strip()
        team_b = match.group("team_b").strip()
        key = tuple(sorted((team_a.lower(), team_b.lower()))) + (match.group("date"),)
        if key in seen_matchups:
            continue
        seen_matchups.add(key)
        matchups.append(
            {
                "date": match.group("date"),
                "time": match.group("time"),
                "team_a": team_a,
                "team_b": team_b,
                "format": match.group("format").lower(),
            }
        )

    return {
        "url": url,
        "simulator_visible": "Simulator" in lines and "0:0" in buckets,
        "format_lines": format_lines,
        "swiss_buckets": buckets,
        "initial_matchups": matchups,
        "script_assets": script_assets,
        "script_asset_count": len(script_assets),
        "notes": [
            "This inspects the rendered HLTV event page. It can confirm visible simulator data, format text, buckets, and initial pairings.",
            "If HLTV keeps pairing generation in client JS, inspect the listed script assets in browser devtools for the exact algorithm.",
            "If pairings are generated server-side, use this as a behavior-capture starting point and compare generated pairings after manual simulator clicks.",
        ],
    }


def _extract_team_ranks_from_html(html: str) -> Dict[str, int]:
    ranks: Dict[str, int] = {}
    for line in _html_to_lines(html):
        match = HLTV_TEAM_RANK_RE.match(line)
        if not match:
            continue
        team = re.sub(r"\s+", " ", match.group("team")).strip()
        if team and not team.lower().startswith(("player share", "club share")):
            ranks[team] = int(match.group("rank"))
    return ranks


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<[^>]+>", " ", value or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _is_plausible_team_name(name: str) -> bool:
    clean = (name or "").strip()
    if len(clean) < 2 or len(clean) > 40:
        return False
    lower = clean.lower()
    blocked = {
        "overview",
        "matches",
        "fantasy",
        "news",
        "results",
        "ranking",
        "players",
        "events",
        "image",
    }
    return lower not in blocked and not lower.startswith(("image:", "logo", "flag"))


def _extract_event_team_names_from_html(html: str) -> List[str]:
    names: List[str] = []
    for match in HLTV_TEAM_LINK_RE.finditer(html or ""):
        clean = _strip_html(match.group("body"))
        if _is_plausible_team_name(clean):
            names.append(clean)
    for match in HLTV_IMAGE_NAME_RE.finditer(html or ""):
        clean = _strip_html(match.group("name"))
        if _is_plausible_team_name(clean):
            names.append(clean)
    return _ordered_unique(names)


def _seed_ranks_from_initial_pairs(initial_pairs: List[Dict[str, str]]) -> Dict[str, int]:
    first_half = []
    second_half = []
    for pair in initial_pairs:
        a = str(pair.get("team_a") or "").strip()
        b = str(pair.get("team_b") or "").strip()
        if a and b:
            first_half.append(a)
            second_half.append(b)
    return {name: idx + 1 for idx, name in enumerate(first_half + second_half)}


def _pair_key(pair: Dict[str, str]) -> tuple[str, str]:
    a = str(pair.get("team_a") or "").strip().lower()
    b = str(pair.get("team_b") or "").strip().lower()
    return tuple(sorted((a, b)))


def _candidate_pairings(teams: List[str], ranks: Dict[str, int], mode: str) -> List[Dict[str, str]]:
    if len(teams) % 2 != 0:
        return []

    def seed_key(name: str) -> tuple[int, str]:
        return (int(ranks.get(name, 9999)), name.lower())

    ordered = list(teams)
    if mode in {"rank_adjacent", "rank_high_low", "rank_strong_weak"}:
        ordered = sorted(teams, key=seed_key)

    pairs = []
    if mode in {"rank_adjacent", "initial_order_adjacent"}:
        for idx in range(0, len(ordered), 2):
            pairs.append({"team_a": ordered[idx], "team_b": ordered[idx + 1]})
    elif mode in {"rank_high_low", "initial_order_high_low"}:
        half = len(ordered) // 2
        for idx in range(half):
            pairs.append({"team_a": ordered[idx], "team_b": ordered[idx + half]})
    elif mode == "rank_strong_weak":
        for idx in range(len(ordered) // 2):
            pairs.append({"team_a": ordered[idx], "team_b": ordered[len(ordered) - 1 - idx]})
    return pairs


def _score_candidate(observed: List[Dict[str, str]], predicted: List[Dict[str, str]]) -> Dict[str, Any]:
    observed_set = {_pair_key(pair) for pair in observed}
    predicted_set = {_pair_key(pair) for pair in predicted}
    matched = len(observed_set & predicted_set)
    total = max(len(observed_set), 1)
    return {
        "matched": matched,
        "total": len(observed_set),
        "score": matched / total,
        "missing": [
            {"team_a": a, "team_b": b}
            for a, b in sorted(observed_set - predicted_set)
            if a and b
        ],
        "extra": [
            {"team_a": a, "team_b": b}
            for a, b in sorted(predicted_set - observed_set)
            if a and b
        ],
    }


def _infer_pairing_rule_from_probe(probe: Dict[str, Any], ranks: Dict[str, int]) -> Dict[str, Any]:
    scenarios = probe.get("scenarios") or []
    modes = [
        ("rank_high_low", "Sort by rank, pair top half vs bottom half"),
        ("rank_adjacent", "Sort by rank, pair adjacent teams"),
        ("rank_strong_weak", "Sort by rank, pair strongest vs weakest"),
        ("initial_order_high_low", "Use previous match order, pair top half vs bottom half"),
        ("initial_order_adjacent", "Use previous match order, pair adjacent teams"),
    ]
    candidates = []
    for mode, label in modes:
        matched = 0
        total = 0
        details = []
        for scenario in scenarios:
            for bucket_key, teams_key in (("1:0", "winners"), ("0:1", "losers")):
                observed = scenario.get("after", {}).get(bucket_key) or []
                teams = scenario.get(teams_key) or []
                predicted = _candidate_pairings(teams, ranks, mode)
                score = _score_candidate(observed, predicted)
                matched += score["matched"]
                total += score["total"]
                details.append(
                    {
                        "scenario": scenario.get("name"),
                        "bucket": bucket_key,
                        "observed": observed,
                        "predicted": predicted,
                        **score,
                    }
                )
        candidates.append(
            {
                "mode": mode,
                "label": label,
                "matched": matched,
                "total": total,
                "score": (matched / total) if total else 0.0,
                "details": details,
            }
        )
    candidates.sort(key=lambda row: (-row["score"], -row["matched"], row["mode"]))
    return {
        "best": candidates[0] if candidates else None,
        "candidates": candidates,
    }


SIMULATOR_SNAPSHOT_JS = r"""
const teamNames = arguments[0] || [];
const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
const byNorm = new Map(teamNames.map((name) => [normalize(name), name]));
const isTeam = (value) => byNorm.has(normalize(value));
const teamName = (value) => byNorm.get(normalize(value));

function visible(el) {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
}

function teamsIn(el) {
  const names = [];
  el.querySelectorAll("img[alt], img[title], [aria-label], [title]").forEach((node) => {
    const values = [
      node.getAttribute("alt"),
      node.getAttribute("title"),
      node.getAttribute("aria-label"),
    ];
    values.forEach((value) => {
      if (isTeam(value)) names.push(teamName(value));
    });
  });
  (el.innerText || "").split(/\n+/).forEach((line) => {
    const clean = line.replace(/\s+/g, " ").trim();
    if (isTeam(clean)) names.push(teamName(clean));
  });
  return [...new Set(names)];
}

function simulatorRoot() {
  const roots = [...document.querySelectorAll("div, section, main")]
    .filter((el) => {
      const text = el.innerText || "";
      if (!text.includes("0:0") || !text.includes("3:2") || !text.includes("2:3")) return false;
      return teamsIn(el).length >= 8;
    })
    .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
  return roots[0] || document.body;
}

function pairElements(root) {
  const candidates = [];
  [...root.querySelectorAll("div, a, button, span, li")].forEach((el, idx) => {
    if (!visible(el)) return;
    const found = teamsIn(el);
    if (found.length !== 2) return;
    const text = (el.innerText || "").replace(/\s+/g, " ").trim();
    const hasVs = /\bvs\b/i.test(text);
    const rect = el.getBoundingClientRect();
    if (!hasVs && rect.width > 260) return;
    if (text.length > 180) return;
    candidates.push({ el, idx, text, found, area: rect.width * rect.height, y: rect.top, x: rect.left });
  });

  const byPair = new Map();
  candidates
    .sort((a, b) => a.text.length - b.text.length || a.area - b.area || a.idx - b.idx)
    .forEach((item) => {
      const key = [...item.found].map(normalize).sort().join("||");
      if (!byPair.has(key)) byPair.set(key, item);
    });

  return [...byPair.values()].sort((a, b) => a.y - b.y || a.x - b.x);
}

function bucketElements(root) {
  const buckets = {};
  const bucketLabels = ["0:0", "1:0", "0:1", "2:0", "1:1", "0:2", "3:0", "2:1", "1:2", "0:3", "3:1", "3:2", "2:2", "1:3", "2:3"];
  [...root.querySelectorAll("div, section, article, li")].forEach((el, idx) => {
    if (!visible(el)) return;
    const text = (el.innerText || "").replace(/\s+/g, " ").trim();
    const found = teamsIn(el);
    if (!found.length) return;
    const labels = bucketLabels.filter((label) => text.includes(label));
    if (labels.length !== 1) return;
    const rect = el.getBoundingClientRect();
    if (text.length > 900 || rect.width > 700 || rect.height > 700) return;
    const label = labels[0];
    const current = buckets[label];
    const item = { label, teams: found, text, idx, area: rect.width * rect.height, x: rect.left, y: rect.top };
    if (!current || item.teams.length > current.teams.length || (item.teams.length === current.teams.length && item.area < current.area)) {
      buckets[label] = item;
    }
  });
  return buckets;
}

function pairFromBucketTeams(teams) {
  const pairs = [];
  for (let i = 0; i + 1 < teams.length; i += 2) {
    pairs.push({ team_a: teams[i], team_b: teams[i + 1] });
  }
  return pairs;
}

const root = simulatorRoot();
const buckets = bucketElements(root);
return {
  rootTextLength: (root.innerText || "").length,
  pairs: pairElements(root).map((item) => ({
    team_a: item.found[0],
    team_b: item.found[1],
    text: item.text,
    x: Math.round(item.x),
    y: Math.round(item.y),
  })),
  buckets: Object.fromEntries(Object.entries(buckets).map(([label, item]) => [
    label,
    {
      teams: item.teams,
      pairs: pairFromBucketTeams(item.teams),
      text: item.text,
      x: Math.round(item.x),
      y: Math.round(item.y),
    },
  ])),
};
"""


SIMULATOR_CLICK_TEAM_JS = r"""
const teamNames = arguments[0] || [];
const winner = arguments[1];
const normalize = (value) => String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
const winnerNorm = normalize(winner);
const byNorm = new Map(teamNames.map((name) => [normalize(name), name]));
const isTeam = (value) => byNorm.has(normalize(value));
const teamName = (value) => byNorm.get(normalize(value));

function visible(el) {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
}

function teamsIn(el) {
  const names = [];
  el.querySelectorAll("img[alt], img[title], [aria-label], [title]").forEach((node) => {
    [node.getAttribute("alt"), node.getAttribute("title"), node.getAttribute("aria-label")].forEach((value) => {
      if (isTeam(value)) names.push(teamName(value));
    });
  });
  (el.innerText || "").split(/\n+/).forEach((line) => {
    const clean = line.replace(/\s+/g, " ").trim();
    if (isTeam(clean)) names.push(teamName(clean));
  });
  return [...new Set(names)];
}

function simulatorRoot() {
  const roots = [...document.querySelectorAll("div, section, main")]
    .filter((el) => {
      const text = el.innerText || "";
      if (!text.includes("0:0") || !text.includes("3:2") || !text.includes("2:3")) return false;
      return teamsIn(el).length >= 8;
    })
    .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);
  return roots[0] || document.body;
}

function clickableFor(node) {
  return node.closest("button, a, [role='button'], [onclick], .clickable") || node.parentElement || node;
}

function fireClick(el) {
  if (!el) return false;
  el.scrollIntoView({ block: "center", inline: "center" });
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  ["pointerdown", "mousedown", "mouseup", "click"].forEach((type) => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
  });
  return true;
}

const root = simulatorRoot();
const pairEls = [...root.querySelectorAll("div, a, button, span, li")]
  .filter((el) => visible(el) && teamsIn(el).length === 2)
  .sort((a, b) => (a.innerText || "").length - (b.innerText || "").length);

for (const pairEl of pairEls) {
  if (!teamsIn(pairEl).some((name) => normalize(name) === winnerNorm)) continue;
  const nodes = [...pairEl.querySelectorAll("img[alt], img[title], [aria-label], [title], span, div")];
  for (const node of nodes) {
    const values = [
      node.getAttribute && node.getAttribute("alt"),
      node.getAttribute && node.getAttribute("title"),
      node.getAttribute && node.getAttribute("aria-label"),
      node.innerText,
    ];
    if (!values.some((value) => normalize(value) === winnerNorm)) continue;
    const target = clickableFor(node);
    fireClick(target);
    return { clicked: true, winner, text: (pairEl.innerText || "").replace(/\s+/g, " ").trim() };
  }
  const pairTarget = clickableFor(pairEl);
  fireClick(pairTarget);
  return { clicked: true, winner, text: (pairEl.innerText || "").replace(/\s+/g, " ").trim(), fallback: "pair" };
}
return { clicked: false, winner };
"""


SIMULATOR_ADVANCE_JS = r"""
const labels = ["simulate", "next", "advance", "generate", "play", "submit", "update"];
function visible(el) {
  const rect = el.getBoundingClientRect();
  const style = window.getComputedStyle(el);
  return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
}
function fireClick(el) {
  el.scrollIntoView({ block: "center", inline: "center" });
  const rect = el.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  ["pointerdown", "mousedown", "mouseup", "click"].forEach((type) => {
    el.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
  });
}
const clicked = [];
[...document.querySelectorAll("button, a, [role='button'], input[type='button'], input[type='submit']")].forEach((el) => {
  if (!visible(el)) return;
  const text = ((el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("title") || "") + "").replace(/\s+/g, " ").trim();
  if (!text) return;
  const lower = text.toLowerCase();
  if (!labels.some((label) => lower.includes(label))) return;
  fireClick(el);
  clicked.push(text);
});
return { clicked };
"""


def _choose_probe_winners(initial_pairs: List[Dict[str, str]], ranks: Dict[str, int], scenario_name: str) -> List[str]:
    winners = []
    for idx, pair in enumerate(initial_pairs):
        a = str(pair.get("team_a") or "").strip()
        b = str(pair.get("team_b") or "").strip()
        if not a or not b:
            continue
        if scenario_name == "left_wins":
            winners.append(a)
        elif scenario_name == "right_wins":
            winners.append(b)
        elif scenario_name == "alternating":
            winners.append(a if idx % 2 == 0 else b)
        else:
            winners.append(a if int(ranks.get(a, 9999)) <= int(ranks.get(b, 9999)) else b)
    return winners


def _classify_after_pairs(
    pairs: List[Dict[str, str]],
    winners: List[str],
    losers: List[str],
) -> Dict[str, List[Dict[str, str]]]:
    winner_set = {name.lower() for name in winners}
    loser_set = {name.lower() for name in losers}
    buckets = {"1:0": [], "0:1": []}
    seen = {"1:0": set(), "0:1": set()}
    for pair in pairs:
        a = str(pair.get("team_a") or "").strip()
        b = str(pair.get("team_b") or "").strip()
        if not a or not b:
            continue
        key = tuple(sorted((a.lower(), b.lower())))
        if a.lower() in winner_set and b.lower() in winner_set and key not in seen["1:0"]:
            buckets["1:0"].append({"team_a": a, "team_b": b})
            seen["1:0"].add(key)
        if a.lower() in loser_set and b.lower() in loser_set and key not in seen["0:1"]:
            buckets["0:1"].append({"team_a": a, "team_b": b})
            seen["0:1"].add(key)
    return buckets


def _run_hltv_pairing_probe(url: str, scenario_names: List[str] | None = None) -> Dict[str, Any]:
    scenario_names = scenario_names or ["higher_seed_wins", "left_wins", "right_wins", "alternating"]

    def callback(driver):
        html = driver.page_source or ""
        parsed_ranks = _extract_team_ranks_from_html(html)
        extracted_names = _extract_event_team_names_from_html(html)
        if len(parsed_ranks) >= 8:
            team_names = sorted(parsed_ranks.keys(), key=lambda name: (parsed_ranks.get(name, 9999), name.lower()))
        else:
            rank_names = list(parsed_ranks.keys())
            extra_names = [name for name in extracted_names if name not in parsed_ranks]
            team_names = _ordered_unique(rank_names + extra_names)[:24]

        if len(team_names) < 8:
            return {
                "ok": False,
                "error": "Could not extract enough teams from the HLTV event page.",
                "ranks": parsed_ranks,
                "extracted_team_names": extracted_names,
            }

        scenarios = []
        inferred_seed_ranks: Dict[str, int] | None = None
        for scenario_name in scenario_names:
            driver.get(url)
            time.sleep(1.0)
            initial_snapshot = driver.execute_script(SIMULATOR_SNAPSHOT_JS, team_names) or {}
            initial_pairs = list(initial_snapshot.get("pairs") or [])[:8]
            if len(initial_pairs) < 4:
                scenarios.append(
                    {
                        "name": scenario_name,
                        "error": "Could not capture enough initial simulator pairings.",
                        "initial_snapshot": initial_snapshot,
                    }
                )
                continue

            if inferred_seed_ranks is None:
                inferred_seed_ranks = _seed_ranks_from_initial_pairs(initial_pairs)
            ranks = parsed_ranks if len(parsed_ranks) >= 8 else inferred_seed_ranks
            winners = _choose_probe_winners(initial_pairs, ranks, scenario_name)
            winner_set = {name.lower() for name in winners}
            losers = [
                name
                for pair in initial_pairs
                for name in (pair.get("team_a"), pair.get("team_b"))
                if str(name or "").lower() not in winner_set
            ]
            clicks = []
            for winner in winners:
                click_result = driver.execute_script(SIMULATOR_CLICK_TEAM_JS, team_names, winner) or {}
                clicks.append(click_result)
                time.sleep(0.15)

            advance_result = driver.execute_script(SIMULATOR_ADVANCE_JS) or {}
            time.sleep(0.8)
            after_snapshot = driver.execute_script(SIMULATOR_SNAPSHOT_JS, team_names) or {}
            after_buckets = after_snapshot.get("buckets") or {}
            after = {
                "1:0": list((after_buckets.get("1:0") or {}).get("pairs") or []),
                "0:1": list((after_buckets.get("0:1") or {}).get("pairs") or []),
            }
            if not after["1:0"] and not after["0:1"]:
                after = _classify_after_pairs(list(after_snapshot.get("pairs") or []), winners, losers)
            scenarios.append(
                {
                    "name": scenario_name,
                    "initial": initial_pairs,
                    "winners": winners,
                    "losers": losers,
                    "clicks": clicks,
                    "clicks_succeeded": sum(1 for click in clicks if click.get("clicked")),
                    "advance_result": advance_result,
                    "after": after,
                    "after_snapshot": after_snapshot,
                }
            )

        probe = {
            "ok": True,
            "ranks": parsed_ranks if len(parsed_ranks) >= 8 else (inferred_seed_ranks or {}),
            "rank_source": "hltv_page" if len(parsed_ranks) >= 8 else "inferred_from_initial_pairings",
            "parsed_rank_count": len(parsed_ranks),
            "extracted_team_count": len(team_names),
            "team_names": team_names,
            "scenarios": scenarios,
        }
        probe["inference"] = _infer_pairing_rule_from_probe(probe, probe["ranks"])
        return probe

    return run_hltv_browser_session(url, callback, wait_text="Simulator", timeout_ms=90000)


def _import_money_draft_data(money: Dict[str, Any], event_id: Optional[int] = None) -> Dict[str, int]:
    teams = money.get("teams") or []
    imported_players = 0
    imported_teams = 0
    event_teams_snapshot: List[Dict[str, Any]] = []

    for team_block in teams:
        team_data = team_block.get("teamData", {}) or {}
        team_name = team_data.get("name", "").strip()
        if not team_name:
            continue
        hltv_team_id = (
            team_data.get("teamId")
            or team_data.get("id")
            or (team_data.get("fantasyTeamId", {}) or {}).get("teamId")
            or (team_data.get("team", {}) or {}).get("teamId")
        )
        try:
            hltv_team_id = int(hltv_team_id) if hltv_team_id is not None else None
        except Exception:
            hltv_team_id = None

        player_entries = team_block.get("players", []) or []
        player_ids = []
        prices_by_player: Dict[int, int] = {}

        for entry in player_entries:
            p_data = entry.get("playerData", {}) or {}
            pid_obj = p_data.get("fantasyPlayerId", {}) or {}
            pid = pid_obj.get("playerId")
            name = p_data.get("name", "").strip()
            cost = entry.get("cost", 0)

            if pid is None or not name:
                continue

            pid = int(pid)
            player_ids.append(pid)
            prices_by_player[pid] = int(cost)

            add_or_update_player(
                player_id=pid,
                name=name,
                # Do not import overall rating from event JSON.
                # Overall rating is sourced from player stats page fetch (Top-X flow).
                rating=None,
                price=int(cost),
                best_role="",
                major_win_pct=0.0,
                minor_win_pct=0.0,
                boosters_json=None,
                roles_json=None,
            )
            imported_players += 1

        ids = player_ids[:5] if len(player_ids) >= 5 else player_ids + [0] * (5 - len(player_ids))
        existing_team = get_team_by_name(team_name)
        hltv_rank = int((existing_team or {}).get("hltv_rank") or 999)
        vrs_rank = int((existing_team or {}).get("vrs_rank") or 999)
        win_rate = float((existing_team or {}).get("win_rate") or 0.5)
        add_or_update_team(
            name=team_name,
            hltv_rank=hltv_rank,
            hltv_points=int((existing_team or {}).get("hltv_points") or 0),
            vrs_rank=vrs_rank,
            vrs_points=int((existing_team or {}).get("vrs_points") or 0),
            win_rate=win_rate,
            player_ids=ids,
            hltv_team_id=hltv_team_id or (existing_team or {}).get("hltv_team_id"),
        )
        imported_teams += 1
        event_teams_snapshot.append(
            {
                "team_name": team_name,
                "player_ids": ids,
                "prices_by_player": prices_by_player,
            }
        )

    if event_id is not None:
        upsert_event_snapshot(int(event_id), event_teams_snapshot)
        set_active_event(int(event_id))

    return {"imported_players": imported_players, "imported_teams": imported_teams}


@router.post("/wipe")
def wipe():
    """
    Deletes all players and teams but keeps the schema.
    """
    wipe_database()
    return {"status": "ok"}


@router.post("/import-hltv")
def import_hltv(payload: Dict[str, Any]):
    """
    Import players/teams from a pasted HLTV fantasy JSON (moneyDraftData).
    This avoids scraping and works offline with a provided JSON blob.
    """
    raw_json = payload.get("fantasy_json")
    if not raw_json:
        raise HTTPException(status_code=400, detail="fantasy_json is required")

    try:
        data = json.loads(raw_json) if isinstance(raw_json, str) else raw_json
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    money = data.get("moneyDraftData", {})
    teams = money.get("teams") or []
    if not teams:
        raise HTTPException(status_code=400, detail="moneyDraftData.teams missing or empty")

    counts = _import_money_draft_data(money)
    return {"status": "ok", **counts}


@router.post("/import-hltv-event")
def import_hltv_event(payload: Dict[str, Any]):
    """
    Fetch HLTV fantasy JSON for a given event id and import teams/players.
    """
    event_id = str(payload.get("event_id", "")).strip()
    if not event_id.isdigit():
        raise HTTPException(status_code=400, detail="event_id must be numeric")

    url = f"https://www.hltv.org/fantasy/{event_id}/leagues/create/json"
    try:
        data = fetch_hltv_json(url)
    except HLTVBrowserError as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV data with SeleniumBase UC: {e}")

    money = data.get("moneyDraftData", {})
    if not money:
        raise HTTPException(status_code=400, detail="moneyDraftData missing in response")

    counts = _import_money_draft_data(money, event_id=int(event_id))
    return {"status": "ok", **counts, "event_id": event_id}


@router.post("/import-trigger-rates")
def import_trigger_rates(payload: Dict[str, Any]):
    """
    Paste-in importer for triggerRates JSON (playerTriggerRates array).
    Updates boosters_json and roles_json for players.
    """
    raw = payload.get("trigger_json")
    if not raw:
        raise HTTPException(status_code=400, detail="trigger_json is required")

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    ptr_list = data.get("playerTriggerRates") or []
    if not ptr_list:
        raise HTTPException(status_code=400, detail="playerTriggerRates missing or empty")

    updated = 0
    updated_players_info: List[Dict[str, Any]] = []
    seen_player_ids = set()
    for entry in ptr_list:
        pid = entry.get("playerId", {}).get("playerId")
        if pid is None:
            continue
        booster_map_raw = entry.get("boosterIdToTriggerRate", {}) or {}
        role_map_raw = entry.get("roleIdToTriggerRate", {}) or {}

        # Preserve existing core info so NOT NULL cols are satisfied
        from backend.data.player_db import get_player  # local import to avoid circular

        existing = get_player(int(pid))
        name = (existing or {}).get("name") or f"Player {pid}"
        rating = (existing or {}).get("rating", 0.0)
        price = (existing or {}).get("price", 0)
        best_role = (existing or {}).get("best_role", "")
        major_win_pct = (existing or {}).get("major_win_pct", 0.0)
        minor_win_pct = (existing or {}).get("minor_win_pct", 0.0)

        # Start from existing booster/role JSON so we don't lose prior data
        existing_boosters = {}
        existing_roles = {}
        try:
            if existing and existing.get("boosters_json"):
                existing_boosters = json.loads(existing["boosters_json"])
        except Exception:
            existing_boosters = {}
        try:
            if existing and existing.get("roles_json"):
                existing_roles = json.loads(existing["roles_json"])
        except Exception:
            existing_roles = {}

        boosters_json = dict(existing_boosters)
        if booster_map_raw:
            boosters_json = {}
            for idx, src_id in enumerate(BOOSTER_SOURCE_IDS):
                obj = booster_map_raw.get(str(src_id), {}) or {}
                val = float(obj.get("value", 0.0))
                boosters_json[str(idx)] = val

        roles_json = dict(existing_roles)
        if role_map_raw:
            roles_json = {}
            for idx, src_id in enumerate(ROLE_SOURCE_IDS):
                rdata = role_map_raw.get(str(src_id), {}) or {}
                small = rdata.get("smallPoints", {}) or {}
                maxp = rdata.get("maxPoints", {}) or {}
                roles_json[str(idx)] = {
                    "major": float(maxp.get("value", 0.0)),
                    "minor": float(small.get("value", 0.0)),
                }

        # Pick best role by highest major trigger; fall back to existing values
        best_role_id = best_role
        best_major = major_win_pct
        best_minor = minor_win_pct
        if roles_json:
            best = max(roles_json.items(), key=lambda kv: kv[1].get("major", 0.0))
            best_role_id = str(best[0])
            best_major = float(best[1].get("major", 0.0))
            best_minor = float(best[1].get("minor", 0.0))

        add_or_update_player(
            player_id=int(pid),
            name=name,
            rating=rating,
            price=price,
            best_role=best_role_id,
            major_win_pct=best_major,
            minor_win_pct=best_minor,
            boosters_json=boosters_json,
            roles_json=roles_json,
        )
        updated += 1
        if int(pid) not in seen_player_ids:
            updated_players_info.append({"player_id": int(pid), "name": name})
            seen_player_ids.add(int(pid))

    updated_player_names = [p["name"] for p in updated_players_info]
    return {
        "status": "ok",
        "updated_players": updated,
        "updated_players_info": updated_players_info,
        "updated_player_names": updated_player_names,
    }


@router.post("/import-top-ratings")
def import_top_ratings(payload: Dict[str, Any]):
    """
    Parse pasted "vs top X opponents" text and update rating_topX/maps_topX for a player.
    Payload: { player_id: int, text: str }
    """
    player_id = payload.get("player_id")
    text = payload.get("text") or ""
    if not player_id:
        raise HTTPException(status_code=400, detail="player_id is required")
    if not str(player_id).isdigit():
        raise HTTPException(status_code=400, detail="player_id must be numeric")
    if not text.strip():
        raise HTTPException(status_code=400, detail="text is required")

    # Find all rating/tier/maps tuples
    pattern = re.compile(r"(?P<rating>\d+\.\d+)\s*vs top\s*(?P<tier>\d+)\s*opponents.*?\(\s*(?P<maps>\d+)\s*maps", re.IGNORECASE | re.DOTALL)
    matches = pattern.findall(text)
    if not matches:
        raise HTTPException(status_code=400, detail="No 'vs top X opponents' entries found")

    # Collect parsed values per tier
    tier_data = {}
    for rating_str, tier_str, maps_str in matches:
        try:
            tier = int(tier_str)
        except Exception:
            continue
        if tier not in TIER_FIELD_MAP:
            continue
        try:
            rating_val = float(rating_str)
            maps_val = int(maps_str)
        except Exception:
            continue
        tier_data[tier] = (rating_val, maps_val)

    # For each target tier, if maps < 5, fall forward to the next available tier with >=5 maps
    tiers_sorted = sorted(TIER_FIELD_MAP.keys())

    def pick_for(target_tier):
        # prefer current tier if maps >=5
        if target_tier in tier_data and tier_data[target_tier][1] >= 5:
            return tier_data[target_tier]
        # otherwise look ahead
        for t in tiers_sorted:
            if t < target_tier:
                continue
            if t in tier_data and tier_data[t][1] >= 5:
                return tier_data[t]
        # fallback: if current tier exists but <5 maps, still return it
        if target_tier in tier_data:
            return tier_data[target_tier]
        return None

    updates = {}
    for t in tiers_sorted:
        val = pick_for(t)
        if not val:
            continue
        rating_val, maps_val = val
        r_field, m_field = TIER_FIELD_MAP[t]
        updates[r_field] = rating_val
        updates[m_field] = maps_val

    if not updates:
        raise HTTPException(status_code=400, detail="No supported tiers found (expect top 5/10/20/30/50)")

    # Ensure NOT NULL fields are present by pulling existing row
    from backend.data.player_db import get_player  # local import to avoid circular

    existing = get_player(int(player_id)) or {}
    name = existing.get("name") or f"Player {player_id}"
    rating = existing.get("rating", 0.0)
    price = existing.get("price", 0)
    best_role = existing.get("best_role", "")
    major_win_pct = existing.get("major_win_pct", 0.0)
    minor_win_pct = existing.get("minor_win_pct", 0.0)
    boosters_json = existing.get("boosters_json")
    roles_json = existing.get("roles_json")

    add_or_update_player(
        player_id=int(player_id),
        name=name,
        rating=rating,
        price=price,
        best_role=best_role,
        major_win_pct=major_win_pct,
        minor_win_pct=minor_win_pct,
        boosters_json=boosters_json,
        roles_json=roles_json,
        **updates,
    )
    return {"status": "ok", "updated_fields": list(updates.keys())}


@router.post("/inspect-hltv-simulator")
def inspect_hltv_simulator(payload: Dict[str, Any]):
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if url.startswith("/events/"):
        url = f"https://www.hltv.org{url}"
    if "#simulator" not in url:
        url = url.rstrip("/") + "#simulator"
    if not HLTV_EVENT_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="Use a valid HLTV event URL, e.g. https://www.hltv.org/events/8914/xse-pro-league-2026#simulator")

    try:
        html = fetch_hltv_html(url, wait_text="Simulator", timeout_ms=60000)
    except HLTVBrowserError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch HLTV event page with SeleniumBase UC: {exc}") from exc

    return _inspect_hltv_simulator_html(url, html)


@router.post("/infer-hltv-simulator-pairing")
def infer_hltv_simulator_pairing(payload: Dict[str, Any]):
    url = str(payload.get("url") or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    if url.startswith("/events/"):
        url = f"https://www.hltv.org{url}"
    if "#simulator" not in url:
        url = url.rstrip("/") + "#simulator"
    if not HLTV_EVENT_URL_RE.match(url):
        raise HTTPException(status_code=400, detail="Use a valid HLTV event URL, e.g. https://www.hltv.org/events/8914/xse-pro-league-2026#simulator")

    scenario_names = payload.get("scenarios")
    if not isinstance(scenario_names, list):
        scenario_names = None
    else:
        scenario_names = [str(name) for name in scenario_names if str(name) in {"higher_seed_wins", "left_wins", "right_wins", "alternating"}]

    try:
        result = _run_hltv_pairing_probe(url, scenario_names=scenario_names)
    except HLTVBrowserError as exc:
        raise HTTPException(status_code=502, detail=f"Failed to probe HLTV simulator with SeleniumBase UC: {exc}") from exc

    result["url"] = url
    return result


@router.post("/booster-calc")
def booster_calc(payload: Dict[str, Any]):
    """
    Utility calculator for per-match and expected fantasy components.
    Payload:
      {
        "player_id": int,            # required; loads Top-X profile for match rating prediction
        "major_pct": float,
        "minor_pct": float,
        "win_prob": float,           # expected per-match win probability [0,1]
        "opponent_ranks": [int...],  # required; one rank per match
        "booster_rates": [float...], # trigger rates per match (typically 5)
        "matches": int,              # optional, default 5
        "expected_games": float      # optional, e.g. 4.2
      }
    """
    try:
        major_pct = float(payload.get("major_pct", 0.0))
        minor_pct = float(payload.get("minor_pct", 0.0))
        win_prob = float(payload.get("win_prob", 0.5))
    except Exception:
        raise HTTPException(status_code=400, detail="major_pct, minor_pct, win_prob must be numeric")

    if not (0.0 <= win_prob <= 1.0):
        raise HTTPException(status_code=400, detail="win_prob must be between 0 and 1")

    player_id = payload.get("player_id")
    if player_id is None or str(player_id).strip() == "":
        raise HTTPException(status_code=400, detail="player_id is required")
    try:
        pid = int(player_id)
    except Exception:
        raise HTTPException(status_code=400, detail="player_id must be an integer")
    picker_row: Optional[Dict[str, Any]] = get_player(pid)
    if not picker_row:
        raise HTTPException(status_code=404, detail=f"player_id {pid} not found")

    raw_opponent_ranks = payload.get("opponent_ranks", []) or []
    opponent_ranks: List[int] = []
    for r in raw_opponent_ranks:
        try:
            opponent_ranks.append(int(r))
        except Exception:
            continue

    raw_rates = payload.get("booster_rates", []) or []
    booster_rates: List[float] = []
    for r in raw_rates:
        try:
            booster_rates.append(float(r))
        except Exception:
            continue

    matches = int(payload.get("matches", 5))
    if matches < 1:
        matches = 1
    if matches > 10:
        matches = 10
    if len(opponent_ranks) < matches:
        raise HTTPException(status_code=400, detail=f"opponent_ranks must contain at least {matches} values")

    player = PlayerState(
        player_id=pid,
        rating=float(picker_row.get("rating", 1.0)),
        major_pct=major_pct,
        minor_pct=minor_pct,
        boosters=booster_rates,
    )

    role_points = compute_role_points(player)
    win_points = compute_win_points(win_prob, did_win=True)

    per_match = []
    rating_points_sum = 0.0
    for match_no in range(1, matches + 1):
        opp_rank = opponent_ranks[match_no - 1]
        match_rating = float(pick_match_rating(picker_row, int(opp_rank)))

        player.rating = match_rating
        rating_points = compute_rating_points(player)
        booster_points = compute_booster_points(player, match_no)
        total_points = rating_points + role_points + win_points + booster_points
        rating_points_sum += rating_points
        per_match.append(
            {
                "match_number": match_no,
                "opponent_rank": int(opp_rank),
                "match_rating": match_rating,
                "rating_points": rating_points,
                "role_points": role_points,
                "win_points": win_points,
                "booster_points": booster_points,
                "total_points": total_points,
            }
        )

    avg_rating_points = (rating_points_sum / float(matches)) if matches > 0 else 0.0
    out: Dict[str, Any] = {
        "rating_points": avg_rating_points,
        "role_points": role_points,
        "win_points": win_points,
        "per_match": per_match,
    }

    if payload.get("expected_games") is not None:
        try:
            expected_games = float(payload.get("expected_games"))
        except Exception:
            raise HTTPException(status_code=400, detail="expected_games must be numeric")

        expected_games = max(0.0, min(float(matches), expected_games))
        whole = int(math.floor(expected_games))
        frac = expected_games - whole

        expected_booster = sum(p["booster_points"] for p in per_match[:whole])
        if whole < matches and frac > 0:
            expected_booster += frac * per_match[whole]["booster_points"]

        base_no_booster = (avg_rating_points + role_points + win_points) * expected_games
        expected_total = base_no_booster + expected_booster

        out["expected_games"] = expected_games
        out["expected_booster_points"] = expected_booster
        out["expected_total_points"] = expected_total

    return out
