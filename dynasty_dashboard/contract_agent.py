"""
Contract Agent
Stage 2 of player retrieval: looks up each roster player's real-world NFL
contract on Spotrac and writes it into the same per-player cache that
sleeper_agent.py populates (player_cache.json), under a "contract" key.

Runs independently of the Sleeper stage and on a longer cadence — contract
terms change far less often than rosters or rankings, so each player's
contract is only re-looked-up when missing or older than CONTRACT_FRESHNESS.
This keeps Parse Bot traffic to a handful of requests per run instead of one
per roster player every time the pipeline executes.

Spotrac has no public API of its own; this calls the "spotrac.com API" on
Parse Bot (https://parse.bot/marketplace/7994a459-31cd-4d12-a28b-5c053246f105/spotrac-com-api),
a hosted wrapper that does the HTML scraping for us and hands back structured
JSON. Auth is a Parse Bot API key (PARSE_BOT_API in .env).
"""

import concurrent.futures
import httpx
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from .common import parsebot_headers
from .schemas import ContractInfo, ContractLookupPlayer
from .sleeper_agent import (
    PLAYER_CACHE_PATH,
    is_stale,
    load_player_cache,
    normalise_name,
    save_player_cache,
)

log = logging.getLogger(__name__)

# Canonical scraper id for the "spotrac.com API" listing on Parse Bot's
# marketplace — stable regardless of which marketplace listing id is browsed.
PARSEBOT_SCRAPER_ID = "1063a484-f52f-4db5-a50f-b26705848e2f"
PARSEBOT_BASE_URL = f"https://api.parse.bot/scraper/{PARSEBOT_SCRAPER_ID}"

CONTRACT_FRESHNESS = timedelta(days=28)

# Concurrent Spotrac lookups in flight at once — this only runs for stale/
# missing cache entries, which after the first backfill is a small fraction
# of the roster, but a cold cache (first-ever run) hits Parse Bot for
# hundreds of players sequentially otherwise, at 0.6s/player. 4 workers
# (each still individually paced, see lookup_one() in run()) is a
# conservative multiplier chosen without a documented Parse Bot rate limit
# to check against — raise it if Parse Bot's actual limit comfortably allows
# more, lower it if runs start seeing rate-limit errors.
CONTRACT_LOOKUP_WORKERS = 4

# Contract data only exists for individual players — team defenses have no
# personal contract to look up.
SKIP_POSITIONS = {"DEF"}

# Spotrac search results label positions with the full word; map back to the
# same abbreviations Sleeper uses so a candidate can be confirmed by position,
# not name alone (this is what disambiguates e.g. NFL Patrick Mahomes from an
# unrelated MLB pitcher of the same name).
SPOTRAC_POSITION_MAP = {
    "quarterback": "QB",
    "running back": "RB",
    "fullback": "RB",
    "wide receiver": "WR",
    "tight end": "TE",
    "kicker": "K",
    "punter": "P",
}

# The "(CURRENT)" contract table's title looks like "2026-2033renegotiation
# (CURRENT)" — start year, end year, then a free-text deal type with no
# separator before it.
CURRENT_TABLE_TITLE_RE = re.compile(r"^(?P<start>\d{4})-(?P<end>\d{4})(?P<type>[^(]*)\(CURRENT\)")


def search_spotrac(full_name: str) -> list[dict]:
    """Query Spotrac's site-wide search (via Parse Bot) and return candidate players (any sport)."""
    try:
        r = httpx.get(
            f"{PARSEBOT_BASE_URL}/search_players",
            headers=parsebot_headers(),
            params={"query": full_name},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"Spotrac search failed for {full_name!r}: {e}")
        return []

    if payload.get("status") != "success":
        log.warning(f"Spotrac search returned non-success status for {full_name!r}: {payload}")
        return []

    candidates = []
    for item in payload.get("data", {}).get("results", []):
        if not item.get("player_id") or not item.get("name"):
            continue
        candidates.append({
            "spotrac_id": item["player_id"],
            "name": item["name"].strip(),
            "position_word": (item.get("position") or "").strip(),
        })
    return candidates


def rank_candidates(same_name: list[dict], position: str) -> list[dict]:
    """
    Order same-name Spotrac candidates by confidence: an exact position-word
    match first, then candidates Spotrac's search left unlabelled — which
    happens even for marquee players (the real NFL Patrick Mahomes comes
    back with an empty position while a same-named MLB pitcher gets a real
    one), so an unlabelled candidate still deserves a shot rather than an
    automatic skip. Candidates confidently labelled as a different
    position/sport are dropped. A lone same-name candidate is trusted as-is,
    matching the old behaviour for positions (DL/LB/DB/OL, ...) that aren't
    in SPOTRAC_POSITION_MAP at all.
    """
    if len(same_name) == 1:
        return same_name

    confirmed, unknown = [], []
    for c in same_name:
        mapped = SPOTRAC_POSITION_MAP.get(c["position_word"].strip().lower())
        if mapped == position:
            confirmed.append(c)
        elif not c["position_word"]:
            unknown.append(c)
    return confirmed + unknown


def has_nfl_contract_table(tables: list[dict]) -> bool:
    """
    NFL contract tables carry a "Cap Hit" column. Used to confirm an
    unlabelled same-name candidate is actually the NFL player before
    trusting its data — the "(CURRENT)" table tag alone isn't sport-specific,
    so without this check a namesake's active contract in another sport
    (e.g. an MLB pitcher) could get misattributed to the NFL player.
    """
    return any(
        any("cap hit" in h.lower() for h in (t.get("headers") or []) if h)
        for t in tables
    )


def fetch_contract_details(spotrac_id: str) -> Optional[dict]:
    """Fetch a player's full contract data (Parse Bot's get_player_contract)."""
    try:
        r = httpx.get(
            f"{PARSEBOT_BASE_URL}/get_player_contract",
            headers=parsebot_headers(),
            params={"player_id": spotrac_id},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"Failed to fetch Spotrac contract for player_id={spotrac_id}: {e}")
        return None

    if payload.get("status") != "success":
        log.warning(f"Spotrac contract lookup returned non-success status for player_id={spotrac_id}: {payload}")
        return None

    return payload.get("data")


def summarise_current_contract(tables: list[dict]) -> ContractInfo:
    """
    Parse Bot's get_player_contract returns raw year-by-year tables (cap hit,
    cash, dead cap, earnings history, ...) rather than the clean "summary
    widget" the old HTML page exposed. Rather than fabricate figures Spotrac
    itself doesn't label (total value, guaranteed money), pull the handful of
    facts that can be read directly off the active ("(CURRENT)") deal's table:
    the deal's year span/type, and its first row (current league year)'s cap
    hit and cap percentage.
    """
    for table in tables:
        title = table.get("title", "")
        if "(CURRENT)" not in title:
            continue
        rows = table.get("rows") or []
        headers = table.get("headers") or []
        if not rows:
            continue

        m = CURRENT_TABLE_TITLE_RE.match(title)
        summary: ContractInfo = {
            "contract_span": f"{m.group('start')}-{m.group('end')}" if m else None,
            "contract_type": m.group("type").strip().title() if m and m.group("type").strip() else None,
            "current_year": rows[0][0] if rows[0] else None,
            "final_year": rows[-1][0] if rows[-1] else None,
        }

        def col_index(name_fragment: str) -> Optional[int]:
            for i, h in enumerate(headers):
                if h and name_fragment.lower() in h.lower():
                    return i
            return None

        cap_hit_idx = col_index("cap hit")
        cap_pct_idx = col_index("cap %")
        if cap_hit_idx is not None and cap_hit_idx < len(rows[0]):
            summary["current_year_cap_hit"] = rows[0][cap_hit_idx]
        if cap_pct_idx is not None and cap_pct_idx < len(rows[0]):
            summary["current_year_cap_pct"] = rows[0][cap_pct_idx]

        return summary

    return {}


def resolve_contract(full_name: str, position: str, known_spotrac_id: Optional[str] = None) -> ContractInfo:
    """
    Look up one player's contract on Spotrac. Always returns a dict with a
    "found" flag and contract_updated_at, even on a miss, so a player with
    no Spotrac page (deep stash rookie, etc.) isn't re-queried every run —
    it just gets retried after CONTRACT_FRESHNESS like everything else.

    If known_spotrac_id is supplied (a prior run already matched this player
    to a Spotrac id and cached it), that id is used directly via
    get_player_contract, skipping search_players entirely — the whole point
    of caching the id is to avoid re-searching by name on every refresh. The
    id is only abandoned, falling back to a fresh name search below, if the
    lookup itself fails (bad/retired id); a live id with no active deal this
    cycle still gets kept and returned, since search wouldn't know any more
    than we do about the player's contract status.
    """
    now = datetime.now(timezone.utc).isoformat()

    if known_spotrac_id:
        details = fetch_contract_details(known_spotrac_id)
        if details and details.get("tables"):
            summary = summarise_current_contract(details["tables"])
            result: ContractInfo = {
                "found": bool(summary.get("contract_span")),
                "spotrac_id": known_spotrac_id,
                "contract_updated_at": now,
            }
            if result["found"]:
                result["spotrac_url"] = f"https://www.spotrac.com/nfl/player/_/id/{known_spotrac_id}/"
                result.update(summary)
            return result
        log.info(f"  {full_name}: cached Spotrac id {known_spotrac_id} no longer resolves — re-searching")

    candidates = search_spotrac(full_name)
    target_name = normalise_name(full_name)
    same_name = [c for c in candidates if normalise_name(c["name"]) == target_name]

    if not same_name:
        return {"found": False, "contract_updated_at": now}

    ambiguous = len(same_name) > 1
    for candidate in rank_candidates(same_name, position)[:3]:
        details = fetch_contract_details(candidate["spotrac_id"])
        if not details or not details.get("tables"):
            continue
        if ambiguous and not has_nfl_contract_table(details["tables"]):
            continue

        summary = summarise_current_contract(details["tables"])
        if not summary.get("contract_span"):
            # No active "(CURRENT)" deal in the data (e.g. free agent, retired).
            continue

        return {
            "found": True,
            "spotrac_id": candidate["spotrac_id"],
            "spotrac_url": f"https://www.spotrac.com/nfl/player/_/id/{candidate['spotrac_id']}/",
            **summary,
            "contract_updated_at": now,
        }

    if ambiguous:
        log.info(f"  {full_name}: {len(same_name)} same-name Spotrac candidates, none confirmed as NFL — skipping")
    return {"found": False, "contract_updated_at": now}


def run(players: Sequence[ContractLookupPlayer]) -> dict[str, ContractInfo]:
    """
    Main entry point. Takes the deduplicated roster player list (each dict
    needs at least player_id, full_name, position) and returns a dict of
    player_id (str) -> contract dict, for the orchestrator to merge back
    onto every league's player entries. Also persists results into the
    shared player_cache.json so the next run's cache-freshness check works.
    """
    log.info("Contract agent starting...")
    player_cache = load_player_cache()
    cache_dirty = False

    refreshed = 0
    reused = 0
    skipped = 0
    failed = 0
    contracts_by_pid: dict[str, ContractInfo] = {}

    # Phase 1 (sequential, no network): decide who actually needs a fresh
    # Spotrac lookup vs. who's covered by an unexpired cached contract. Cheap
    # and not worth parallelising — the real cost is entirely in phase 2.
    to_lookup: list[tuple[str, str, str, Optional[str]]] = []
    for player in players:
        pid = str(player.get("player_id"))
        full_name = player.get("full_name", "")
        position = player.get("position", "UNK")

        if not full_name or position in SKIP_POSITIONS:
            skipped += 1
            continue

        entry = player_cache.setdefault(pid, {"sleeper_player_id": pid, "full_name": full_name})
        contract = entry.get("contract")

        if contract and not is_stale(contract.get("contract_updated_at"), CONTRACT_FRESHNESS):
            contracts_by_pid[pid] = contract
            reused += 1
            continue

        known_spotrac_id = contract.get("spotrac_id") if contract else None
        to_lookup.append((pid, full_name, position, known_spotrac_id))

    def lookup_one(item: tuple[str, str, str, Optional[str]]) -> tuple[str, str, Optional[ContractInfo]]:
        pid, full_name, position, known_spotrac_id = item
        log.info(f"  Looking up contract: {full_name} ({position})")
        try:
            contract = resolve_contract(full_name, position, known_spotrac_id)
        except Exception as e:
            # One player's malformed Spotrac payload shouldn't cost every
            # other lookup already in flight — isolate it and move on
            # instead of letting it propagate out of the whole batch.
            log.warning(f"  {full_name}: contract lookup crashed, skipping this run: {e}")
            contract = None
        finally:
            # Stay under Parse Bot's rate limit per-worker — CONTRACT_LOOKUP_WORKERS
            # bounds how many of these run concurrently, this bounds how fast any
            # one of them fires requests, so the two multiply rather than one
            # undoing the other.
            time.sleep(0.6)
        return pid, full_name, contract

    # Phase 2 (concurrent, network-bound): CONTRACT_LOOKUP_WORKERS lookups in
    # flight at once instead of one at a time — the 0.6s pacing above is
    # per-worker, so total throughput scales with the worker count rather
    # than being flattened back to one request every 0.6s. executor.map()
    # preserves submission order for the results below, which keeps the
    # "save every 10th" checkpoint below meaningful even though completion
    # order across threads isn't guaranteed to match it exactly.
    try:
        if to_lookup:
            with concurrent.futures.ThreadPoolExecutor(max_workers=CONTRACT_LOOKUP_WORKERS) as executor:
                for pid, full_name, contract in executor.map(lookup_one, to_lookup):
                    if contract is None:
                        failed += 1
                        continue
                    player_cache[pid]["contract"] = contract
                    cache_dirty = True
                    contracts_by_pid[pid] = contract
                    refreshed += 1

                    # Save incrementally, not just once at the very end — a
                    # roster-wide run makes hundreds of external requests, and
                    # without this a crash or interruption partway through
                    # would throw away every contract already looked up,
                    # forcing a full re-fetch of the whole roster next run.
                    if refreshed % 10 == 0:
                        save_player_cache(player_cache)
    finally:
        if cache_dirty:
            save_player_cache(player_cache)

    log.info(
        f"Contract agent complete. {refreshed} looked up, {reused} reused (<{CONTRACT_FRESHNESS.days}d old), "
        f"{skipped} skipped (no name / non-player position), {failed} failed. Cache: {PLAYER_CACHE_PATH}"
    )
    return contracts_by_pid


if __name__ == "__main__":
    import json
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [CONTRACT] %(message)s")
    mock_players: list[ContractLookupPlayer] = [
        {"player_id": "test-mahomes", "full_name": "Patrick Mahomes", "position": "QB"},
    ]
    print(json.dumps(run(mock_players), indent=2))
