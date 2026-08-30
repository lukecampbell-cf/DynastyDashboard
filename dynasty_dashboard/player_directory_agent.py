"""
Player Directory Agent
Builds a single, comprehensive player + trade-value directory covering every
fantasy-relevant NFL player Sleeper knows about — not just players rostered
in your own leagues (player_cache.json, ~180 players) or RosterAudit's own
top-ranked set (trade_values.json's players lists, ~390 per format). Exists
so trade_calculator.php can look up ANY NFL player, not just yours.

Built almost entirely from sources the pipeline already fetches for other
reasons:
  - Sleeper's full /players/nfl database (sleeper_agent.get_nfl_players(),
    cached 24h at /tmp/sleeper_players_cache.json)
  - RosterAudit's dynasty trade values, both sf and 1qb (trade_value_agent.py,
    refreshed weekly, trade_values.json)
  - FantasyPros dynasty consensus rank (sleeper_agent.fetch_fantasypros_rankings,
    cached 24h) — only consulted as a fallback, see below.

RosterAudit only prices its own top ~390 players per format. Everyone else
in the directory would otherwise show as a flat 0 ("Deep Stash"), even
players FantasyPros ranks reasonably. Rather than inventing an arbitrary
number from a FantasyPros rank alone (rank is ordinal, not a value), the
fallback in build_calibration_curve()/estimate_value_from_rank() below
calibrates against RosterAudit's own value curve: for every player priced by
*both* systems, we know (fp_rank, ra_value); an unpriced player's value is
linearly interpolated between the two calibration points bracketing their
own fp_rank. This keeps estimates on the same numeric scale as real
RosterAudit values instead of a disconnected made-up scale, while every
estimated entry is still tagged `"source": "fantasypros_estimate"` (vs.
`"rosteraudit"` or `"unranked"`) so callers can tell real market data from a
derived guess.

Refreshed at most once a week (DIRECTORY_FRESHNESS_SECONDS below) — matches
RosterAudit's own cadence, since that's the piece of this file that actually
moves week to week; team/roster data and FantasyPros consensus rank change
slowly enough that a week-old snapshot is fine for a trade calculator.
"""

import json
import logging
from typing import Optional

from . import trade_value_agent
from .common import is_stale, normalise_name, write_json_atomic
from .paths import PROJECT_ROOT

log = logging.getLogger(__name__)

PLAYER_DIRECTORY_PATH = PROJECT_ROOT / "player_directory.json"
DIRECTORY_FRESHNESS_SECONDS = 7 * 86400  # once a week, same cadence as RosterAudit

# Only fantasy-relevant positions, and only players actually on an NFL
# roster — Sleeper's full DB is ~12,000 entries, most of them positions this
# project has never tracked (OL/DL/LB/DB/CB, IDP-only) or players with no
# team (retired, practice-squad-only, true free agents) that a dynasty
# manager would never actually trade.
RELEVANT_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF"}


def load_player_directory() -> Optional[dict]:
    if not PLAYER_DIRECTORY_PATH.exists():
        return None
    try:
        with open(PLAYER_DIRECTORY_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Failed to read {PLAYER_DIRECTORY_PATH.name}, will rebuild: {e}")
        return None


def save_player_directory(data: dict) -> None:
    try:
        write_json_atomic(PLAYER_DIRECTORY_PATH, data, sort_keys=True)
        log.info(f"Saved player directory to {PLAYER_DIRECTORY_PATH.name}")
    except OSError as e:
        log.error(f"Failed to write {PLAYER_DIRECTORY_PATH.name}: {e}")


def _display_name(sleeper_id: str, entry: dict) -> str:
    if entry.get("full_name"):
        return entry["full_name"]
    if entry.get("position") == "DEF" and entry.get("team"):
        return f"{entry['team']} D/ST"
    return sleeper_id


def build_calibration_curve(candidates: dict, ra_players_for_format: dict) -> list[tuple[float, float]]:
    """
    (fp_rank, ra_value) pairs, sorted by fp_rank ascending, for every
    candidate priced by RosterAudit AND matched to a FantasyPros rank —
    the calibration curve estimate_value_from_rank() interpolates against.
    """
    points = []
    for sleeper_id, c in candidates.items():
        if c["fp_rank"] is None:
            continue
        ra_entry = ra_players_for_format.get(sleeper_id)
        if ra_entry and ra_entry.get("value") is not None:
            points.append((float(c["fp_rank"]), float(ra_entry["value"])))
    points.sort(key=lambda pair: pair[0])
    return points


def estimate_value_from_rank(fp_rank: float, calibration: list[tuple[float, float]]) -> float:
    """
    Linearly interpolate a RosterAudit-scale value for a player RosterAudit
    hasn't priced, from the two calibration points (real RosterAudit-priced
    players with a FantasyPros rank) bracketing this fp_rank. Assumes
    `calibration` is sorted by fp_rank ascending and non-empty.
    """
    if fp_rank <= calibration[0][0]:
        return calibration[0][1]
    if fp_rank >= calibration[-1][0]:
        # Past the last (worst) calibration point: extend its trend, but
        # never let a matched-but-unpriced player read as literally 0 —
        # that's reserved for players with no signal at all.
        (r1, v1), (r2, v2) = calibration[-2], calibration[-1]
        if r2 == r1:
            return max(1.0, v2)
        slope = (v2 - v1) / (r2 - r1)
        return max(1.0, v2 + slope * (fp_rank - r2))

    lo, hi = 0, len(calibration) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if calibration[mid][0] <= fp_rank:
            lo = mid
        else:
            hi = mid
    r1, v1 = calibration[lo]
    r2, v2 = calibration[hi]
    if r2 == r1:
        return v1
    return v1 + (v2 - v1) * (fp_rank - r1) / (r2 - r1)


def build_player_directory(all_players: dict, rosteraudit_data: dict, fp_rankings: dict) -> dict:
    """
    Filter Sleeper's full player DB down to fantasy-relevant, rostered
    players, and attach each one's trade value (both sf and 1qb):
    RosterAudit's real value where it's priced the player, otherwise a
    FantasyPros-rank-calibrated estimate (see estimate_value_from_rank), and
    only falling all the way back to 0 / "Deep Stash" when neither system
    has any signal on the player at all.
    """
    formats = (rosteraudit_data or {}).get("formats", {})
    ra_players = {
        fmt: (formats.get(fmt) or {}).get("players", {})
        for fmt in ("sf", "1qb")
    }
    ra_tier_charts = {
        fmt: (formats.get(fmt) or {}).get("tier_chart", [])
        for fmt in ("sf", "1qb")
    }

    candidates = {}
    for sleeper_id, entry in all_players.items():
        position = entry.get("position")
        if position not in RELEVANT_POSITIONS:
            continue
        # Team defenses have no "active"/"team" roster concept the same way
        # — they're always eligible. Everyone else must be an active player
        # actually on an NFL roster.
        if position != "DEF" and (not entry.get("active") or not entry.get("team")):
            continue

        fp_rank = None
        full_name = entry.get("full_name")
        if full_name:
            fp_entry = fp_rankings.get(normalise_name(full_name))
            if fp_entry:
                fp_rank = fp_entry.get("fp_rank")

        candidates[str(sleeper_id)] = {"entry": entry, "fp_rank": fp_rank}

    directory: dict = {}
    for fmt in ("sf", "1qb"):
        calibration = build_calibration_curve(candidates, ra_players[fmt])

        for sleeper_id, c in candidates.items():
            ra_entry = ra_players[fmt].get(sleeper_id)
            if ra_entry and ra_entry.get("value") is not None:
                value = float(ra_entry["value"])
                tier = ra_entry.get("tier")
                source = "rosteraudit"
            elif c["fp_rank"] is not None and calibration:
                value = estimate_value_from_rank(c["fp_rank"], calibration)
                tier = trade_value_agent.trade_value_label(value, ra_tier_charts[fmt])
                source = "fantasypros_estimate"
            else:
                value = 0.0
                tier = "Deep Stash"
                source = "unranked"

            directory.setdefault(sleeper_id, {
                "name": _display_name(sleeper_id, c["entry"]),
                "position": c["entry"].get("position"),
                "team": c["entry"].get("team"),
                "values": {},
            })
            directory[sleeper_id]["values"][fmt] = {
                "value": round(value, 1),
                "tier": tier,
                "source": source,
            }

    return directory


def run(all_players: dict, rosteraudit_data: dict, force: bool = False) -> dict:
    """
    Main entry point. Rebuilds player_directory.json only if it's missing or
    older than a week (or force=True) — building it is cheap (a pure
    in-memory merge of data the pipeline already fetched this run for other
    steps), but there's no reason to rewrite the file every 4-hour cron tick
    when nothing in it would meaningfully change.
    """
    if not force and not is_stale(PLAYER_DIRECTORY_PATH, DIRECTORY_FRESHNESS_SECONDS):
        cached = load_player_directory()
        if cached is not None:
            log.info(f"Using cached player directory ({len(cached)} players)")
            return cached

    from .sleeper_agent import fetch_fantasypros_rankings

    log.info("Building player directory from Sleeper + RosterAudit + FantasyPros data...")
    # Dynasty rankings are scoring-format-agnostic (see sleeper_agent.py) —
    # one fetch covers both sf and 1qb estimates.
    fp_rankings = fetch_fantasypros_rankings("dynasty")
    directory = build_player_directory(all_players, rosteraudit_data, fp_rankings)

    if not directory:
        cached = load_player_directory()
        if cached:
            log.warning("Player directory build produced 0 players — keeping stale cached directory.")
            return cached

    save_player_directory(directory)
    log.info(f"Player directory built: {len(directory)} players")
    return directory


if __name__ == "__main__":
    import sleeper_agent

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [PLAYER DIRECTORY] %(message)s")
    players = sleeper_agent.get_nfl_players()
    rosteraudit = trade_value_agent.run()
    result = run(players, rosteraudit, force=True)
    print(f"{len(result)} players in directory")
