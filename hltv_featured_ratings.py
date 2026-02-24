import argparse
import json
import random
import re
import time
from datetime import date
from pathlib import Path

from dateutil.relativedelta import relativedelta
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


HLTV_PLAYER_STATS_URL = (
    "https://www.hltv.org/stats/players/{player_id}/{slug}?startDate={start}&endDate={end}"
)
DEFAULT_TOP_BUCKETS = [5, 10, 20, 30, 50]


def last_6_months_range(end: date | None = None) -> tuple[str, str]:
    end_d = end or date.today()
    start_d = end_d - relativedelta(months=6)
    return start_d.isoformat(), end_d.isoformat()


def _parse_card_text(card_text: str) -> dict:
    rating_m = re.search(r"\b(\d+\.\d+)\b", card_text)
    maps_m = re.search(r"\((\d+)\s+maps\)", card_text, flags=re.IGNORECASE)
    top_m = re.search(r"vs\s+top\s+(\d+)\s+opponents", card_text, flags=re.IGNORECASE)

    parsed = {"raw": card_text.strip()}
    if rating_m:
        parsed["rating"] = float(rating_m.group(1))
    if maps_m:
        parsed["maps"] = int(maps_m.group(1))
    if top_m:
        parsed["top"] = int(top_m.group(1))
    return parsed


def get_featured_ratings(
    player_id: int,
    *,
    tops: list[int] | None = None,
    headed: bool = True,
) -> dict:
    start, end_str = last_6_months_range()
    url = HLTV_PLAYER_STATS_URL.format(
        player_id=player_id,
        slug="player",
        start=start,
        end=end_str,
    )
    buckets = tops or DEFAULT_TOP_BUCKETS

    with sync_playwright() as p:
        launch_kwargs = {
            "headless": not headed,
            "slow_mo": 50 if headed else 0,
        }
        browser = p.chromium.launch(**launch_kwargs)
        context_kwargs = {
            "viewport": {"width": 1400, "height": 900},
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/123.0.0.0 Safari/537.36"
            ),
        }
        context = browser.new_context(**context_kwargs)
        page = context.new_page()
        page.goto(url, wait_until="domcontentloaded")

        try:
            page.get_by_text("Featured ratings", exact=False).wait_for(timeout=20000)
        except PlaywrightTimeoutError:
            html = page.content().lower()
            browser.close()
            if "cloudflare" in html or "cf-" in html:
                raise RuntimeError(
                    "Hit Cloudflare interstitial. Re-run without --headless and complete the challenge once."
                )
            raise RuntimeError("Could not find 'Featured ratings' (page layout changed or not loaded).")

        featured: dict[int, dict] = {}
        for top_x in buckets:
            label = f"vs top {top_x} opponents"
            el = page.get_by_text(label, exact=False).first
            container = el.locator("xpath=ancestor::*[self::div or self::a][1]")
            text = container.inner_text()
            parsed = _parse_card_text(text)

            rating = parsed.get("rating")
            maps = parsed.get("maps")
            if rating is None or maps is None:
                featured[top_x] = {"rating": rating, "maps": maps, "raw": parsed.get("raw")}
            else:
                featured[top_x] = {"rating": rating, "maps": maps}

        browser.close()

    return {
        "player_id": player_id,
        "startDate": start,
        "endDate": end_str,
        "featured_ratings": featured,
    }


def get_featured_ratings_batch(
    player_ids: list[int],
    *,
    tops: list[int] | None = None,
    headed: bool = True,
    min_delay_seconds: float = 8.0,
    max_delay_seconds: float = 15.0,
    retries: int = 2,
    retry_backoff_seconds: float = 20.0,
) -> dict:
    if not player_ids:
        raise ValueError("At least one player id is required.")
    if min_delay_seconds < 0 or max_delay_seconds < 0:
        raise ValueError("Delays must be >= 0.")
    if min_delay_seconds > max_delay_seconds:
        raise ValueError("min_delay_seconds cannot be greater than max_delay_seconds.")
    if retries < 0:
        raise ValueError("retries must be >= 0.")

    results = []
    for idx, player_id in enumerate(player_ids):
        attempts = 0
        last_err = None
        data = None
        for attempt in range(retries + 1):
            attempts = attempt + 1
            try:
                data = get_featured_ratings(player_id, tops=tops, headed=headed)
                last_err = None
                break
            except Exception as exc:
                last_err = str(exc)
                if attempt < retries:
                    backoff = retry_backoff_seconds * (attempt + 1)
                    time.sleep(max(0.0, backoff))

        if last_err is None and data is not None:
            results.append(
                {
                    "player_id": player_id,
                    "status": "ok",
                    "attempts": attempts,
                    "data": data,
                }
            )
        else:
            results.append(
                {
                    "player_id": player_id,
                    "status": "error",
                    "attempts": attempts,
                    "error": last_err or "Unknown error",
                }
            )

        if idx < len(player_ids) - 1:
            delay = random.uniform(min_delay_seconds, max_delay_seconds)
            time.sleep(delay)

    ok_count = sum(1 for r in results if r["status"] == "ok")
    return {
        "total": len(player_ids),
        "ok": ok_count,
        "failed": len(player_ids) - ok_count,
        "min_delay_seconds": min_delay_seconds,
        "max_delay_seconds": max_delay_seconds,
        "retries": retries,
        "results": results,
    }


def _parse_tops(values: list[str] | None) -> list[int]:
    if not values:
        return DEFAULT_TOP_BUCKETS
    tops: list[int] = []
    for value in values:
        for token in value.split(","):
            token = token.strip()
            if not token:
                continue
            parsed = int(token)
            if parsed <= 0:
                raise ValueError("Top bucket values must be positive integers.")
            tops.append(parsed)
    if not tops:
        raise ValueError("No valid top bucket values were provided.")
    return sorted(set(tops))


def _parse_player_ids(single_player_id: int | None, values: list[str] | None) -> list[int]:
    raw = []
    if single_player_id is not None:
        raw.append(str(single_player_id))
    for value in values or []:
        raw.extend(part.strip() for part in value.split(","))

    player_ids = []
    for token in raw:
        if not token:
            continue
        parsed = int(token)
        if parsed <= 0:
            raise ValueError("Player ids must be positive integers.")
        player_ids.append(parsed)

    deduped = sorted(set(player_ids))
    if not deduped:
        raise ValueError("At least one player id is required.")
    return deduped


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch HLTV Featured ratings (vs top X opponents) for the last 6 months."
    )
    parser.add_argument("player_id", type=int, nargs="?", help="HLTV player id (e.g. 11893)")
    parser.add_argument(
        "--player-ids",
        nargs="+",
        help="Multiple player ids. Example: --player-ids 11893 7998 or --player-ids 11893,7998",
    )
    parser.add_argument(
        "--tops",
        nargs="+",
        help="Top buckets to fetch. Example: --tops 5 10 20 or --tops 5,10,20",
    )
    parser.add_argument("--headless", action="store_true", help="Run browser in headless mode.")
    parser.add_argument(
        "--output",
        type=str,
        help="Optional output JSON file path. If omitted, prints JSON to stdout.",
    )
    parser.add_argument("--min-delay", type=float, default=8.0, help="Minimum delay in seconds between players.")
    parser.add_argument("--max-delay", type=float, default=15.0, help="Maximum delay in seconds between players.")
    parser.add_argument("--retries", type=int, default=2, help="Retries per player on failure.")
    parser.add_argument(
        "--retry-backoff",
        type=float,
        default=20.0,
        help="Linear backoff base in seconds between retries (attempt index * backoff).",
    )

    args = parser.parse_args()
    try:
        tops = _parse_tops(args.tops)
        player_ids = _parse_player_ids(args.player_id, args.player_ids)
    except ValueError as exc:
        parser.error(str(exc))

    if len(player_ids) == 1:
        data = get_featured_ratings(player_ids[0], tops=tops, headed=not args.headless)
    else:
        data = get_featured_ratings_batch(
            player_ids,
            tops=tops,
            headed=not args.headless,
            min_delay_seconds=args.min_delay,
            max_delay_seconds=args.max_delay,
            retries=args.retries,
            retry_backoff_seconds=args.retry_backoff,
        )

    rendered = json.dumps(data, indent=2)
    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(rendered + "\n", encoding="utf-8")
        print(f"Wrote results to: {out_path}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
