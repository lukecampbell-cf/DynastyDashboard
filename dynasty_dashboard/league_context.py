"""Weekly Sleeper standings, playoff odds, and current matchup context."""

from __future__ import annotations

import hashlib
import json
import logging
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from .common import write_json_atomic
from .paths import PROJECT_ROOT

log = logging.getLogger(__name__)

LEAGUE_CONTEXT_CACHE_PATH = PROJECT_ROOT / "league_context_cache.json"
SIMULATIONS = 5000


def _load_cache(path: Path = LEAGUE_CONTEXT_CACHE_PATH) -> dict:
    try:
        with open(path) as f:
            value = json.load(f)
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, ValueError):
        return {}


def _refresh_due(entry: Optional[dict], season: str, week: int, now: datetime) -> bool:
    """Refresh once each Sleeper week, no later than the first Tue-Thu run."""
    if not entry or entry.get("season") != season or entry.get("week") != week:
        return True
    try:
        fetched = datetime.fromisoformat(entry["fetched_at"])
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return True
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    tuesday = monday + timedelta(days=1)
    # Before Tuesday, the prior successful fetch remains the weekly snapshot.
    return now >= tuesday and fetched < tuesday


def _points_for(roster: dict) -> float:
    settings = roster.get("settings") or {}
    return float(settings.get("fpts") or 0) + float(settings.get("fpts_decimal") or 0) / 100


def _record(roster: dict) -> dict:
    settings = roster.get("settings") or {}
    wins = int(settings.get("wins") or 0)
    losses = int(settings.get("losses") or 0)
    ties = int(settings.get("ties") or 0)
    return {"wins": wins, "losses": losses, "ties": ties, "points_for": round(_points_for(roster), 2)}


def _team_name(roster: dict, users: dict) -> str:
    user = users.get(roster.get("owner_id"), {})
    metadata = user.get("metadata") or {}
    return metadata.get("team_name") or user.get("display_name") or f"Roster {roster.get('roster_id', '?')}"


def _projection(matchup: dict) -> Optional[float]:
    """Read a projection only when the league feed explicitly exposes one."""
    for key in ("projected_points", "projected_score", "projection", "projections"):
        value = matchup.get(key)
        if isinstance(value, (int, float)):
            return round(float(value), 2)
        if isinstance(value, dict):
            for nested in ("total", "points", "projected_points"):
                if isinstance(value.get(nested), (int, float)):
                    return round(float(value[nested]), 2)
    metadata = matchup.get("metadata") or {}
    for key in ("projected_points", "projected_score"):
        if isinstance(metadata.get(key), (int, float)):
            return round(float(metadata[key]), 2)
    return None


def _schedule_for_week(matchups: list[dict]) -> list[tuple[int, int]]:
    grouped: dict[object, list[int]] = {}
    for item in matchups:
        matchup_id = item.get("matchup_id")
        roster_id = item.get("roster_id")
        if matchup_id is not None and roster_id is not None:
            grouped.setdefault(matchup_id, []).append(int(roster_id))
    return [(ids[0], ids[1]) for ids in grouped.values() if len(ids) == 2]


def playoff_probabilities(
    rosters: list[dict], schedules: list[list[tuple[int, int]]], playoff_teams: int, seed: str
) -> dict[int, float]:
    """Monte Carlo odds using smoothed win rates and the remaining schedule."""
    if not rosters:
        return {}
    playoff_teams = max(1, min(int(playoff_teams), len(rosters)))
    base = {int(r["roster_id"]): _record(r) for r in rosters}
    strengths = {
        rid: (rec["wins"] + rec["ties"] * 0.5 + 2) / (rec["wins"] + rec["losses"] + rec["ties"] + 4)
        for rid, rec in base.items()
    }
    counts = {rid: 0 for rid in base}
    rng = random.Random(int(hashlib.sha256(seed.encode()).hexdigest()[:16], 16))
    for _ in range(SIMULATIONS):
        wins = {rid: rec["wins"] + rec["ties"] * 0.5 for rid, rec in base.items()}
        for schedule in schedules:
            for a, b in schedule:
                if a not in wins or b not in wins:
                    continue
                sa, sb = strengths[a], strengths[b]
                chance_a = sa / (sa + sb) if sa + sb else 0.5
                wins[a if rng.random() < chance_a else b] += 1
        # Current points-for is the deterministic Sleeper-style tiebreak proxy.
        ranked = sorted(wins, key=lambda rid: (wins[rid], base[rid]["points_for"], rng.random()), reverse=True)
        for rid in ranked[:playoff_teams]:
            counts[rid] += 1
    return {rid: round(count / SIMULATIONS * 100, 1) for rid, count in counts.items()}


def build_context(
    league: dict,
    rosters: list[dict],
    users: dict,
    my_roster_id: int,
    week: int,
    fetch_matchups: Callable[[str, int], list[dict]],
) -> dict:
    league_id = str(league["league_id"])
    settings = league.get("settings") or {}
    playoff_start = int(settings.get("playoff_week_start") or 15)
    playoff_teams = int(settings.get("playoff_teams") or min(6, len(rosters)))
    weekly_feeds = {w: fetch_matchups(league_id, w) for w in range(week, max(week + 1, playoff_start))}
    odds = playoff_probabilities(
        rosters,
        # Sleeper records reflect completed games, so the current week's game
        # is still part of the remaining path to the playoffs.
        [_schedule_for_week(weekly_feeds[w]) for w in range(week, playoff_start)],
        playoff_teams,
        f"{league_id}:{league.get('season')}:{week}",
    )
    standings: list[dict[str, Any]] = []
    for roster in rosters:
        rid = int(roster["roster_id"])
        standings.append({
            "roster_id": rid,
            "team_name": _team_name(roster, users),
            "is_me": rid == int(my_roster_id),
            "record": _record(roster),
            "playoff_chance": odds.get(rid, 0.0),
        })
    standings.sort(
        key=lambda row: (row["record"]["wins"], row["record"]["ties"], row["record"]["points_for"]),
        reverse=True,
    )
    for rank, row in enumerate(standings, 1):
        row["rank"] = rank

    current = weekly_feeds.get(week, [])
    mine = next((m for m in current if int(m.get("roster_id", -1)) == int(my_roster_id)), None)
    matchup = None
    if mine and mine.get("matchup_id") is not None:
        opponent = next(
            (m for m in current if m.get("matchup_id") == mine.get("matchup_id") and m is not mine), None
        )
        if opponent:
            opponent_roster = next((r for r in rosters if r.get("roster_id") == opponent.get("roster_id")), {})
            matchup = {
                "week": week,
                "my_team": _team_name(next(r for r in rosters if int(r["roster_id"]) == int(my_roster_id)), users),
                "opponent_team": _team_name(opponent_roster, users),
                "my_points": round(float(mine.get("points") or 0), 2),
                "opponent_points": round(float(opponent.get("points") or 0), 2),
                "my_projection": _projection(mine),
                "opponent_projection": _projection(opponent),
            }
    mine_row = next((row for row in standings if row["is_me"]), None)
    return {
        "week": week,
        "playoff_start_week": playoff_start,
        "playoff_teams": playoff_teams,
        "my_standing": mine_row,
        "standings": standings,
        "matchup": matchup,
    }


def get_league_context(
    league: dict,
    rosters: list[dict],
    users: dict,
    my_roster_id: int,
    week: int,
    fetch_matchups: Callable[[str, int], list[dict]],
    *,
    now: Optional[datetime] = None,
    cache_path: Path = LEAGUE_CONTEXT_CACHE_PATH,
) -> dict:
    now = now or datetime.now(timezone.utc)
    cache = _load_cache(cache_path)
    league_id = str(league["league_id"])
    entry = cache.get(league_id)
    season = str(league.get("season") or "")
    if not _refresh_due(entry, season, week, now):
        log.info("Using weekly league context cache for league=%s week=%s", league_id, week)
        assert isinstance(entry, dict)
        return entry["context"]
    context = build_context(league, rosters, users, my_roster_id, week, fetch_matchups)
    cache[league_id] = {"season": season, "week": week, "fetched_at": now.isoformat(), "context": context}
    write_json_atomic(cache_path, cache, sort_keys=True)
    return context
