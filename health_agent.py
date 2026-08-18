"""
Health Agent
Tracks orchestrator-level pipeline health across runs: which step last
succeeded or failed (including per-source detail for news_agent.py, since a
single "news step failed" flag hides which of the six sources is actually
down), when the pipeline last completed successfully end to end, and
whether any of the calendar-based caches have gone stale — a real signal
that something has been silently broken for days (an expired API key, a
changed selector, a dead cron job), not just "no news today."

Written by orchestrator.py at the end of every run — success or failure —
so a failed run still updates health.json rather than leaving it silently
frozen at the last good state. Read it directly (it's plain JSON, e.g. for
an uptime-monitor HTTP check against `overall_ok`) or run this file
directly for a human-readable summary.
"""

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from common import write_json_atomic
from schemas import CacheStatus, HealthRecord, StepStatus

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
HEALTH_PATH = PROJECT_ROOT / "health.json"

# (display name, path, freshness threshold in seconds) for every cache this
# project maintains with a real, calendar-based expiry. Most thresholds are
# duplicated from each agent's own freshness constant (sleeper_agent.
# DETAILS_FRESHNESS, trade_value_agent.RA_FRESHNESS_SECONDS,
# player_directory_agent.DIRECTORY_FRESHNESS_SECONDS) rather than imported,
# so a health check never depends on successfully importing every agent
# module first — this file has to stay import-light enough to run even when
# something else in the pipeline is broken.
#
# player_analysis_cache.json is the exception: it's rewritten unconditionally
# at the end of every reasoning_agent.run(), so its mtime doubles as "how
# long since the pipeline last completed step 4" — a proxy for "is cron
# still running at all." Its own internal per-player freshness window
# (reasoning_agent.CACHE_FRESHNESS, 4h) is deliberately NOT reused as the
# threshold here — that equals the recommended cron interval itself, so the
# file would read "stale" for roughly half of every cycle even when cron is
# firing exactly on schedule. 3x the recommended interval gives room for a
# missed or delayed tick without false-alarming on normal cron jitter.
TRACKED_CACHES = [
    ("player_cache.json", PROJECT_ROOT / "player_cache.json", 14 * 86400),
    ("trade_values.json", PROJECT_ROOT / "trade_values.json", 7 * 86400),
    ("player_directory.json", PROJECT_ROOT / "player_directory.json", 7 * 86400),
    ("player_analysis_cache.json", PROJECT_ROOT / "player_analysis_cache.json", 3 * 4 * 3600),
]


def load_health() -> dict:
    if not HEALTH_PATH.exists():
        return {}
    try:
        with open(HEALTH_PATH) as f:
            return json.load(f)
    except Exception as e:
        log.warning(f"Failed to read {HEALTH_PATH.name}, starting fresh: {e}")
        return {}


def check_stale_caches() -> list[CacheStatus]:
    """
    One entry per tracked cache: whether it exists, its age, and whether
    it's past its own freshness threshold. A missing file is always
    reported stale — "never built yet" and "stopped refreshing" both matter,
    just for different reasons, and the `exists` flag lets a caller tell
    them apart.
    """
    results: list[CacheStatus] = []
    now = time.time()
    for name, path, threshold_seconds in TRACKED_CACHES:
        if not path.exists():
            results.append({"file": name, "exists": False, "stale": True, "age_hours": None})
            continue
        age_seconds = now - path.stat().st_mtime
        results.append({
            "file": name,
            "exists": True,
            "stale": age_seconds >= threshold_seconds,
            "age_hours": round(age_seconds / 3600, 1),
        })
    return results


def record_run(steps: dict[str, StepStatus], pipeline_success: bool) -> HealthRecord:
    """
    Write health.json for this run.

    `steps` is step_name -> {"ok": bool, "error": str | None, ...}. A step
    orchestrator.py never reached (e.g. because an earlier fatal step
    failed) is simply absent, not reported as ok=False — "didn't run" and
    "ran and failed" are different states worth being able to tell apart in
    the output. `pipeline_success` mirrors run_pipeline()'s own return
    value.

    last_success_at only advances when this run actually succeeded — a
    failed run keeps whatever the last real success was, which is the whole
    point of tracking it separately from last_run_at.
    """
    previous = load_health()
    now_iso = datetime.now(timezone.utc).isoformat()
    stale_caches = check_stale_caches()

    health: HealthRecord = {
        "last_run_at": now_iso,
        "last_run_success": pipeline_success,
        "last_success_at": now_iso if pipeline_success else previous.get("last_success_at"),
        "steps": steps,
        "stale_caches": stale_caches,
        "overall_ok": pipeline_success and not any(c["stale"] for c in stale_caches),
    }

    try:
        write_json_atomic(HEALTH_PATH, health)
    except OSError as e:
        log.error(f"Failed to write {HEALTH_PATH.name}: {e}")

    return health


def print_summary(health: Optional[dict] = None) -> None:
    """Human-readable summary — `python health_agent.py`, or called from orchestrator.py after a run."""
    health = health or load_health()
    if not health:
        print("No health data yet — run orchestrator.py first.")
        return

    print(f"Last run:      {health.get('last_run_at', 'never')} ({'OK' if health.get('last_run_success') else 'FAILED'})")
    print(f"Last success:  {health.get('last_success_at') or 'never'}")
    print(f"Overall:       {'HEALTHY' if health.get('overall_ok') else 'ATTENTION NEEDED'}")

    print("\nSteps:")
    for step, result in health.get("steps", {}).items():
        if result.get("ok"):
            print(f"  {step:12s} OK")
        else:
            print(f"  {step:12s} FAILED — {result.get('error')}")
        for source, detail in (result.get("sources") or {}).items():
            flag = "ok" if detail.get("ok") else f"FAILED — {detail.get('error')}"
            print(f"      {source:22s} {flag} ({detail.get('items', 0)} items)")

    print("\nCaches:")
    for c in health.get("stale_caches", []):
        if not c["exists"]:
            print(f"  {c['file']:28s} MISSING")
        else:
            flag = "STALE" if c["stale"] else "fresh"
            print(f"  {c['file']:28s} {flag} ({c['age_hours']}h old)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [HEALTH] %(message)s")
    print_summary()
