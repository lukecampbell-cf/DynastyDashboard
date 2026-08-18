"""
Trade Value Agent
Fetches dynasty player trade values from RosterAudit's "rosteraudit.com API"
listing on Parse Bot (https://parse.bot/marketplace/0df80132-239a-4553-97df-
7b36fed4d070/rosteraudit-com-api), keyed by Sleeper player_id, and saves them
to trade_values.json — a separate file from player_cache.json, refreshed at
most once a week (RA_FRESHNESS below).

RosterAudit's get_dynasty_rankings endpoint returns a single value-ordered
list that blends real players AND future draft picks (e.g. "2027 Early 1st",
"2027 Mid 1st", ...) into one ranking. That gives us a market-calibrated
value chart for free: rather than inventing our own rank->tier bucketing (the
old approach in sleeper_agent.py, kept there as a fallback), a player's trade
value tier is just "the label of the closest draft pick their numeric value
sits above" — e.g. a player valued just under a market 2027 Mid 1st is
labelled "Mid 1st" the same way RosterAudit's own pick would be.

Because Superflex and 1QB startups value QBs (and therefore picks) very
differently, rankings are fetched and tiered separately per format. Which
format applies to a given league is decided in sleeper_agent.py from that
league's roster_positions (a SUPER_FLEX slot, or 2+ QB slots, means "sf").

Only meaningful for dynasty leagues — RosterAudit's values are startup/
dynasty market values built around draft-pick capital, so sleeper_agent.py
only consults this file for leagues where settings.type == 2 (dynasty) and
falls back to the old FantasyPros-rank heuristic for redraft leagues and for
any player RosterAudit hasn't ranked yet (very deep stashes).
"""

import httpx
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Union

from common import is_stale, parsebot_headers, write_json_atomic

log = logging.getLogger(__name__)

# Canonical scraper id for the "rosteraudit.com API" listing on Parse Bot's
# marketplace — stable regardless of which marketplace listing id is browsed.
PARSEBOT_SCRAPER_ID = "34e9689f-ad14-419e-8aa6-e2604592b725"
PARSEBOT_BASE_URL = f"https://api.parse.bot/scraper/{PARSEBOT_SCRAPER_ID}"

TRADE_VALUES_PATH = Path(__file__).resolve().parent / "trade_values.json"
RA_FRESHNESS_SECONDS = 7 * 86400  # pull down once a week

# get_dynasty_rankings caps per_page at 100 regardless of what's requested.
PER_PAGE = 100
LEAGUE_SIZE = 12  # matches the 12-team assumption baked into the old TRADE_VALUE_TIERS bucketing
VALUE_FORMATS = ["sf", "1qb"]

ROUND_LABELS = {1: "1st", 2: "2nd", 3: "3rd", 4: "4th"}
SLOT_LABELS = {"early": "Early", "mid": "Mid", "late": "Late"}


def fetch_dynasty_rankings(format_key: str, league_size: int = LEAGUE_SIZE) -> tuple[dict, list]:
    """
    Page through RosterAudit's get_dynasty_rankings for one value format
    ("sf" or "1qb"), across every position. Returns (players, picks):
      - players: dict keyed by sleeper_id -> {name, position, team, value, rank_overall, tier}
      - picks: list of the raw future-draft-pick entries interleaved in the
        same ranking (identified by type == "pick"), used to build the tier chart.
    """
    players: dict = {}
    picks: list = []
    page = 1
    total = None

    while total is None or (page - 1) * PER_PAGE < total:
        params: dict[str, Union[str, int]] = {
            "sort": "value",
            "format": format_key,
            "position": "all",
            "league_size": league_size,
            "page": page,
            "per_page": PER_PAGE,
        }
        try:
            r = httpx.get(f"{PARSEBOT_BASE_URL}/get_dynasty_rankings", headers=parsebot_headers(), params=params, timeout=20)
            r.raise_for_status()
            payload = r.json()
        except Exception as e:
            log.error(f"RosterAudit get_dynasty_rankings failed (format={format_key}, page={page}): {e}")
            break

        if payload.get("status") != "success":
            log.warning(f"RosterAudit returned non-success status (format={format_key}, page={page}): {payload}")
            break

        data = payload.get("data", {})
        total = data.get("total", 0)

        for entry in data.get("players", []):
            if entry.get("type") == "pick":
                picks.append(entry)
                continue
            sleeper_id = entry.get("sleeper_id")
            if not sleeper_id:
                continue
            players[str(sleeper_id)] = {
                "name": entry.get("name"),
                "position": entry.get("position"),
                "team": entry.get("team"),
                "value": entry.get("value"),
                "rank_overall": entry.get("rank_overall"),
                "tier": entry.get("tier"),
            }

        page += 1

    log.info(f"RosterAudit {format_key}: fetched {len(players)} players, {len(picks)} pick entries across {page - 1} page(s)")
    return players, picks


def build_tier_chart(picks: list) -> list[dict]:
    """
    Turn RosterAudit's interleaved future-pick entries into a value->label
    threshold chart, e.g. [{"label": "Elite", "min_value": 6300}, {"label":
    "Early 1st", "min_value": 5122}, ...], sorted highest value first.

    Only out-year bucketed picks (pick_slot "early"/"mid"/"late" — RosterAudit
    doesn't give an exact slot for picks more than a year out) are used, since
    those coarse Early/Mid/Late-per-round buckets are exactly the trade value
    tiers we want. For each (round, slot) combination the earliest available
    draft year is preferred, since it's the closer/less speculative value.
    An "Elite" tier is added above the best (Early 1st) bucket, anchored to
    the current year's exact 1.01 pick value — the actual top of the market.

    RosterAudit's per-round Early/Mid/Late values aren't perfectly monotonic
    in the deepest rounds (market noise around throwaway late picks — e.g. a
    "Mid 4th" occasionally pricing a hair above "Early 4th"), so buckets are
    walked in round/slot order and any bucket that doesn't strictly decrease
    from the previous one is dropped rather than left to produce an
    out-of-order chart.
    """
    best_bucket: dict[tuple, tuple] = {}  # (round, slot) -> (season, value)
    best_first_overall: Optional[tuple] = None  # (season, value) for the current year's 1.01 pick

    for p in picks:
        slot = p.get("pick_slot")
        season = p.get("pick_season")
        value = p.get("value")
        round_ = p.get("pick_round")
        if value is None or season is None:
            continue

        if slot in SLOT_LABELS and round_ in ROUND_LABELS:
            key = (round_, slot)
            if key not in best_bucket or season < best_bucket[key][0]:
                best_bucket[key] = (season, value)
        elif slot == "01" and round_ == 1:
            if best_first_overall is None or season < best_first_overall[0]:
                best_first_overall = (season, value)

    chart = []
    last_value = None
    for round_ in sorted(ROUND_LABELS):
        for slot in ("early", "mid", "late"):
            bucket = best_bucket.get((round_, slot))
            if bucket is None:
                continue
            _season, value = bucket
            if last_value is not None and value >= last_value:
                continue
            chart.append({"label": f"{SLOT_LABELS[slot]} {ROUND_LABELS[round_]}", "min_value": value})
            last_value = value

    if best_first_overall is not None and chart:
        elite_value = max(best_first_overall[1], chart[0]["min_value"] + 1)
        chart.insert(0, {"label": "Elite", "min_value": elite_value})

    return chart


def trade_value_label(value: Optional[float], tier_chart: list[dict]) -> Optional[str]:
    """Map a player's RosterAudit numeric value to a tier_chart label, e.g. 'Mid 1st'."""
    if value is None or not tier_chart:
        return None
    for tier in tier_chart:
        if value >= tier["min_value"]:
            return tier["label"]
    return "Waiver / Deep Stash"


def load_trade_values() -> Optional[dict]:
    if not TRADE_VALUES_PATH.exists():
        return None
    try:
        with open(TRADE_VALUES_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Failed to read {TRADE_VALUES_PATH.name}, will refetch: {e}")
        return None


def save_trade_values(data: dict) -> None:
    try:
        write_json_atomic(TRADE_VALUES_PATH, data)
        log.info(f"Saved RosterAudit trade values to {TRADE_VALUES_PATH}")
    except Exception as e:
        log.error(f"Failed to write {TRADE_VALUES_PATH.name}: {e}")


def fetch_format(format_key: str) -> dict:
    """Fetch one value format fresh from RosterAudit and build its tier chart, stamped with its own fetched_at."""
    players, picks = fetch_dynasty_rankings(format_key)
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "players": players,
        "tier_chart": build_tier_chart(picks),
    }


def run(force: bool = False) -> dict:
    """
    Main entry point. Returns the RosterAudit trade value data, refreshing
    from the API only if trade_values.json is missing or older than a week
    (or force=True).

    Each format (sf, 1qb) is fetched and evaluated independently: a format
    whose fresh fetch comes back with zero players (API error, non-success
    status, or a genuinely empty response) retains its own previously
    cached data instead of being overwritten with an empty result — the
    other format's success/failure has no bearing on it. This matters
    because both formats used to be merged into one combined result before
    the empty/non-empty check ran, so one format's clean fetch could make
    the whole refresh look "non-empty" and let the *other* format's failed,
    empty fetch silently replace its own valid cached data. A format with no
    cache to fall back to (first-ever run, or never successfully fetched)
    simply stays empty for this run — sleeper_agent.derive_trade_value()
    already falls back to FantasyPros ranks per player when RosterAudit has
    no value for them.

    Returned dict carries `degraded_formats`: the list of format keys (if
    any) that fell back to cached or empty data this run, for callers that
    want to report a degraded-but-successful refresh distinctly from a
    fully-fresh one (see orchestrator.py's pipeline stage reporting).
    """
    cached = load_trade_values()
    cached_formats = (cached or {}).get("formats", {})

    if not force and cached is not None and not is_stale(TRADE_VALUES_PATH, RA_FRESHNESS_SECONDS):
        log.info(f"Using cached RosterAudit trade values from {cached.get('fetched_at')}")
        cached.setdefault("degraded_formats", [])
        return cached

    log.info("Refreshing RosterAudit dynasty rankings (per format)...")
    merged_formats = {}
    degraded_formats = []
    any_fresh = False

    for format_key in VALUE_FORMATS:
        fresh = fetch_format(format_key)
        if fresh["players"]:
            merged_formats[format_key] = fresh
            any_fresh = True
            continue

        cached_entry = cached_formats.get(format_key)
        if cached_entry and cached_entry.get("players"):
            log.warning(f"RosterAudit {format_key} refresh returned no players — keeping stale cached {format_key} data.")
            merged_formats[format_key] = cached_entry
        else:
            log.warning(
                f"RosterAudit {format_key} refresh returned no players and no cache exists — "
                f"trade values will fall back to FantasyPros ranks for {format_key}."
            )
            merged_formats[format_key] = fresh
        degraded_formats.append(format_key)

    result = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": "rosteraudit.com dynasty rankings (via Parse Bot)",
        "formats": merged_formats,
        "degraded_formats": degraded_formats,
    }

    if any_fresh:
        save_trade_values(result)
    else:
        log.warning("RosterAudit refresh failed for every format — not overwriting trade_values.json; will retry next run.")

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [TRADE VALUE] %(message)s")
    result = run(force=True)
    print(json.dumps(result, indent=2))
