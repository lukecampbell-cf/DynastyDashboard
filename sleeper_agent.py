"""
Sleeper Agent
Fetches all NFL leagues and rosters for a given username via the Sleeper public API.
Season is resolved dynamically — checks the Sleeper NFL state endpoint first,
then falls back to current year / prior year league lookup.
No authentication required for read operations.

Each roster player is also enriched with a FantasyPros consensus ranking
(dynasty, PPR, half-PPR, or standard — chosen per league) and a trade value
tier (e.g. "Mid 1st"). For dynasty leagues that tier comes from RosterAudit's
market values (see trade_value_agent.py, refreshed weekly); everything else
falls back to a bucketing of the FantasyPros rank — see derive_trade_value().

This is stage 1 of player retrieval: Sleeper bio/id details, cached for 14
days in player_cache.json. Stage 2 (Spotrac contract details, cached for 28
days) lives in contract_agent.py and runs separately against the same cache.
"""

import httpx
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from common import USER_AGENT, is_timestamp_stale, normalise_name, write_json_atomic
from schemas import LeagueRecord, PlayerCacheEntry, ResolvedPlayer, SleeperOutput
import player_directory_agent
import trade_value_agent

log = logging.getLogger(__name__)

SLEEPER_BASE = "https://api.sleeper.app/v1"

FANTASYPROS_HEADERS = {"User-Agent": USER_AGENT}

# FantasyPros doesn't split dynasty rankings by scoring format — dynasty value
# is dominated by long-term outlook, so a single blended dynasty page covers
# all dynasty leagues regardless of PPR/half-PPR/standard scoring.
FANTASYPROS_RANKING_URLS = {
    "dynasty": "https://www.fantasypros.com/nfl/rankings/dynasty-overall.php",
    "ppr": "https://www.fantasypros.com/nfl/rankings/ppr-cheatsheets.php",
    "half_ppr": "https://www.fantasypros.com/nfl/rankings/half-point-ppr-cheatsheets.php",
    "standard": "https://www.fantasypros.com/nfl/rankings/consensus-cheatsheets.php",
}

# Per-player bio/id cache: Sleeper<->FantasyPros id crosswalk plus bio
# details (name, position, team, age, years_exp, college, draft_year).
# Refreshed only when an entry is missing or its details_updated_at is
# older than DETAILS_FRESHNESS — everything else (injury status, fp_rank,
# roster flags) is re-read fresh from the API every run regardless. The
# same file also holds a "contract" sub-object per player, populated and
# refreshed independently by contract_agent.py on its own 28-day cadence.
PLAYER_CACHE_PATH = Path(__file__).resolve().parent / "player_cache.json"

# Legacy filename from before player details were cached — if present and
# player_cache.json doesn't exist yet, its id crosswalk is migrated in
# rather than thrown away (bio fields simply refresh on first run).
LEGACY_PLAYER_ID_MAP_PATH = Path(__file__).resolve().parent / "player_id_map.json"

DETAILS_FRESHNESS = timedelta(days=14)


def get_user(username: str) -> Optional[dict]:
    """Resolve username to Sleeper user object."""
    url = f"{SLEEPER_BASE}/user/{username}"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        user = r.json()
        log.info(f"Resolved user: {user.get('display_name')} (ID: {user.get('user_id')})")
        return user
    except Exception as e:
        log.error(f"Failed to fetch user {username}: {e}")
        return None


def get_leagues(user_id: str, season: str) -> list[dict]:
    """Fetch all NFL leagues for a user in a given season."""
    url = f"{SLEEPER_BASE}/user/{user_id}/leagues/nfl/{season}"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        leagues = r.json() or []
        log.info(f"Found {len(leagues)} league(s) for season {season}")
        return leagues
    except Exception as e:
        log.error(f"Failed to fetch leagues: {e}")
        return []


def resolve_season(user_id: str) -> str:
    """
    Dynamically resolve the active NFL season.

    Strategy:
    1. Hit the Sleeper /state/nfl endpoint — canonical source, returns
       the current season and week regardless of time of year.
    2. If that fails, attempt league lookup for current year then prior year.

    Returns the season year as a string e.g. "2026".
    """
    # Primary: Sleeper's own NFL state endpoint
    try:
        r = httpx.get(f"{SLEEPER_BASE}/state/nfl", timeout=10)
        r.raise_for_status()
        state = r.json()
        # Sleeper returns "season" for the current active season year
        season = state.get("season") or state.get("league_season")
        if season:
            log.info(
                f"NFL state resolved: season={season}, week={state.get('week')}, "
                f"season_type={state.get('season_type', 'unknown')}"
            )
            return str(season)
    except Exception as e:
        log.warning(f"NFL state endpoint failed: {e} — falling back to year-based resolution")

    # Fallback: try current year, then prior year
    current_year = datetime.now().year
    for year in [current_year, current_year - 1]:
        season_str = str(year)
        leagues = get_leagues(user_id, season_str)
        if leagues:
            log.info(f"Season resolved via league check: {season_str}")
            return season_str
        log.info(f"No leagues found for {season_str}")

    # Last resort
    fallback = str(current_year - 1)
    log.warning(f"Could not resolve active season — defaulting to {fallback}")
    return fallback


def get_rosters(league_id: str) -> list[dict]:
    """Fetch all rosters in a league."""
    url = f"{SLEEPER_BASE}/league/{league_id}/rosters"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.error(f"Failed to fetch rosters for league {league_id}: {e}")
        return []


def get_users_in_league(league_id: str) -> dict:
    """Fetch all users in a league, keyed by user_id."""
    url = f"{SLEEPER_BASE}/league/{league_id}/users"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        users = r.json() or []
        return {u["user_id"]: u for u in users}
    except Exception as e:
        log.error(f"Failed to fetch league users for {league_id}: {e}")
        return {}


def get_nfl_players() -> dict:
    """
    Fetch the full Sleeper NFL player database.
    Large payload (~5MB) — cached locally for 24 hours.
    Returns dict keyed by player_id.
    """
    import json
    import os
    import time

    cache_path = "/tmp/sleeper_players_cache.json"

    if os.path.exists(cache_path):
        age = os.path.getmtime(cache_path)
        if time.time() - age < 86400:
            log.info("Using cached player database")
            with open(cache_path) as f:
                return json.load(f)

    log.info("Fetching full NFL player database from Sleeper...")
    url = f"{SLEEPER_BASE}/players/nfl"
    try:
        r = httpx.get(url, timeout=30)
        r.raise_for_status()
        players = r.json() or {}
        with open(cache_path, "w") as f:
            json.dump(players, f)
        log.info(f"Cached {len(players)} players")
        return players
    except Exception as e:
        log.error(f"Failed to fetch player database: {e}")
        return {}


def determine_ranking_format(league_settings: dict, scoring_settings: dict) -> str:
    """
    Decide which FantasyPros ranking set applies to a league.
    Sleeper: settings.type == 2 means dynasty; scoring_settings.rec is the
    receptions-per-catch value (1 = PPR, 0.5 = half-PPR, 0/absent = standard).
    """
    if (league_settings or {}).get("type") == 2:
        return "dynasty"

    rec = (scoring_settings or {}).get("rec", 0) or 0
    if rec >= 1:
        return "ppr"
    if rec >= 0.5:
        return "half_ppr"
    return "standard"


def fetch_fantasypros_rankings(format_key: str) -> dict:
    """
    Fetch FantasyPros consensus rankings for the given format
    ("dynasty", "ppr", "half_ppr", or "standard").
    Returns a dict keyed by normalised player name, cached locally for 24 hours.
    """
    url = FANTASYPROS_RANKING_URLS.get(format_key)
    if not url:
        log.warning(f"Unknown FantasyPros ranking format: {format_key}")
        return {}

    cache_path = f"/tmp/fantasypros_rankings_{format_key}.json"
    if os.path.exists(cache_path) and time.time() - os.path.getmtime(cache_path) < 86400:
        log.info(f"Using cached FantasyPros {format_key} rankings")
        with open(cache_path) as f:
            return json.load(f)

    log.info(f"Fetching FantasyPros {format_key} rankings: {url}")
    try:
        r = httpx.get(url, headers=FANTASYPROS_HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        match = re.search(r"var ecrData\s*=\s*(\{.*?\});", r.text, re.DOTALL)
        if not match:
            log.warning(f"Could not find ecrData on FantasyPros {format_key} rankings page")
            return {}

        data = json.loads(match.group(1))
        rankings = {}
        for p in data.get("players", []):
            key = normalise_name(p.get("player_name", ""))
            if not key:
                continue
            rankings[key] = {
                "fp_player_id": p.get("player_id"),
                "fp_rank": p.get("rank_ecr"),
                "fp_pos_rank": p.get("pos_rank"),
                "fp_tier": p.get("tier"),
                "fp_page_url": p.get("player_page_url"),
            }

        with open(cache_path, "w") as f:
            json.dump(rankings, f)
        log.info(f"Cached {len(rankings)} FantasyPros {format_key} rankings")
        return rankings
    except Exception as e:
        log.error(f"Failed to fetch FantasyPros {format_key} rankings: {e}")
        return {}


# Fallback-only trade value tiers, derived from FantasyPros consensus rank.
# Dynasty leagues now get a market-calibrated trade value from RosterAudit
# instead (see trade_value_agent.py + derive_trade_value() below); this
# FantasyPros-rank bucketing only kicks in for redraft leagues (RosterAudit's
# values are dynasty/startup-only) or for a player RosterAudit hasn't ranked
# yet. FantasyPros does not publish an official trade value chart, so this
# buckets rank into 4-player slices (early/mid/late thirds of a round, sized
# for a 12-team startup draft) — a common industry convention, not a
# scraped/authoritative value.
TRADE_VALUE_TIERS = [
    (4, "Elite / Early 1st"),
    (8, "Mid 1st"),
    (12, "Late 1st"),
    (16, "Early 2nd"),
    (20, "Mid 2nd"),
    (24, "Late 2nd"),
    (28, "Early 3rd"),
    (32, "Mid 3rd"),
    (36, "Late 3rd"),
    (48, "4th Round Value"),
    (60, "5th Round Value"),
    (100, "Bench Depth"),
]


def trade_value_tier(rank: Optional[int]) -> Optional[str]:
    """Map a FantasyPros consensus rank to a human-readable trade value tier."""
    if rank is None:
        return None
    for threshold, label in TRADE_VALUE_TIERS:
        if rank <= threshold:
            return label
    return "Waiver / Deep Stash"


def determine_value_format(roster_positions: Optional[list]) -> str:
    """
    Decide whether a league's RosterAudit trade values should be Superflex
    or 1QB — a SUPER_FLEX slot, or a second dedicated QB slot, means teams
    can start (and therefore pay up for) more than one quarterback.
    """
    positions = roster_positions or []
    if "SUPER_FLEX" in positions:
        return "sf"
    if positions.count("QB") >= 2:
        return "sf"
    return "1qb"


def derive_trade_value(
    sleeper_player_id,
    ranking_format: str,
    ra_players: dict,
    ra_tier_chart: list,
    fp_rank: Optional[int],
) -> Optional[str]:
    """
    Dynasty leagues: RosterAudit market value, bucketed into the same
    "Early 1st / Mid 2nd / ..." tiers RosterAudit itself uses for future
    draft picks (see trade_value_agent.py). Falls back to the FantasyPros-
    rank heuristic when the player isn't in RosterAudit's ranked set yet
    (very deep stashes) or the league isn't dynasty — RosterAudit values are
    startup/dynasty market values built around draft-pick capital, which
    doesn't map onto redraft leagues.
    """
    if ranking_format == "dynasty":
        ra_entry = ra_players.get(str(sleeper_player_id))
        if ra_entry and ra_entry.get("value") is not None:
            return trade_value_agent.trade_value_label(ra_entry["value"], ra_tier_chart)
    return trade_value_tier(fp_rank)


DESIGNATION_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def assign_roster_designations(players: list[ResolvedPlayer]) -> None:
    """
    Assign a roster-relative depth designation (WR1, WR2, RB1, ...) to each
    player, mutating the list in place. Ranked within this roster only (not
    league-wide) by FantasyPros consensus rank — lower rank = higher on the
    depth chart. Players with no fp_rank (unranked/deep stash) sort last and
    still get a designation, just at the bottom of their position group.
    Only applied to standard fantasy positions; anything else is left unset.
    """
    by_position: dict[str, list[ResolvedPlayer]] = {}
    for p in players:
        pos = p.get("position")
        if pos in DESIGNATION_POSITIONS:
            by_position.setdefault(pos, []).append(p)

    for pos, group in by_position.items():
        group.sort(key=lambda p: (p.get("fp_rank") is None, p.get("fp_rank") or 0))
        for i, p in enumerate(group, start=1):
            p["roster_designation"] = f"{pos}{i}"


def load_player_cache() -> dict[str, PlayerCacheEntry]:
    """Load the persisted per-player cache, keyed by Sleeper player_id."""
    if PLAYER_CACHE_PATH.exists():
        try:
            with open(PLAYER_CACHE_PATH) as f:
                return json.load(f)
        except Exception as e:
            log.warning(f"Failed to read player cache, starting fresh: {e}")
            return {}

    if LEGACY_PLAYER_ID_MAP_PATH.exists():
        log.info(f"No player_cache.json yet — migrating id crosswalk from {LEGACY_PLAYER_ID_MAP_PATH.name}")
        try:
            with open(LEGACY_PLAYER_ID_MAP_PATH) as f:
                legacy = json.load(f)
            # No details_updated_at on legacy entries, so every entry is
            # treated as stale and refreshes itself on first use.
            return legacy
        except Exception as e:
            log.warning(f"Failed to migrate legacy player id map: {e}")

    return {}


def save_player_cache(player_cache: dict[str, PlayerCacheEntry]) -> None:
    """Persist the per-player cache."""
    try:
        write_json_atomic(PLAYER_CACHE_PATH, player_cache, sort_keys=True)
    except Exception as e:
        log.error(f"Failed to write player cache: {e}")


# Thin re-export: contract_agent.py imports this as `from sleeper_agent
# import is_stale` (it needs the same per-timestamp freshness check sleeper_
# agent itself uses for player_cache.json's details_updated_at, just against
# a different field/threshold), and the logic itself lives in common.py
# alongside is_stale()'s mtime-based sibling, so it's not reimplemented here.
is_stale = is_timestamp_stale


def extract_draft_year(sleeper_player: dict) -> Optional[int]:
    """
    Best-effort accurate NFL draft year for a player, read from Sleeper's own
    metadata.rookie_year field (the season they entered the league).

    Deliberately not inferred from years_exp: years_exp counts completed
    seasons and is relative to whatever moment the player DB snapshot was
    taken, so "years_exp == 0" means something different in Week 1 of a
    rookie's debut season than it does in the following offseason. rookie_year
    is a fixed fact and gives an accurate "Year Drafted 2025" vs "2026" read
    regardless of when in the season this runs.
    """
    rookie_year = (sleeper_player.get("metadata") or {}).get("rookie_year")
    if not rookie_year:
        return None
    try:
        return int(rookie_year)
    except (TypeError, ValueError):
        return None


def index_by_fp_id(rankings: dict) -> dict:
    """Re-key a name-keyed FantasyPros rankings dict by fp_player_id for O(1) id lookups."""
    return {
        entry["fp_player_id"]: entry
        for entry in rankings.values()
        if entry.get("fp_player_id") is not None
    }


def get_matchups(league_id: str, week: int) -> list[dict]:
    """Fetch matchups for a given week."""
    url = f"{SLEEPER_BASE}/league/{league_id}/matchups/{week}"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        return r.json() or []
    except Exception as e:
        log.error(f"Failed to fetch matchups for league {league_id} week {week}: {e}")
        return []


def get_league_info(league_id: str) -> Optional[dict]:
    """Fetch league metadata."""
    url = f"{SLEEPER_BASE}/league/{league_id}"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error(f"Failed to fetch league info for {league_id}: {e}")
        return None


def run() -> SleeperOutput:
    """
    Main entry point for the Sleeper agent.
    Season is resolved dynamically — no hardcoding required.
    Returns a structured summary of all leagues, the user's rosters, and player details.
    """
    username = os.environ.get("SLEEPER_USERNAME", "")
    result: SleeperOutput = {
        "username": username,
        "season": None,
        "user": None,
        "leagues": [],
        "trade_values_degraded": [],
    }

    # Step 1: Resolve user
    user = get_user(username)
    if not user:
        log.error("Cannot proceed without valid user.")
        return result

    result["user"] = user
    user_id = user["user_id"]

    # Step 2: Dynamically resolve the active season
    season = resolve_season(user_id)
    result["season"] = season
    log.info(f"Active season resolved: {season}")

    # Step 3: Fetch player database
    all_players = get_nfl_players()

    # Step 4: Fetch all leagues for the resolved season
    leagues = get_leagues(user_id, season)

    # Rankings are fetched once per format and reused across leagues that share one
    fp_rankings_by_format: dict[str, dict] = {}
    fp_rankings_by_id_by_format: dict[str, dict] = {}

    # RosterAudit dynasty trade values — fetched at most once a week (see
    # trade_value_agent.py), reused across every dynasty league regardless
    # of scoring format (only sf/1qb split matters, decided per league below).
    rosteraudit_data = trade_value_agent.run()
    result["trade_values_degraded"] = rosteraudit_data.get("degraded_formats", [])

    # Full player directory (every fantasy-relevant NFL player + trade value,
    # not just your rostered ones) for trade_calculator.php — refreshed at
    # most weekly, see player_directory_agent.py. Non-fatal: a failure here
    # shouldn't take down the whole Sleeper pipeline, since it's a side file
    # for a standalone tool, not something reasoning_agent.py depends on.
    try:
        player_directory_agent.run(all_players, rosteraudit_data)
    except Exception as e:
        log.warning(f"Player directory build failed (non-fatal): {e}")

    # Step 3a: load the persisted per-player cache (bio/id details + any
    # previously-resolved contract data from contract_agent.py)
    player_cache = load_player_cache()
    player_cache_dirty = False
    details_refreshed = 0

    for league in leagues:
        league_id = league["league_id"]
        league_name = league.get("name", "Unnamed League")
        log.info(f"Processing league: {league_name} ({league_id})")

        rosters = get_rosters(league_id)
        league_info = get_league_info(league_id)

        # Find the user's roster
        my_roster = None
        for roster in rosters:
            if roster.get("owner_id") == user_id:
                my_roster = roster
                break

        if not my_roster:
            log.warning(f"Could not find roster for user in league {league_name}")
            continue

        # Resolve player IDs to full player data. Sleeper's "players" list is
        # the full roster — reserve (IR) and taxi squad players are already
        # included in it, not a separate addition — "starters"/"reserve"/
        # "taxi" are just status subsets of "players" used below to flag
        # is_starter/is_ir/is_taxi. Adding reserve/taxi on top of "players"
        # here previously double-counted them (see the duplicate-player-IDs
        # fix); if a future roster ever showed a reserve/taxi player missing
        # from "players", that'd be a genuine Sleeper API inconsistency, not
        # something to work around by re-adding them here.
        player_ids = my_roster.get("players") or []
        starters = my_roster.get("starters") or []
        taxi = my_roster.get("taxi") or []

        league_settings = league_info.get("settings", {}) if league_info else {}
        scoring_settings = league_info.get("scoring_settings", {}) if league_info else {}

        # Step 4a: FantasyPros rank + approximate trade value, per league's scoring format
        ranking_format = determine_ranking_format(league_settings, scoring_settings)
        if ranking_format not in fp_rankings_by_format:
            fp_rankings_by_format[ranking_format] = fetch_fantasypros_rankings(ranking_format)
            fp_rankings_by_id_by_format[ranking_format] = index_by_fp_id(fp_rankings_by_format[ranking_format])
        fp_rankings = fp_rankings_by_format[ranking_format]
        fp_rankings_by_id = fp_rankings_by_id_by_format[ranking_format]

        value_format = determine_value_format((league_info or {}).get("roster_positions"))
        ra_format_data = rosteraudit_data.get("formats", {}).get(value_format, {})
        ra_players = ra_format_data.get("players", {})
        ra_tier_chart = ra_format_data.get("tier_chart", [])

        resolved_players: list[ResolvedPlayer] = []
        for pid in player_ids:
            p = all_players.get(pid, {})
            if not p:
                continue

            full_name = f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
            cache_entry = player_cache.get(str(pid))
            stale = cache_entry is None or is_stale(cache_entry.get("details_updated_at"), DETAILS_FRESHNESS)

            if stale:
                # Id-first lookup via the cached crosswalk; only fall back to
                # name matching against fresh FantasyPros data on a cache miss.
                cached_fp_id = (cache_entry or {}).get("fp_player_id")
                fp = fp_rankings_by_id.get(cached_fp_id) if cached_fp_id is not None else None
                if fp is None:
                    fp = fp_rankings.get(normalise_name(full_name), {})

                cache_entry = {
                    "sleeper_player_id": str(pid),
                    "full_name": full_name,
                    "position": p.get("position", "UNK"),
                    "team": p.get("team", "FA"),
                    "age": p.get("age"),
                    "years_exp": p.get("years_exp"),
                    "college": p.get("college"),
                    "draft_year": extract_draft_year(p),
                    "fp_player_id": fp.get("fp_player_id", cached_fp_id),
                    "fp_page_url": fp.get("fp_page_url"),
                    "details_updated_at": datetime.now(timezone.utc).isoformat(),
                }
                # Preserve any contract data contract_agent.py already resolved
                old_contract = (player_cache.get(str(pid)) or {}).get("contract")
                if old_contract:
                    cache_entry["contract"] = old_contract

                player_cache[str(pid)] = cache_entry
                player_cache_dirty = True
                details_refreshed += 1

            # stale = cache_entry is None or is_stale(...) above, so if we
            # get here without the "if stale:" branch running, stale was
            # False, which means cache_entry was already non-None.
            assert cache_entry is not None

            # fp_rank/tier move daily even when bio details are still fresh,
            # so always re-read those live off today's rankings fetch.
            fp_id = cache_entry.get("fp_player_id")
            fp_live = fp_rankings_by_id.get(fp_id, {}) if fp_id is not None else {}

            resolved_players.append({
                "player_id": pid,
                "full_name": cache_entry["full_name"],
                "position": cache_entry.get("position", p.get("position", "UNK")),
                "team": cache_entry.get("team", p.get("team", "FA")),
                "age": cache_entry.get("age"),
                "years_exp": cache_entry.get("years_exp"),
                "college": cache_entry.get("college"),
                "draft_year": cache_entry.get("draft_year"),
                "injury_status": p.get("injury_status"),
                "injury_body_part": p.get("injury_body_part"),
                "status": p.get("status", "Active"),
                "is_starter": pid in starters,
                "is_taxi": pid in taxi,
                "is_ir": pid in (my_roster.get("reserve") or []),
                "fp_player_id": fp_id,
                "fp_rank": fp_live.get("fp_rank"),
                "fp_pos_rank": fp_live.get("fp_pos_rank"),
                "fp_tier": fp_live.get("fp_tier"),
                "fp_scoring_format": ranking_format,
                "trade_value": derive_trade_value(pid, ranking_format, ra_players, ra_tier_chart, fp_live.get("fp_rank")),
                "contract": cache_entry.get("contract"),
            })

        assign_roster_designations(resolved_players)

        league_data: LeagueRecord = {
            "league_id": league_id,
            "league_name": league_name,
            "season": season,
            "roster_id": my_roster.get("roster_id"),
            "record": my_roster.get("settings", {}),
            "players": resolved_players,
            "total_players": len(resolved_players),
            "settings": league_settings,
            "scoring_settings": scoring_settings,
            "ranking_format": ranking_format,
        }

        result["leagues"].append(league_data)
        log.info(f"  → {len(resolved_players)} players on roster in {league_name}")

    if player_cache_dirty:
        save_player_cache(player_cache)
        log.info(
            f"Player cache updated: {details_refreshed} entr{'y' if details_refreshed == 1 else 'ies'} "
            f"refreshed (missing or >{DETAILS_FRESHNESS.days}d old), {len(player_cache)} total cached at {PLAYER_CACHE_PATH}"
        )

    log.info(f"Sleeper agent complete. {len(result['leagues'])} league(s) processed.")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [SLEEPER] %(message)s")
    data = run()
    print(json.dumps(data, indent=2))
