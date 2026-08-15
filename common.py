"""
Common
Small helpers shared by two or more agent modules, kept in one place so a
change to a header, a name-normalisation rule, or a freshness check only has
to be made once. Deliberately minimal — this is not a place to accumulate
general-purpose utilities, only demonstrated cross-module duplicates.
"""

import os
import re
import time
from pathlib import Path

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


def normalise_name(name: str) -> str:
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
