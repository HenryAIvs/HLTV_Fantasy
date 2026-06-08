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
    "https://www.hltv.org/stats/players/{player_id}/{slug}?startDate={start}&endDate={end}"
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


def get_featured_ratings(
    player_id: int,
    *,
    player_name: str | None = None,
    tops: list[int] | tuple[int, ...] | None = None,
    timeout: int = 45,
) -> dict:
    buckets = tuple(tops or DEFAULT_TOP_BUCKETS)
    start_date, end_date = _last_6_months_range()
    url = build_hltv_player_stats_url(
        player_id, player_name, start_date=start_date, end_date=end_date
    )
    timeout_ms = max(15000, int(timeout * 1000))

    try:
        html = fetch_hltv_html(url, timeout_ms=timeout_ms, wait_text="Featured ratings")
        featured = parse_featured_ratings_html(html, tops=buckets)
    except HLTVBrowserError as exc:
        raise HLTVFeaturedRatingsError(str(exc)) from exc

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
