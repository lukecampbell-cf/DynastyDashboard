"""
Common
Small helpers shared by two or more agent modules, kept in one place so a
change to a header, a name-normalisation rule, or a freshness check only has
to be made once. Deliberately minimal — this is not a place to accumulate
general-purpose utilities, only demonstrated cross-module duplicates.
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

# Shared browser identity for direct HTML scraping (sleeper_agent's
# FantasyPros rankings pages, news_agent's Rotowire/NFL.com/CBS Sports
# scrapes) — sites that block the default httpx/requests user agent outright.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


def parsebot_headers() -> dict:
    """
    Auth header for any Parse Bot scraper call (contract_agent.py,
    trade_value_agent.py, news_agent.py). A function, not a module-level
    constant — PARSE_BOT_API is read fresh on every call, so a module
    imported before .env is loaded (e.g. running one of these files
    directly rather than through orchestrator.py, which loads .env before
    importing any agent) still picks up the real key once it's set, instead
    of permanently caching an empty string from import time.
    """
    return {"X-API-Key": os.environ.get("PARSE_BOT_API", "")}


def normalise_name(name: Optional[str]) -> str:
    """Normalise a player name for matching across Sleeper, FantasyPros, Spotrac, and news sources."""
    if not name:
        return ""
    name = name.strip().lower()
    name = re.sub(r"\b(jr|sr|ii|iii|iv)\b\.?", "", name)
    name = re.sub(r"[^a-z\s]", "", name)
    return " ".join(name.split())


def is_stale(path: Path, max_age_seconds: int) -> bool:
    """True if `path` doesn't exist, or was last modified more than max_age_seconds ago."""
    if not path.exists():
        return True
    return time.time() - path.stat().st_mtime >= max_age_seconds


def is_timestamp_stale(timestamp: Optional[str], max_age: timedelta) -> bool:
    """
    True if an ISO timestamp string (as opposed to a file's mtime — see
    is_stale() above) is missing, unparseable, or older than max_age.
    Shared by every per-entry (not whole-file) freshness check: sleeper_agent's
    per-player bio cache, contract_agent's per-player contract cache, and
    reasoning_agent's own cache-entry timestamps use the same mechanics, just
    against different fields and thresholds.
    """
    if not timestamp:
        return True
    try:
        ts = datetime.fromisoformat(timestamp)
    except ValueError:
        return True
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts > max_age


def _write_atomic(path: Path, write_body) -> None:
    """
    Shared machinery behind write_json_atomic()/write_text_atomic(): write
    to a temporary sibling file, flush + fsync it, then os.replace() it onto
    the target. os.replace() is atomic on both POSIX and Windows, so a
    reader (or a web server serving index.html) never observes a partially
    written file — it sees either the old complete file or the new complete
    one, never a truncated one from a process killed mid-write. The temp
    file is cleaned up on any failure so a crashed write doesn't leave
    stray `.tmp` files behind.

    `write_body(f)` does the actual content write onto the open temp file
    handle `f` — callers supply this instead of raw text/bytes so json.dump
    can stream directly into the file rather than building the full string
    in memory first.

    tempfile.mkstemp() creates its temp file mode 0600 (owner-only) as a
    security default, and os.replace() carries that mode straight through
    to the final path — a plain open(path, "w") would instead land on the
    umask-derived default (typically 644). Every file this writes is meant
    to be read by a different process than the one writing it (nginx/PHP
    serving index.html or reading the JSON caches, often as a different
    user than the cron job that ran the pipeline), so the explicit chmod
    below restores that expectation rather than silently locking readers
    out the moment atomic writes were introduced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            write_body(f)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp_name, 0o644)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def write_json_atomic(path: Path, data, *, indent: Optional[int] = 2, sort_keys: bool = False) -> None:
    """
    Write `data` as JSON to `path` atomically (see _write_atomic). Used for
    every persistent JSON cache in the pipeline — a process interruption
    after truncation but before completion would otherwise corrupt known-
    good cached state instead of just losing this run's update.
    """
    _write_atomic(path, lambda f: json.dump(data, f, indent=indent, sort_keys=sort_keys, ensure_ascii=False))


def write_text_atomic(path: Path, text: str) -> None:
    """Write `text` to `path` atomically (see _write_atomic). Used for the
    published dashboard HTML, so a web server never serves a half-written page."""
    _write_atomic(path, lambda f: f.write(text))
