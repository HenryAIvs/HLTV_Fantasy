import argparse
import logging
import os
from pathlib import Path
import random
import re
import threading
import time
import unicodedata
from datetime import date
from html import unescape

from dateutil.relativedelta import relativedelta


logger = logging.getLogger(__name__)
DEFAULT_TOP_BUCKETS = (5, 10, 20, 30, 50)

_HLTV_PLAYER_STATS_URL = (
    "https://www.hltv.org/stats/players/{player_id}/{slug}?startDate={start}&endDate={end}"
)
_HLTV_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/123.0.0.0 Safari/537.36"
)

# Regex fallback for HTML parsing (used only when caller has raw HTML)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.IGNORECASE | re.DOTALL)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.IGNORECASE | re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_FEATURED_RATING_RE = re.compile(
    r"(?P<rating>\d+\.\d+)\s*vs\s*top\s*(?P<tier>\d+)\s*opponents.*?\(\s*(?P<maps>\d+)\s*maps?\s*\)",
    re.IGNORECASE,
)

_CHROME_FETCH_LOCK = threading.Lock()
# Seconds to sleep between consecutive page fetches to avoid Cloudflare rate-limits.
# Can be overridden via env vars HLTV_FETCH_DELAY_MIN / HLTV_FETCH_DELAY_MAX.
_FETCH_DELAY_MIN: float = float(os.getenv("HLTV_FETCH_DELAY_MIN", "8"))
_FETCH_DELAY_MAX: float = float(os.getenv("HLTV_FETCH_DELAY_MAX", "15"))
_CF_MANUAL_TIMEOUT_SEC: int = int(os.getenv("HLTV_CF_MANUAL_TIMEOUT_SEC", "90"))
_LAST_FETCH_TIME: float = 0.0


class HLTVFeaturedRatingsError(RuntimeError):
    """Base error for HLTV player featured ratings lookups."""


class FeaturedRatingsParseError(HLTVFeaturedRatingsError):
    """Raised when the HLTV stats page does not contain parseable featured ratings."""


def slugify_hltv_player_name(name: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(name or ""))
    ascii_name = raw.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name).strip("-").lower()
    return slug or "player"


def _last_6_months_range(end: date | None = None) -> tuple[str, str]:
    end_d = end or date.today()
    start_d = end_d - relativedelta(months=6)
    return start_d.isoformat(), end_d.isoformat()


def build_hltv_player_stats_url(
    player_id: int,
    player_name: str | None = None,
    *,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    pid = int(player_id)
    if pid <= 0:
        raise ValueError("player_id must be a positive integer")
    start, end = start_date, end_date
    if start is None or end is None:
        start, end = _last_6_months_range()
    return _HLTV_PLAYER_STATS_URL.format(
        player_id=pid,
        slug=slugify_hltv_player_name(player_name),
        start=start,
        end=end,
    )


def _is_cloudflare_challenge_html(html: str) -> bool:
    html_l = (html or "").lower()
    return (
        "just a moment" in html_l
        or "/cdn-cgi/challenge-platform" in html_l
        or "cf-chl-" in html_l
        or "challenge-platform" in html_l
        or "ray id:" in html_l
    )


def _resolve_profile_dir() -> Path:
    configured = os.getenv("HLTV_PROFILE_DIR", "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        # Legacy profile folder is often unstable for Playwright persistent contexts.
        if configured_path.name.lower() == "hltv_profile":
            migrated = configured_path.parent / "hltv_profile_playwright"
            logger.warning(
                "HLTV_PROFILE_DIR points to legacy '%s'; using '%s' instead.",
                configured_path,
                migrated,
            )
            return migrated.resolve()
        return configured_path
    # backend/services/<this_file> -> repo root is parents[2]
    return (Path(__file__).resolve().parents[2] / "hltv_profile_playwright").resolve()


def _cleanup_profile_locks(profile_dir: Path) -> None:
    # Stale Chromium singleton locks can prevent persistent profile startup.
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = profile_dir / name
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception as exc:
            logger.debug("Unable to remove %s: %s", lock_path, exc)


def _wait_for_featured_after_challenge(page, *, timeout_ms: int) -> bool:
    deadline = time.monotonic() + (max(1000, timeout_ms) / 1000.0)
    while time.monotonic() < deadline:
        try:
            page.get_by_text("Featured ratings", exact=False).wait_for(timeout=2500)
            return True
        except Exception:
            pass
        page.wait_for_timeout(1500)
    return False


def _parse_card_text(card_text: str) -> dict:
    rating_m = re.search(r"\b(\d+\.\d+)\b", card_text)
    maps_m = re.search(r"\((\d+)\s+maps\)", card_text, flags=re.IGNORECASE)
    top_m = re.search(r"vs\s+top\s+(\d+)\s+opponents", card_text, flags=re.IGNORECASE)
    parsed: dict = {"raw": card_text.strip()}
    if rating_m:
        parsed["rating"] = float(rating_m.group(1))
    if maps_m:
        parsed["maps"] = int(maps_m.group(1))
    if top_m:
        parsed["top"] = int(top_m.group(1))
    return parsed


def _fetch_featured_ratings_via_dom(
    page,
    tops: tuple[int, ...],
    *,
    timeout_ms: int,
    headless: bool,
) -> dict[int, dict]:
    """Use DOM selectors to extract featured ratings — the original working approach."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

    try:
        page.get_by_text("Featured ratings", exact=False).wait_for(timeout=min(timeout_ms, 20000))
    except PlaywrightTimeoutError:
        html = page.content().lower()
        if _is_cloudflare_challenge_html(html):
            if not headless and _wait_for_featured_after_challenge(
                page, timeout_ms=_CF_MANUAL_TIMEOUT_SEC * 1000
            ):
                return _fetch_featured_ratings_via_dom(
                    page,
                    tops,
                    timeout_ms=timeout_ms,
                    headless=headless,
                )
            raise HLTVFeaturedRatingsError(
                "Hit Cloudflare interstitial. Complete the browser challenge and retry. "
                "If needed, increase HLTV_CF_MANUAL_TIMEOUT_SEC."
            )
        raise HLTVFeaturedRatingsError(
            "Could not find 'Featured ratings' on the HLTV stats page (page layout may have changed or not loaded)."
        )

    featured: dict[int, dict] = {}
    for top_x in tops:
        label = f"vs top {top_x} opponents"
        try:
            el = page.get_by_text(label, exact=False).first
            container = el.locator("xpath=ancestor::*[self::div or self::a][1]")
            text = container.inner_text()
            parsed = _parse_card_text(text)
            rating = parsed.get("rating")
            maps = parsed.get("maps")
            if rating is not None and maps is not None:
                featured[top_x] = {"rating": rating, "maps": maps}
            else:
                featured[top_x] = {"rating": rating, "maps": maps, "raw": parsed.get("raw")}
        except Exception as exc:
            logger.warning("DOM extraction failed for top %d: %s", top_x, exc)

    return featured


def get_featured_ratings(
    player_id: int,
    *,
    player_name: str | None = None,
    tops: list[int] | tuple[int, ...] | None = None,
    timeout: int = 45,
) -> dict:
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:
        raise HLTVFeaturedRatingsError(
            f"Playwright is not available for HLTV player stats scraping: {exc}"
        ) from exc

    global _LAST_FETCH_TIME
    buckets = tuple(tops or DEFAULT_TOP_BUCKETS)
    start_date, end_date = _last_6_months_range()
    url = build_hltv_player_stats_url(
        player_id, player_name, start_date=start_date, end_date=end_date
    )
    headless = os.getenv("HLTV_HEADLESS", "0") == "1"
    timeout_ms = max(15000, int(timeout * 1000))

    with _CHROME_FETCH_LOCK:
        # Enforce a random delay between requests to avoid Cloudflare rate-limits.
        elapsed = time.monotonic() - _LAST_FETCH_TIME
        delay = random.uniform(_FETCH_DELAY_MIN, _FETCH_DELAY_MAX)
        if elapsed < delay:
            time.sleep(delay - elapsed)

        with sync_playwright() as pw:
            profile_dir = _resolve_profile_dir()
            profile_dir.mkdir(parents=True, exist_ok=True)
            _cleanup_profile_locks(profile_dir)
            browser = None
            using_persistent_profile = True
            try:
                context = pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=headless,
                    slow_mo=50 if not headless else 0,
                    viewport={"width": 1400, "height": 900},
                    user_agent=_HLTV_USER_AGENT,
                )
            except Exception as exc:
                # Fallback prevents total failure if profile dir is corrupted/locked.
                logger.warning(
                    "Persistent HLTV profile launch failed (%s). Falling back to ephemeral context.",
                    exc,
                )
                using_persistent_profile = False
                browser = pw.chromium.launch(
                    headless=headless,
                    slow_mo=50 if not headless else 0,
                )
                context = browser.new_context(
                    viewport={"width": 1400, "height": 900},
                    user_agent=_HLTV_USER_AGENT,
                )

            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                try:
                    page.locator("button:has-text('Allow all cookies')").click(timeout=2500)
                except Exception:
                    pass

                featured = _fetch_featured_ratings_via_dom(
                    page,
                    buckets,
                    timeout_ms=timeout_ms,
                    headless=headless,
                )
                _LAST_FETCH_TIME = time.monotonic()
                logger.info(
                    "HLTV player stats page loaded: title='%s' final_url=%s headless=%s profile_dir=%s persistent=%s",
                    page.title(),
                    page.url,
                    headless,
                    profile_dir,
                    using_persistent_profile,
                )
            finally:
                context.close()
                if browser is not None:
                    browser.close()

    if not featured:
        raise FeaturedRatingsParseError(
            "Could not parse 'vs top X opponents' entries from the HLTV stats page."
        )

    return {
        "player_id": int(player_id),
        "player_name": player_name,
        "url": url,
        "startDate": start_date,
        "endDate": end_date,
        "featured_ratings": featured,
    }


def parse_featured_ratings_html(html: str, *, tops: list[int] | tuple[int, ...] | None = None) -> dict[int, dict[str, float | int]]:
    """Fallback HTML regex parser — used only when caller has raw HTML."""
    wanted = {int(t) for t in (tops or DEFAULT_TOP_BUCKETS)}
    html_no_scripts = _SCRIPT_RE.sub(" ", html or "")
    html_no_scripts = _STYLE_RE.sub(" ", html_no_scripts)
    text = _TAG_RE.sub(" ", html_no_scripts)
    text = unescape(text)
    text = _WS_RE.sub(" ", text).strip()

    featured: dict[int, dict[str, float | int]] = {}
    for match in _FEATURED_RATING_RE.finditer(text):
        tier = int(match.group("tier"))
        if tier not in wanted:
            continue
        rating = float(match.group("rating"))
        maps = int(match.group("maps"))
        existing = featured.get(tier)
        if existing is None or int(existing.get("maps") or 0) < maps:
            featured[tier] = {"rating": rating, "maps": maps}

    if featured:
        return featured
    if _is_cloudflare_challenge_html(html):
        raise HLTVFeaturedRatingsError(
            "HLTV returned a Cloudflare/interstitial page instead of player stats HTML."
        )
    raise FeaturedRatingsParseError("Could not parse 'vs top X opponents' entries from the HLTV stats page.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a player's HLTV stats URL.")
    parser.add_argument("player_id", type=int, help="HLTV player id")
    parser.add_argument("--name", default="", help="Optional player name to slugify into the URL")
    args = parser.parse_args()
    print(build_hltv_player_stats_url(args.player_id, args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
