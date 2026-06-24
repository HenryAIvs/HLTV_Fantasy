import json
import logging
import os
from pathlib import Path
import random
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_RECONNECT_TIME = float(os.getenv("HLTV_UC_RECONNECT_TIME", "4"))
_FETCH_DELAY_MIN = float(os.getenv("HLTV_FETCH_DELAY_MIN", "2"))
_FETCH_DELAY_MAX = float(os.getenv("HLTV_FETCH_DELAY_MAX", "5"))
_WAIT_AFTER_LOAD_MIN = float(os.getenv("HLTV_UC_WAIT_AFTER_LOAD_MIN", os.getenv("HLTV_UC_WAIT_AFTER_LOAD", "0.8")))
_WAIT_AFTER_LOAD_MAX = float(os.getenv("HLTV_UC_WAIT_AFTER_LOAD_MAX", "1.8"))
_DEFAULT_WINDOW_SIZE = (1400, 900)
_FETCH_LOCK = threading.Lock()
_LAST_FETCH_TIME = 0.0


class HLTVBrowserError(RuntimeError):
    """Raised when SeleniumBase UC cannot load or extract an HLTV page."""


def _resolve_profile_dir() -> Path:
    configured = os.getenv("HLTV_PROFILE_DIR", "").strip()
    if configured:
        configured_path = Path(configured).expanduser().resolve()
        legacy_names = {"hltv_profile", "hltv_profile_playwright"}
        if configured_path.name.lower() in legacy_names:
            migrated = configured_path.parent / "hltv_profile_seleniumbase"
            logger.warning(
                "HLTV_PROFILE_DIR points to legacy browser profile '%s'; using SeleniumBase profile '%s'.",
                configured_path,
                migrated,
            )
            return migrated.resolve()
        return configured_path
    return (Path(__file__).resolve().parents[2] / "hltv_profile_seleniumbase").resolve()


def _cleanup_profile_locks(profile_dir: Path) -> None:
    for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
        lock_path = profile_dir / name
        try:
            if lock_path.exists():
                lock_path.unlink()
        except Exception as exc:
            logger.debug("Unable to remove %s: %s", lock_path, exc)


def _import_seleniumbase_driver():
    try:
        from seleniumbase import Driver
    except Exception as exc:
        raise HLTVBrowserError(
            f"SeleniumBase is not available for HLTV scraping. Install requirements first: {exc}"
        ) from exc
    return Driver


def _make_driver():
    Driver = _import_seleniumbase_driver()
    profile_dir = _resolve_profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    _cleanup_profile_locks(profile_dir)

    headless = os.getenv("HLTV_HEADLESS", "0") == "1"
    if headless:
        logger.warning("HLTV_HEADLESS=1 is set, but SeleniumBase UC mode is more detectable in headless mode.")

    kwargs = {
        "uc": True,
        "headless": headless,
        "user_data_dir": str(profile_dir),
        "page_load_strategy": "eager",
        "browser": "chrome",
    }
    try:
        driver = Driver(**kwargs)
    except TypeError:
        kwargs.pop("page_load_strategy", None)
        try:
            driver = Driver(**kwargs)
        except TypeError:
            kwargs.pop("browser", None)
            driver = Driver(**kwargs)
    try:
        driver.set_page_load_timeout(45)
        driver.set_script_timeout(45)
    except Exception:
        pass
    try:
        driver.set_window_size(*_DEFAULT_WINDOW_SIZE)
    except Exception:
        pass
    return driver, profile_dir, headless


def _open_hltv_url(driver, url: str, *, reconnect_time: float | None = None) -> None:
    delay = _DEFAULT_RECONNECT_TIME if reconnect_time is None else reconnect_time
    if hasattr(driver, "uc_open_with_reconnect"):
        driver.uc_open_with_reconnect(url, reconnect_time=delay)
    else:
        driver.get(url)


def _accept_cookies(driver) -> None:
    try:
        driver.uc_click("button:contains('Allow all cookies')", reconnect_time=1)
        return
    except Exception:
        pass
    try:
        from selenium.webdriver.common.by import By

        for button in driver.find_elements(By.TAG_NAME, "button"):
            if "allow all cookies" in (button.text or "").strip().lower():
                button.click()
                return
    except Exception:
        pass


def _wait_for_text(driver, text: str, timeout: float) -> bool:
    if not text:
        return True
    deadline = time.monotonic() + max(0.5, timeout)
    needle = text.lower()
    while time.monotonic() < deadline:
        try:
            if needle in (driver.page_source or "").lower():
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _random_range(min_value: float, max_value: float) -> float:
    low = min(float(min_value), float(max_value))
    high = max(float(min_value), float(max_value))
    return random.uniform(low, high)


def _wait_for_rate_limit() -> None:
    global _LAST_FETCH_TIME
    elapsed = time.monotonic() - _LAST_FETCH_TIME
    delay = _random_range(_FETCH_DELAY_MIN, _FETCH_DELAY_MAX)
    if elapsed < delay:
        sleep_for = delay - elapsed
        logger.info("HLTV SeleniumBase UC throttle sleeping %.2fs before next request", sleep_for)
        time.sleep(sleep_for)


def _mark_fetch_complete() -> None:
    global _LAST_FETCH_TIME
    _LAST_FETCH_TIME = time.monotonic()


def _wait_after_load() -> None:
    time.sleep(_random_range(_WAIT_AFTER_LOAD_MIN, _WAIT_AFTER_LOAD_MAX))


def _is_dead_webdriver_error(exc: Exception) -> bool:
    text = str(exc).lower()
    needles = (
        "connection refused",
        "actively refused",
        "max retries exceeded",
        "newconnectionerror",
        "invalid session id",
        "disconnected",
        "chrome not reachable",
        "target window already closed",
        "/window/handles",
    )
    return any(needle in text for needle in needles)


def _quit_driver(driver: Any) -> None:
    try:
        driver.quit()
    except Exception:
        pass


def fetch_hltv_html(
    url: str,
    *,
    timeout_ms: int = 45000,
    wait_text: str | None = None,
    reconnect_time: float | None = None,
) -> str:
    with _FETCH_LOCK:
        _wait_for_rate_limit()
        last_error: Exception | None = None
        for attempt in range(2):
            driver = None
            profile_dir = None
            headless = False
            try:
                driver, profile_dir, headless = _make_driver()
                _open_hltv_url(driver, url, reconnect_time=reconnect_time)
                _accept_cookies(driver)
                if wait_text:
                    _wait_for_text(driver, wait_text, min(12.0, max(1.0, timeout_ms / 1000.0)))
                _wait_after_load()
                html = driver.page_source or ""
                _mark_fetch_complete()
                logger.info(
                    "HLTV SeleniumBase UC page loaded: title='%s' final_url=%s headless=%s profile_dir=%s",
                    getattr(driver, "title", ""),
                    getattr(driver, "current_url", url),
                    headless,
                    profile_dir,
                )
                return html
            except Exception as exc:
                last_error = exc
                if attempt == 0 and _is_dead_webdriver_error(exc):
                    logger.warning("HLTV UC driver died while fetching %s; retrying with a fresh driver: %s", url, exc)
                    if profile_dir is not None:
                        _cleanup_profile_locks(profile_dir)
                    time.sleep(1.0)
                    continue
                raise HLTVBrowserError(f"Failed to fetch HLTV page with SeleniumBase UC: {exc}") from exc
            finally:
                if driver is not None:
                    _quit_driver(driver)
        raise HLTVBrowserError(f"Failed to fetch HLTV page with SeleniumBase UC: {last_error}")


def fetch_hltv_json(url: str, *, timeout_ms: int = 45000) -> dict[str, Any]:
    with _FETCH_LOCK:
        _wait_for_rate_limit()
        last_error: Exception | None = None
        for attempt in range(2):
            driver = None
            profile_dir = None
            try:
                driver, profile_dir, _ = _make_driver()
                _open_hltv_url(driver, url)
                _wait_after_load()

                body_text = ""
                try:
                    from selenium.webdriver.common.by import By

                    body_text = driver.find_element(By.TAG_NAME, "body").text
                except Exception:
                    body_text = ""

                if body_text.strip():
                    try:
                        data = json.loads(body_text)
                        _mark_fetch_complete()
                        return data
                    except Exception:
                        pass

                script = """
                    const url = arguments[0];
                    const done = arguments[arguments.length - 1];
                    fetch(url, {headers: {accept: "application/json"}})
                        .then((response) => response.text())
                        .then((text) => done({ok: true, text}))
                        .catch((error) => done({ok: false, error: String(error)}));
                """
                result = driver.execute_async_script(script, url)
                if not result or not result.get("ok"):
                    raise HLTVBrowserError((result or {}).get("error") or "browser fetch failed")
                data = json.loads(result.get("text") or "{}")
                _mark_fetch_complete()
                return data
            except HLTVBrowserError as exc:
                last_error = exc
                if attempt == 0 and _is_dead_webdriver_error(exc):
                    logger.warning("HLTV UC driver died while fetching JSON %s; retrying with a fresh driver: %s", url, exc)
                    if profile_dir is not None:
                        _cleanup_profile_locks(profile_dir)
                    time.sleep(1.0)
                    continue
                raise
            except Exception as exc:
                last_error = exc
                if attempt == 0 and _is_dead_webdriver_error(exc):
                    logger.warning("HLTV UC driver died while fetching JSON %s; retrying with a fresh driver: %s", url, exc)
                    if profile_dir is not None:
                        _cleanup_profile_locks(profile_dir)
                    time.sleep(1.0)
                    continue
                raise HLTVBrowserError(f"Failed to fetch HLTV JSON with SeleniumBase UC: {exc}") from exc
            finally:
                if driver is not None:
                    _quit_driver(driver)
        raise HLTVBrowserError(f"Failed to fetch HLTV JSON with SeleniumBase UC: {last_error}")
