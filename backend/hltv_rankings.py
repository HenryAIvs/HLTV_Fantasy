import logging
import re
import time
from datetime import date, datetime, timezone
from html import unescape
from typing import Dict, List

from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_html


class HLTVRankingError(RuntimeError):
    """Base error for HLTV team ranking lookups."""


class TeamNotRankedError(HLTVRankingError):
    """Raised when a team is not present on the selected ranking date."""


class RankingPageParseError(HLTVRankingError):
    """Raised when HLTV ranking page format cannot be parsed."""


logger = logging.getLogger(__name__)


_TEXT_ENTRY_RE = re.compile(
    r"#\s*(?P<rank>\d+)\s+"
    r"(?P<name>[A-Za-z0-9 .'\-+&/]+?)\s*"
    r"\(\s*(?P<points>[\d,]+)\s+HLTV\s+points\s*\)",
    re.IGNORECASE,
)
_VRS_TEXT_ENTRY_RE = re.compile(
    r"#\s*(?P<rank>\d+)\s+"
    r"(?P<name>[^()#]+?)\s*"
    r"\(\s*(?P<points>[\d,]+)\s+(?:VRS|Valve)\s+points\s*\)",
    re.IGNORECASE,
)
_VRS_TEXT_ENTRY_FALLBACK_RE = re.compile(
    r"#\s*(?P<rank>\d+)\s+"
    r"(?P<name>[^()#]+?)\s+"
    r"(?P<points>[\d,]+)\s+(?:VRS|Valve)\s+points",
    re.IGNORECASE,
)


_RESULT_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>/matches/[^"]+)"[^>]*>(?P<body>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_EVENT_LINK_RE = re.compile(
    r'<a[^>]+href="/events/\d+/[^"]*"[^>]*>(?P<name>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_UNIX_ATTR_RE = re.compile(r"data-zonedgrouping-entry-unix\s*=\s*['\"](?P<ts>\d{10,13})['\"]", re.IGNORECASE)
_DATE_HEADER_RE = re.compile(
    r"(Results\s+for\s+[A-Za-z,\s]*\b[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4}|[A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4})",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR_RE = re.compile(
    r"\b(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+\d{1,2}(?:st|nd|rd|th)?(?:,)?\s+\d{4}\b",
    re.IGNORECASE,
)
_MAP_LINE_RE = re.compile(
    r"\b(?P<map>Mirage|Inferno|Nuke|Ancient|Anubis|Dust2|Train|Overpass|Vertigo|Cache|Cobblestone)\b"
    r"[^0-9]{0,32}(?P<score1>\d{1,2})\s*[-:]\s*(?P<score2>\d{1,2})",
    re.IGNORECASE,
)


def _build_hltv_ranking_url(on_date: date) -> str:
    month = on_date.strftime("%B").lower()
    return f"https://www.hltv.org/ranking/teams/{on_date.year}/{month}/{on_date.day}"


def _build_hltv_ranking_url_undated() -> str:
    return "https://www.hltv.org/ranking/teams"


def _build_vrs_ranking_url(on_date: date) -> str:
    month = on_date.strftime("%B").lower()
    return f"https://www.hltv.org/valve-ranking/teams/{on_date.year}/{month}/{on_date.day}"


def _build_vrs_ranking_url_undated() -> str:
    return "https://www.hltv.org/valve-ranking/teams"


def _build_results_url(offset: int = 0) -> str:
    offset = max(0, int(offset))
    if offset <= 0:
        return "https://www.hltv.org/results"
    return f"https://www.hltv.org/results?offset={offset}"


def _is_cloudflare_challenge_html(html: str) -> bool:
    html_l = (html or "").lower()
    return (
        "just a moment" in html_l
        or "/cdn-cgi/challenge-platform" in html_l
        or "cf-chl-" in html_l
        or "challenge-platform" in html_l
        or "ray id:" in html_l
    )


def _candidate_ranking_urls(on_date: date) -> list[str]:
    dated = _build_hltv_ranking_url(on_date)
    undated = _build_hltv_ranking_url_undated()
    return [dated, undated]


def _extract_rankings(html: str) -> Dict[str, Dict[str, int | str]]:
    t0 = time.perf_counter()
    rankings: Dict[str, Dict[str, int | str]] = {}
    found_any = False
    html_l = html.lower()

    # Parse normalized visible text only. The old HTML-structure regex could
    # catastrophically backtrack and hang on modern HLTV pages.
    t_text0 = time.perf_counter()
    html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html_no_scripts = re.sub(r"<style\b[^>]*>.*?</style>", " ", html_no_scripts, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", html_no_scripts)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    logger.info("HLTV ranking text normalization complete in %.3fs", time.perf_counter() - t_text0)

    t_re0 = time.perf_counter()
    for match in _TEXT_ENTRY_RE.finditer(text):
        found_any = True
        name = unescape(match.group("name")).strip()
        rank = int(match.group("rank"))
        points = int(match.group("points").replace(",", ""))
        rankings[name.lower()] = {
            "team_name": name,
            "hltv_rating": rank,
            "points": points,
        }
    logger.info("HLTV ranking text regex pass complete in %.3fs (teams=%d)", time.perf_counter() - t_re0, len(rankings))

    if not found_any:
        if "404" in html_l and "page not found" in html_l:
            raise RankingPageParseError("HLTV returned 404 page for ranking URL.")
        if "cloudflare" in html_l or "just a moment" in html_l or "/cdn-cgi/challenge-platform" in html_l:
            raise HLTVRankingError(
                "HLTV page is behind a Cloudflare/interstitial challenge. "
                "Open once in visible browser mode and complete challenge, then retry."
            )
        raise RankingPageParseError("Could not parse team ranking entries from HLTV page.")

    logger.info("HLTV ranking extraction complete in %.3fs (teams=%d)", time.perf_counter() - t0, len(rankings))
    return rankings


def _extract_vrs_rankings(html: str) -> Dict[str, Dict[str, int | str]]:
    rankings: Dict[str, Dict[str, int | str]] = {}
    html_l = html.lower()

    html_no_scripts = re.sub(r"<script\b[^>]*>.*?</script>", " ", html, flags=re.IGNORECASE | re.DOTALL)
    html_no_scripts = re.sub(r"<style\b[^>]*>.*?</style>", " ", html_no_scripts, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", html_no_scripts)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()

    for rx in (_VRS_TEXT_ENTRY_RE, _VRS_TEXT_ENTRY_FALLBACK_RE):
        for match in rx.finditer(text):
            name = unescape(match.group("name")).strip()
            rank = int(match.group("rank"))
            points = int(match.group("points").replace(",", ""))
            key = name.lower()
            # Keep first occurrence to prefer the global ranking block and
            # avoid later regional duplicates overwriting points.
            if key not in rankings:
                rankings[key] = {
                    "team_name": name,
                    "vrs_rank": rank,
                    "points": points,
                }
        if rankings:
            break

    if not rankings:
        if "404" in html_l and "page not found" in html_l:
            raise RankingPageParseError("HLTV returned 404 page for VRS ranking URL.")
        if "cloudflare" in html_l or "just a moment" in html_l or "/cdn-cgi/challenge-platform" in html_l:
            raise HLTVRankingError(
                "HLTV page is behind a Cloudflare/interstitial challenge. "
                "Open once in visible browser mode and complete challenge, then retry."
            )
        raise RankingPageParseError("Could not parse team VRS ranking entries from HLTV page.")

    return rankings


def _fetch_html_with_uc_driver(url: str, timeout_ms: int = 45000, wait_text: str | None = "HLTV points") -> str:
    try:
        return fetch_hltv_html(url, timeout_ms=timeout_ms, wait_text=wait_text)
    except HLTVBrowserError as exc:
        raise HLTVRankingError(str(exc)) from exc


def _strip_html(text: str) -> str:
    if not text:
        return ""
    out = _TAG_RE.sub(" ", text)
    out = unescape(out)
    out = _WS_RE.sub(" ", out).strip()
    return out


def _extract_block_by_class(html: str, class_token: str) -> str:
    """
    Best-effort extraction for a single div block by class token.
    """
    m = re.search(
        rf'<div[^>]*class="[^"]*\b{re.escape(class_token)}\b[^"]*"[^>]*>(.*?)</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return m.group(1) if m else ""


def _extract_results_entries(html: str) -> List[Dict[str, object]]:
    rows: List[Dict[str, object]] = []
    seen = set()
    skipped_not_played = 0
    with_match_date = 0

    for m in _RESULT_LINK_RE.finditer(html):
        href = m.group("href")
        body = m.group("body") or ""
        if "result-score" not in body.lower():
            continue
        if href in seen:
            continue

        team1_block = _extract_block_by_class(body, "team1")
        team2_block = _extract_block_by_class(body, "team2")
        team1 = _strip_html(team1_block)
        team2 = _strip_html(team2_block)
        if not team1 or not team2:
            continue

        body_text = _strip_html(body)
        score1 = None
        score2 = None

        # First try generic score pattern across the full card text.
        m_score = re.search(r"(?<!\d)(\d{1,2})\s*[-:]\s*(\d{1,2})(?!\d)", body_text)
        if m_score:
            score1 = int(m_score.group(1))
            score2 = int(m_score.group(2))
        else:
            score_block = _extract_block_by_class(body, "result-score")
            score_vals = re.findall(r"(\d+)", _strip_html(score_block))
            score1 = int(score_vals[0]) if len(score_vals) >= 1 else None
            score2 = int(score_vals[1]) if len(score_vals) >= 2 else None

        # On /results, upcoming/unplayed cards often appear as 0-0 or without winner markers.
        # We only keep completed matches for training/evaluation datasets.
        body_l = body.lower()
        has_winner_marker = ("team1 won" in body_l) or ("team2 won" in body_l) or ("won" in body_l and "team" in body_l)
        if (
            score1 is None
            or score2 is None
            or (score1 == 0 and score2 == 0 and not has_winner_marker)
        ):
            skipped_not_played += 1
            continue

        winner = None
        if score1 is not None and score2 is not None:
            if score1 > score2:
                winner = team1
            elif score2 > score1:
                winner = team2
            else:
                winner = "Draw"

        event_block = (
            _extract_block_by_class(body, "event-name")
            or _extract_block_by_class(body, "event")
            or _extract_block_by_class(body, "match-event")
            or _extract_block_by_class(body, "matchEvent")
        )
        event_name = _strip_html(event_block)
        if not event_name:
            m_event = _EVENT_LINK_RE.search(body)
            if m_event:
                event_name = _strip_html(m_event.group("name"))
        if not event_name:
            # On HLTV /results, event labels are often outside the match anchor.
            # Use nearest preceding /events/... link as a contextual fallback.
            lookback = html[max(0, m.start() - 5000):m.start()]
            event_matches = list(_EVENT_LINK_RE.finditer(lookback))
            if event_matches:
                event_name = _strip_html(event_matches[-1].group("name"))

        match_date = None
        # Prefer unix timestamp embedded near the result card.
        local_window = html[max(0, m.start() - 12000): min(len(html), m.end() + 1500)]
        unix_matches = list(_UNIX_ATTR_RE.finditer(local_window))
        if unix_matches:
            raw_ts = int(unix_matches[-1].group("ts"))
            if raw_ts > 10_000_000_000:
                raw_ts = raw_ts // 1000
            try:
                match_date = datetime.fromtimestamp(raw_ts, tz=timezone.utc).date().isoformat()
            except Exception:
                match_date = None

        # Fallback to nearby date heading text.
        if not match_date:
            lookback = html[max(0, m.start() - 24000):m.start()]
            lookback_text = _strip_html(lookback)
            date_matches = list(_DATE_HEADER_RE.finditer(lookback_text))
            if date_matches:
                date_text = date_matches[-1].group(0)
                date_text = re.sub(r"^Results\s+for\s+", "", date_text, flags=re.IGNORECASE).strip()
                date_text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_text, flags=re.IGNORECASE)
                date_text = date_text.replace(",", "")
                for fmt in ("%B %d %Y", "%b %d %Y"):
                    try:
                        match_date = datetime.strptime(date_text, fmt).date().isoformat()
                        break
                    except Exception:
                        continue
            if not match_date:
                month_matches = list(_MONTH_DAY_YEAR_RE.finditer(lookback_text))
                if month_matches:
                    date_text = month_matches[-1].group(0)
                    date_text = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_text, flags=re.IGNORECASE).replace(",", "")
                    for fmt in ("%B %d %Y", "%b %d %Y"):
                        try:
                            match_date = datetime.strptime(date_text, fmt).date().isoformat()
                            break
                        except Exception:
                            continue

        rows.append(
            {
                "team1": team1,
                "team2": team2,
                "score1": score1,
                "score2": score2,
                "winner": winner,
                "event": event_name or None,
                "match_date": match_date,
                "match_url": f"https://www.hltv.org{href}",
            }
        )
        if match_date:
            with_match_date += 1
        seen.add(href)

    logger.info(
        "HLTV results parse: kept=%d with_match_date=%d missing_match_date=%d skipped_not_played=%d",
        len(rows),
        with_match_date,
        max(0, len(rows) - with_match_date),
        skipped_not_played,
    )
    return rows


def get_recent_hltv_results(limit: int = 100, offsets: List[int] | None = None) -> Dict[str, object]:
    limit = max(1, min(100000, int(limit)))
    out: List[Dict[str, object]] = []
    last_error: Exception | None = None

    # Try a few offsets if needed to reach requested volume.
    use_offsets = [int(x) for x in (offsets or [0, 100, 200])]
    for offset in use_offsets:
        if len(out) >= limit:
            break
        url = _build_results_url(offset)
        logger.info("HLTV results fetch start: url=%s", url)

        html = None
        try:
            html = _fetch_html_with_uc_driver(url, wait_text=None)
            logger.info("HLTV SeleniumBase UC results fetch succeeded: url=%s", url)
        except Exception as exc:
            logger.warning("HLTV SeleniumBase UC results fetch failed: url=%s error=%s", url, exc)
            last_error = exc
            continue

        try:
            parsed = _extract_results_entries(html)
            for row in parsed:
                row["source_offset"] = int(offset)
                if row.get("match_id") is None:
                    mm = re.search(r"/matches/(\d+)/", str(row.get("match_url") or ""))
                    if mm:
                        row["match_id"] = int(mm.group(1))
            if not parsed and _is_cloudflare_challenge_html(html):
                raise HLTVRankingError(
                    "HLTV results page is behind a Cloudflare/interstitial challenge. "
                    "Open once in visible browser mode and complete challenge, then retry."
                )
            logger.info("HLTV results parse complete: url=%s matches=%d", url, len(parsed))
            if parsed:
                out.extend(parsed)
        except Exception as parse_exc:
            last_error = parse_exc
            logger.warning("HLTV results parse failed: url=%s error=%s", url, parse_exc)
            continue

    dedup = []
    seen_urls = set()
    for r in out:
        u = r.get("match_url")
        if u in seen_urls:
            continue
        dedup.append(r)
        seen_urls.add(u)
        if len(dedup) >= limit:
            break

    if not dedup and last_error is not None:
        raise HLTVRankingError(f"Failed to fetch HLTV recent results: {last_error}") from last_error

    return {
        "source": "https://www.hltv.org/results",
        "count": len(dedup),
        "requested_limit": limit,
        "offsets": use_offsets,
        "results": dedup,
    }


def get_hltv_match_details(match_url: str) -> Dict[str, object]:
    url = str(match_url or "").strip()
    if not url:
        raise HLTVRankingError("match_url is required")
    if url.startswith("/matches/"):
        url = f"https://www.hltv.org{url}"
    if "/matches/" not in url:
        raise HLTVRankingError("Invalid HLTV match URL")

    html = None
    try:
        html = _fetch_html_with_uc_driver(url, wait_text=None)
    except Exception as exc:
        raise HLTVRankingError(f"Failed to fetch HLTV match details with SeleniumBase UC: {exc}") from exc

    text = _strip_html(html)
    maps: List[Dict[str, object]] = []
    for m in _MAP_LINE_RE.finditer(text):
        map_name = str(m.group("map") or "").strip()
        s1 = int(m.group("score1"))
        s2 = int(m.group("score2"))
        key = (map_name.lower(), s1, s2)
        if any((str(x.get("map", "")).lower(), int(x.get("score1", -1)), int(x.get("score2", -1))) == key for x in maps):
            continue
        maps.append(
            {
                "map": map_name,
                "score1": s1,
                "score2": s2,
            }
        )

    return {
        "match_url": url,
        "maps": maps,
    }


def _get_rankings_map_for_date(
    on_date: date, allow_undated_fallback: bool = True
) -> tuple[Dict[str, Dict[str, int | str]], str]:
    last_error: Exception | None = None

    urls = [_build_hltv_ranking_url(on_date)]
    if allow_undated_fallback:
        urls.append(_build_hltv_ranking_url_undated())

    for url in urls:
        logger.info("HLTV ranking page fetch start: date=%s url=%s", on_date.isoformat(), url)

        html = None
        try:
            html = _fetch_html_with_uc_driver(url)
            logger.info("HLTV SeleniumBase UC fetch succeeded: date=%s url=%s", on_date.isoformat(), url)
        except Exception as exc:
            logger.warning("HLTV SeleniumBase UC fetch failed: date=%s url=%s error=%s", on_date.isoformat(), url, exc)
            last_error = exc
            continue

        try:
            rankings = _extract_rankings(html)
            if "/ranking/teams/" in url:
                logger.info("HLTV used dated ranking URL for date=%s", on_date.isoformat())
            else:
                logger.info("HLTV fallback to undated ranking URL for date=%s", on_date.isoformat())
            return rankings, url
        except RankingPageParseError as parse_exc:
            last_error = parse_exc
            logger.warning("HLTV ranking parse failed for url=%s: %s", url, parse_exc)
            continue

    if last_error is not None:
        raise HLTVRankingError(f"Failed to fetch HLTV rankings page: {last_error}") from last_error
    raise HLTVRankingError("Failed to fetch HLTV rankings page: unknown error")


def _get_vrs_rankings_map_for_date(
    on_date: date, allow_undated_fallback: bool = True
) -> tuple[Dict[str, Dict[str, int | str]], str]:
    last_error: Exception | None = None
    urls = [_build_vrs_ranking_url(on_date)]
    if allow_undated_fallback:
        urls.append(_build_vrs_ranking_url_undated())

    for url in urls:
        logger.info("VRS ranking page fetch start: date=%s url=%s", on_date.isoformat(), url)
        html = None
        try:
            html = _fetch_html_with_uc_driver(url, wait_text="points")
            logger.info("VRS SeleniumBase UC fetch succeeded: date=%s url=%s", on_date.isoformat(), url)
        except Exception as exc:
            logger.warning("VRS SeleniumBase UC fetch failed: date=%s url=%s error=%s", on_date.isoformat(), url, exc)
            last_error = exc
            continue

        try:
            rankings = _extract_vrs_rankings(html)
            if "/valve-ranking/teams/" in url:
                logger.info("VRS used dated ranking URL for date=%s", on_date.isoformat())
            else:
                logger.info("VRS fallback to undated ranking URL for date=%s", on_date.isoformat())
            return rankings, url
        except RankingPageParseError as parse_exc:
            last_error = parse_exc
            logger.warning("VRS ranking parse failed for url=%s: %s", url, parse_exc)
            continue

    if last_error is not None:
        raise HLTVRankingError(f"Failed to fetch VRS rankings page: {last_error}") from last_error
    raise HLTVRankingError("Failed to fetch VRS rankings page: unknown error")


def get_all_hltv_rankings_on_or_before_date(
    on_or_before_date: date, max_days_back: int = 7
) -> Dict[str, object]:
    """
    Fetch one HLTV ranking snapshot on or before the requested date and return all teams.
    """
    if max_days_back < 0:
        raise ValueError("max_days_back must be >= 0")

    for days_back in range(max_days_back + 1):
        probe_date = on_or_before_date.fromordinal(on_or_before_date.toordinal() - days_back)
        try:
            rankings, url = _get_rankings_map_for_date(probe_date, allow_undated_fallback=False)
            if rankings:
                return {
                    "requested_date": on_or_before_date.isoformat(),
                    "effective_date": probe_date.isoformat(),
                    "days_back": days_back,
                    "url": url,
                    "rankings_by_team": rankings,
                }
        except RankingPageParseError:
            continue

    raise RankingPageParseError(
        f"Could not parse HLTV ranking entries from {on_or_before_date.isoformat()} "
        f"back {max_days_back} day(s)."
    )


def get_all_vrs_rankings_on_or_before_date(
    on_or_before_date: date, max_days_back: int = 7
) -> Dict[str, object]:
    if max_days_back < 0:
        raise ValueError("max_days_back must be >= 0")

    for days_back in range(max_days_back + 1):
        probe_date = on_or_before_date.fromordinal(on_or_before_date.toordinal() - days_back)
        try:
            rankings, url = _get_vrs_rankings_map_for_date(probe_date, allow_undated_fallback=False)
            if rankings:
                return {
                    "requested_date": on_or_before_date.isoformat(),
                    "effective_date": probe_date.isoformat(),
                    "days_back": days_back,
                    "url": url,
                    "rankings_by_team": rankings,
                }
        except RankingPageParseError:
            continue

    raise RankingPageParseError(
        f"Could not parse VRS ranking entries from {on_or_before_date.isoformat()} "
        f"back {max_days_back} day(s)."
    )


def get_latest_hltv_rankings() -> Dict[str, object]:
    """
    Fetch the latest HLTV team ranking snapshot from the undated ranking URL.
    """
    url = _build_hltv_ranking_url_undated()
    today = date.today().isoformat()
    logger.info("HLTV latest ranking fetch start: url=%s", url)

    html = None
    try:
        html = _fetch_html_with_uc_driver(url)
        logger.info("HLTV SeleniumBase UC fetch succeeded for latest ranking")
    except Exception as exc:
        logger.warning("HLTV SeleniumBase UC fetch failed for latest ranking: %s", exc)
        raise HLTVRankingError(f"Failed to fetch HLTV rankings page: {exc}") from exc

    rankings = _extract_rankings(html)
    logger.info("HLTV latest ranking parse succeeded: teams=%d", len(rankings))
    return {
        "requested_date": today,
        "effective_date": "latest",
        "days_back": 0,
        "url": url,
        "rankings_by_team": rankings,
    }


def get_latest_vrs_rankings() -> Dict[str, object]:
    url = _build_vrs_ranking_url_undated()
    today = date.today().isoformat()
    logger.info("VRS latest ranking fetch start: url=%s", url)

    html = None
    try:
        html = _fetch_html_with_uc_driver(url, wait_text="points")
        logger.info("VRS SeleniumBase UC fetch succeeded for latest ranking")
    except Exception as exc:
        logger.warning("VRS SeleniumBase UC fetch failed for latest ranking: %s", exc)
        raise HLTVRankingError(f"Failed to fetch VRS rankings page: {exc}") from exc

    rankings = _extract_vrs_rankings(html)
    logger.info("VRS latest ranking parse succeeded: teams=%d", len(rankings))
    return {
        "requested_date": today,
        "effective_date": "latest",
        "days_back": 0,
        "url": url,
        "rankings_by_team": rankings,
    }


def get_team_hltv_rating_and_points_on_date(team_name: str, on_date: date) -> Dict[str, int | str]:
    normalized_team = team_name.strip().lower()
    if not normalized_team:
        raise ValueError("team_name is required")

    rankings, url = _get_rankings_map_for_date(on_date)
    data = rankings.get(normalized_team)
    if not data:
        raise TeamNotRankedError(f"Team '{team_name}' was not found in HLTV ranking for {on_date.isoformat()}.")

    return {
        "team_name": str(data["team_name"]),
        "date": on_date.isoformat(),
        "url": url,
        "hltv_rating": int(data["hltv_rating"]),
        "points": int(data["points"]),
    }


def get_team_hltv_rating_and_points_on_or_before_date(
    team_name: str, on_or_before_date: date, max_days_back: int = 7
) -> Dict[str, int | str]:
    normalized_team = team_name.strip().lower()
    if not normalized_team:
        raise ValueError("team_name is required")

    snapshot = get_all_hltv_rankings_on_or_before_date(on_or_before_date, max_days_back=max_days_back)
    rankings = snapshot["rankings_by_team"]
    data = rankings.get(normalized_team)
    if not data:
        raise TeamNotRankedError(
            f"Team '{team_name}' was not found in HLTV rankings from "
            f"{on_or_before_date.isoformat()} back {max_days_back} day(s)."
        )

    days_back = int(snapshot["days_back"])
    if days_back > 0:
        logger.info(
            "HLTV fallback used: team='%s' requested=%s effective=%s days_back=%d",
            team_name,
            snapshot["requested_date"],
            snapshot["effective_date"],
            days_back,
        )

    return {
        "team_name": str(data["team_name"]),
        "date": str(snapshot["effective_date"]),
        "requested_date": str(snapshot["requested_date"]),
        "effective_date": str(snapshot["effective_date"]),
        "days_back": days_back,
        "url": str(snapshot["url"]),
        "hltv_rating": int(data["hltv_rating"]),
        "points": int(data["points"]),
    }


def get_team_vrs_rank_and_points_on_date(team_name: str, on_date: date) -> Dict[str, int | str]:
    normalized_team = team_name.strip().lower()
    if not normalized_team:
        raise ValueError("team_name is required")

    rankings, url = _get_vrs_rankings_map_for_date(on_date)
    data = rankings.get(normalized_team)
    if not data:
        raise TeamNotRankedError(f"Team '{team_name}' was not found in VRS ranking for {on_date.isoformat()}.")

    return {
        "team_name": str(data["team_name"]),
        "date": on_date.isoformat(),
        "url": url,
        "vrs_rank": int(data["vrs_rank"]),
        "points": int(data["points"]),
    }


def get_team_vrs_rank_and_points_on_or_before_date(
    team_name: str, on_or_before_date: date, max_days_back: int = 7
) -> Dict[str, int | str]:
    normalized_team = team_name.strip().lower()
    if not normalized_team:
        raise ValueError("team_name is required")

    if max_days_back < 0:
        raise ValueError("max_days_back must be >= 0")

    for days_back in range(max_days_back + 1):
        probe_date = on_or_before_date.fromordinal(on_or_before_date.toordinal() - days_back)
        rankings, url = _get_vrs_rankings_map_for_date(probe_date)
        data = rankings.get(normalized_team)
        if data:
            return {
                "team_name": str(data["team_name"]),
                "date": probe_date.isoformat(),
                "requested_date": on_or_before_date.isoformat(),
                "effective_date": probe_date.isoformat(),
                "days_back": int(days_back),
                "url": url,
                "vrs_rank": int(data["vrs_rank"]),
                "points": int(data["points"]),
            }

    raise TeamNotRankedError(
        f"Team '{team_name}' was not found in VRS rankings from "
        f"{on_or_before_date.isoformat()} back {max_days_back} day(s)."
    )
