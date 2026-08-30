"""Ranking-format, FantasyPros, and trade-value helpers for Sleeper rosters."""

import json
import logging
import os
import re
import time
from typing import Optional

import httpx

from .common import USER_AGENT, normalise_name
from . import trade_value_agent

log = logging.getLogger(__name__)

FANTASYPROS_HEADERS = {"User-Agent": USER_AGENT}
FANTASYPROS_RANKING_URLS = {
    "dynasty": "https://www.fantasypros.com/nfl/rankings/dynasty-overall.php",
    "ppr": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
    "half_ppr": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php",
    "standard": "https://www.fantasypros.com/nfl/rankings/consensus-cheatsheets.php",
}

TRADE_VALUE_TIERS = [
    (4, "Elite / Early 1st"), (8, "Mid 1st"), (12, "Late 1st"),
    (16, "Early 2nd"), (20, "Mid 2nd"), (24, "Late 2nd"),
    (28, "Early 3rd"), (32, "Mid 3rd"), (36, "Late 3rd"),
    (48, "4th Round Value"), (60, "5th Round Value"), (100, "Bench Depth"),
]


def determine_ranking_format(league_settings: dict, scoring_settings: dict) -> str:
    if (league_settings or {}).get("type") == 2:
        return "dynasty"
    receptions = (scoring_settings or {}).get("rec", 0) or 0
    if receptions >= 1:
        return "ppr"
    if receptions >= 0.5:
        return "half_ppr"
    return "standard"


def fetch_fantasypros_rankings(format_key: str) -> dict:
    """Fetch and cache the FantasyPros consensus rankings for one format."""
    url = FANTASYPROS_RANKING_URLS.get(format_key)
    if not url:
        log.warning("Unknown FantasyPros ranking format: %s", format_key)
        return {}
    cache_path = f"/tmp/fantasypros_rankings_{format_key}.json"
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 86400:
        with open(cache_path) as cache_file:
            return json.load(cache_file)
    try:
        response = httpx.get(url, headers=FANTASYPROS_HEADERS, timeout=20, follow_redirects=True)
        response.raise_for_status()
        match = re.search(r"var ecrData\s*=\s*(\{.*?\});", response.text, re.DOTALL)
        if not match:
            log.warning("Could not find ecrData on FantasyPros %s rankings page", format_key)
            return {}
        data = json.loads(match.group(1))
        rankings = {
            normalise_name(player.get("player_name", "")): {
                "fp_player_id": player.get("player_id"), "fp_rank": player.get("rank_ecr"),
                "fp_pos_rank": player.get("pos_rank"), "fp_tier": player.get("tier"),
                "fp_page_url": player.get("player_page_url"),
            }
            for player in data.get("players", []) if normalise_name(player.get("player_name", ""))
        }
        with open(cache_path, "w") as cache_file:
            json.dump(rankings, cache_file)
        return rankings
    except (httpx.HTTPError, json.JSONDecodeError, OSError) as exc:
        log.error("Failed to fetch FantasyPros %s rankings: %s", format_key, exc)
        return {}


def trade_value_tier(rank: Optional[int]) -> Optional[str]:
    if rank is None:
        return None
    for threshold, label in TRADE_VALUE_TIERS:
        if rank <= threshold:
            return label
    return "Waiver / Deep Stash"


def determine_value_format(roster_positions: Optional[list]) -> str:
    positions = roster_positions or []
    return "sf" if "SUPER_FLEX" in positions or positions.count("QB") >= 2 else "1qb"


def derive_trade_value(
    sleeper_player_id: object, ranking_format: str, ra_players: dict,
    ra_tier_chart: list, fp_rank: Optional[int],
) -> Optional[str]:
    if ranking_format == "dynasty":
        entry = ra_players.get(str(sleeper_player_id))
        if entry and entry.get("value") is not None:
            return trade_value_agent.trade_value_label(entry["value"], ra_tier_chart)
    return trade_value_tier(fp_rank)


def extract_draft_year(sleeper_player: dict) -> Optional[int]:
    rookie_year = (sleeper_player.get("metadata") or {}).get("rookie_year")
    if not rookie_year:
        return None
    try:
        return int(rookie_year)
    except (TypeError, ValueError):
        return None


def index_by_fp_id(rankings: dict) -> dict:
    return {
        entry["fp_player_id"]: entry for entry in rankings.values()
        if entry.get("fp_player_id") is not None
    }
