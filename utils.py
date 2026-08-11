"""
utils.py
--------
Shared low-level helpers: logging, on-disk caching, robots.txt checks,
polite rate-limited HTTP fetching, and the resume/status database.

Nothing university-specific lives here.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.robotparser as robotparser
from pathlib import Path
from urllib.parse import urlparse

import requests

import config

# --------------------------------------------------------------------------
# LOGGING
# --------------------------------------------------------------------------
def get_logger(name: str = "scraper") -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(message)s")

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    return logger


log = get_logger()


# --------------------------------------------------------------------------
# RATE LIMITING (per-host)
# --------------------------------------------------------------------------
_last_request_time: dict[str, float] = {}


def _throttle(host: str) -> None:
    now = time.time()
    last = _last_request_time.get(host, 0.0)
    wait = config.REQUEST_DELAY_SECONDS - (now - last)
    if wait > 0:
        time.sleep(wait)
    _last_request_time[host] = time.time()


# --------------------------------------------------------------------------
# ROBOTS.TXT
# --------------------------------------------------------------------------
_robots_cache: dict[str, robotparser.RobotFileParser] = {}


def is_allowed_by_robots(url: str) -> bool:
    """Return True if crawling `url` is allowed by the site's robots.txt.
    Fails OPEN (returns True) only if robots.txt cannot be fetched at all,
    since that is the common default for permissive sites; but any explicit
    Disallow rule is always respected.
    """
    if not config.RESPECT_ROBOTS_TXT:
        return True

    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    if base not in _robots_cache:
        rp = robotparser.RobotFileParser()
        rp.set_url(f"{base}/robots.txt")
        try:
            rp.read()
        except Exception:
            # Could not fetch robots.txt - treat as permissive but log it.
            log.info(f"[WARN] Could not read robots.txt for {base}, defaulting to allow")
        _robots_cache[base] = rp

    rp = _robots_cache[base]
    try:
        return rp.can_fetch(config.USER_AGENT, url)
    except Exception:
        return True


# --------------------------------------------------------------------------
# ON-DISK CACHE (avoid re-downloading the same page/PDF)
# --------------------------------------------------------------------------
def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
    return config.CACHE_DIR / f"{digest}.cache"


def cache_get(url: str) -> bytes | None:
    path = _cache_path(url)
    if path.exists():
        return path.read_bytes()
    return None


def cache_set(url: str, content: bytes) -> None:
    path = _cache_path(url)
    try:
        path.write_bytes(content)
    except Exception as e:
        log.info(f"[WARN] Failed to cache {url}: {e}")


# --------------------------------------------------------------------------
# HTTP FETCH (requests-based, cached, robots-respecting, rate-limited)
# --------------------------------------------------------------------------
def fetch(url: str, allow_binary: bool = False) -> tuple[bytes | None, str | None]:
    """Fetch a URL politely. Returns (content_bytes, error_message).
    Uses on-disk cache first. Respects robots.txt. Does not raise.
    """
    cached = cache_get(url)
    if cached is not None:
        return cached, None

    if not is_allowed_by_robots(url):
        return None, "blocked_by_robots_txt"

    host = urlparse(url).netloc
    _throttle(host)

    headers = {"User-Agent": config.USER_AGENT}
    try:
        resp = requests.get(
            url, headers=headers, timeout=config.REQUEST_TIMEOUT, allow_redirects=True
        )
    except requests.exceptions.SSLError:
        return None, "ssl_error"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.RequestException as e:
        return None, f"request_error:{e}"

    if resp.status_code == 403:
        return None, "http_403"
    if resp.status_code == 404:
        return None, "http_404"
    if resp.status_code == 429:
        return None, "http_429"
    if resp.status_code >= 400:
        return None, f"http_{resp.status_code}"

    content_type = resp.headers.get("Content-Type", "")
    if not allow_binary and "text" not in content_type and "html" not in content_type:
        # e.g. a PDF fetched without allow_binary=True
        pass  # still cache/return - caller decides how to interpret

    content = resp.content
    cache_set(url, content)
    return content, None


# --------------------------------------------------------------------------
# RESUME / STATUS DATABASE
# A simple local JSON file tracking which (university, course, year) rows
# have already been fully processed, so re-running the script on a partial
# batch of 65+ universities does not redo completed work.
# --------------------------------------------------------------------------
def _row_key(university: str, course: str, year) -> str:
    return f"{university.strip().lower()}::{course.strip().lower()}::{year}"


def load_status_db() -> dict:
    if config.STATUS_DB.exists():
        try:
            return json.loads(config.STATUS_DB.read_text(encoding="utf-8"))
        except Exception:
            log.info("[WARN] status.json unreadable, starting fresh")
            return {}
    return {}


def save_status_db(db: dict) -> None:
    config.STATUS_DB.write_text(json.dumps(db, indent=2), encoding="utf-8")


def is_row_done(db: dict, university: str, course: str, year) -> bool:
    return db.get(_row_key(university, course, year), {}).get("done", False)


def mark_row_done(db: dict, university: str, course: str, year) -> None:
    db[_row_key(university, course, year)] = {"done": True, "ts": time.time()}
    save_status_db(db)
