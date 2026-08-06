import argparse
import logging
import re
import unicodedata
from datetime import date
from html import unescape

from dateutil.relativedelta import relativedelta

from backend.services.hltv_browser import HLTVBrowserError, fetch_hltv_html


logger = logging.getLogger(__name__)
DEFAULT_TOP_BUCKETS = (5, 10, 20, 30, 50)

_HLTV_PLAYER_STATS_URL = (
    "https://www.hltv.org/stats/players/{player_id}/{slug}?startDate={start}&endDate={end}&csVersion=CS2"
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
_OVERALL_RATING_PATTERNS = (
    re.compile(r"(?P<rating>\d+\.\d+)\s*(?:[A-Z]+\s+)?Rating\s*3\.0", re.IGNORECASE),
    re.compile(r"Rating\s*3\.0\s*(?:[A-Z]+\s+)?(?P<rating>\d+\.\d+)", re.IGNORECASE),
)

class HLTVFeaturedRatingsError(RuntimeError):
    """Base error for HLTV player featured ratings lookups."""


class FeaturedRatingsParseError(HLTVFeaturedRatingsError):
    """Raised when the HLTV stats page does not contain parseable featured ratings."""


def slugify_hltv_player_name(name: str | None) -> str:
    raw = unicodedata.normalize("NFKD", str(name or ""))
    ascii_name = raw.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_name).strip("-").lower()
    return slug or "player"


def _last_3_months_range(end: date | None = None) -> tuple[str, str]:
    end_d = end or date.today()
    start_d = end_d - relativedelta(months=3)
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
        start, end = _last_3_months_range()
    return _HLTV_PLAYER_STATS_URL.format(
        player_id=pid,
        slug=slugify_hltv_player_name(player_name),
        start=start,
        end=end,
    )


def _is_cloudflare_challenge_html(html: str) -> bool:
    """True only for an actual interstitial/challenge page. Cloudflare injects a
    passive '/cdn-cgi/challenge-platform' script into EVERY normal page, so
    matching that (or the generic 'challenge-platform') falsely flags real,
    fully-loaded stats pages — the misdiagnosis behind the wrong error."""
    html_l = (html or "").lower()
    if "hltv statistics" in html_l or "featured ratings" in html_l:
        return False  # a real stats page loaded
    return (
        "just a moment" in html_l
        or "cf-chl-" in html_l
        or "challenge-running" in html_l
        or 'id="challenge-form"' in html_l
        or "checking your browser before accessing" in html_l
        or ("verify you are human" in html_l and "/stats/" not in html_l)
    )


def get_featured_ratings(
    player_id: int,
    *,
    player_name: str | None = None,
    tops: list[int] | tuple[int, ...] | None = None,
    timeout: int = 45,
) -> dict:
    buckets = tuple(tops or DEFAULT_TOP_BUCKETS)
    start_date, end_date = _last_3_months_range()
    url = build_hltv_player_stats_url(
        player_id, player_name, start_date=start_date, end_date=end_date
    )
    timeout_ms = max(15000, int(timeout * 1000))

    try:
        html = fetch_hltv_html(url, timeout_ms=timeout_ms, wait_text="Featured ratings")
        if _is_cloudflare_challenge_html(html):
            raise HLTVFeaturedRatingsError(
                "HLTV returned a Cloudflare/interstitial page instead of player stats HTML."
            )
        try:
            featured = parse_featured_ratings_html(html, tops=buckets)
        except FeaturedRatingsParseError:
            featured = {}
        overall_rating = parse_overall_rating_html(html)
    except HLTVBrowserError as exc:
        raise HLTVFeaturedRatingsError(str(exc)) from exc

    # A player with few maps vs top opponents legitimately has no per-tier
    # entries; as long as the page loaded and gave an overall rating, store it —
    # the missing tiers are filled later from the average degradation curve.
    if not featured and overall_rating is None:
        raise FeaturedRatingsParseError(
            "Could not parse ratings from the HLTV stats page (empty or unexpected content)."
        )

    return {
        "player_id": int(player_id),
        "player_name": player_name,
        "url": url,
        "startDate": start_date,
        "endDate": end_date,
        "overall_rating": overall_rating,
        "featured_ratings": featured,
    }


def _html_to_text(html: str) -> str:
    html_no_scripts = _SCRIPT_RE.sub(" ", html or "")
    html_no_scripts = _STYLE_RE.sub(" ", html_no_scripts)
    text = _TAG_RE.sub(" ", html_no_scripts)
    text = unescape(text)
    return _WS_RE.sub(" ", text).strip()


def parse_overall_rating_html(html: str) -> float | None:
    text = _html_to_text(html)
    for pattern in _OVERALL_RATING_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        value = float(match.group("rating"))
        if 0.0 < value <= 3.0:
            return value
    return None


def parse_featured_ratings_html(html: str, *, tops: list[int] | tuple[int, ...] | None = None) -> dict[int, dict[str, float | int]]:
    """Fallback HTML regex parser — used only when caller has raw HTML."""
    wanted = {int(t) for t in (tops or DEFAULT_TOP_BUCKETS)}
    text = _html_to_text(html)

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
