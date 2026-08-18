"""
Orchestrator
Runs the full Dynasty Dashboard pipeline:
  1. Sleeper Agent   — fetch leagues, rosters, and bio details (14-day cache)
  2. Contract Agent  — Spotrac contract lookup per player (28-day cache)
  3. News Agent      — scrape injury and trade news
  4. Reasoning Agent — AI analysis and trend classification
  5. Dashboard Agent — render and publish HTML

Usage:
  python orchestrator.py
  python orchestrator.py --season 2025
  python orchestrator.py --dry-run   (renders HTML to /tmp instead of web root)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load .env from same directory as this script
env_path = Path(__file__).parent / ".env"
load_dotenv(dotenv_path=env_path)

import sleeper_agent
import contract_agent
import news_agent
import reasoning_agent
import dashboard_agent
import health_agent
from schemas import NewsOutput, PipelineResult, ReasoningOutput, SleeperOutput, StageResult

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "pipeline.log", mode="a"),
    ]
)
log = logging.getLogger("orchestrator")

OUTPUT_PATH = os.environ.get("DASHBOARD_OUTPUT_PATH", "")
DRY_RUN_PATH = "/tmp/dynasty_dashboard_preview.html"


def check_environment(dry_run: bool = False) -> bool:
    """Validate required environment variables are set."""
    key = os.environ.get("DASHBOARD_KEY")
    if not key:
        log.error("DASHBOARD_KEY not set. Add it to your .env file.")
        log.error("  echo 'DASHBOARD_KEY=your-key-here' > .env")
        return False
    if not key.startswith("sk-ant-"):
        log.error("DASHBOARD_KEY does not look like a valid Anthropic API key.")
        return False
    if not os.environ.get("SLEEPER_USERNAME"):
        log.error("SLEEPER_USERNAME not set. Add it to your .env file.")
        return False
    if not dry_run and not os.environ.get("DASHBOARD_OUTPUT_PATH"):
        log.error("DASHBOARD_OUTPUT_PATH not set. Add it to your .env file.")
        return False
    if not os.environ.get("PARSE_BOT_API"):
        log.warning(
            "PARSE_BOT_API not set — Contract Agent and ESPN news fetching will fail "
            "(non-fatal: those steps degrade gracefully with no data)."
        )
    log.info("Environment check passed.")
    return True


def run_pipeline(dry_run: bool = False, debug: bool = False) -> PipelineResult:
    """
    Execute the full agent pipeline.
    Season is resolved dynamically by the Sleeper agent.

    Returns a PipelineResult: {"success": bool, "stages": [StageResult, ...]}.
    Each stage is reported success/degraded/failed (see schemas.StageResult)
    rather than just the plain ok/error `steps` dict — which is still built
    alongside `stages` and passed to health_agent.record_run() unchanged,
    since health.json's on-disk schema isn't part of this change. A stage
    marked degraded=True still counts toward overall success; only a stage
    the pipeline treats as fatal (sleeper, reasoning) sets success=False.

    Tracks a per-step ok/error record in `steps` as it goes, and always
    writes health_agent.record_run(steps, ...) before returning — on every
    exit path, not just the success one, so a failed run still updates
    health.json instead of leaving it frozen at the last good state.

    When `debug` is set, intermediate step outputs are dumped to disk via
    save_pipeline_data() right after the Reasoning step, so a dashboard
    render failure doesn't lose them.
    """
    started_at = time.time()
    log.info("=" * 60)
    log.info(f"Pipeline starting — season will be resolved dynamically, dry_run={dry_run}")
    log.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 60)

    steps: dict = {}
    stages: list[StageResult] = []

    def finish(success: bool) -> PipelineResult:
        health_agent.record_run(steps, success)
        return {"success": success, "stages": stages}

    # ── STEP 1: Sleeper Agent ──────────────────────────────────
    log.info("STEP 1/5: Sleeper Agent")
    try:
        sleeper_data = sleeper_agent.run()
        season = sleeper_data.get("season", "unknown")
        league_count = len(sleeper_data.get("leagues", []))
        if league_count == 0:
            log.warning("No leagues found. Dashboard will render with empty state.")
        else:
            total_players = sum(len(l.get("players", [])) for l in sleeper_data["leagues"])
            log.info(f"Sleeper: season={season}, {league_count} league(s), {total_players} total players")
        steps["sleeper"] = {"ok": True, "error": None}
        stages.append({"name": "sleeper", "success": True, "degraded": False, "message": None})

        # Trade values are fetched inside sleeper_agent.run() (it's the
        # module that already calls trade_value_agent), but reported here as
        # their own stage — see SleeperOutput.trade_values_degraded.
        degraded_formats = sleeper_data.get("trade_values_degraded", [])
        if degraded_formats:
            message = f"{', '.join(degraded_formats)} refresh failed; cached data retained"
            log.warning(f"Trade values: {message}")
            stages.append({"name": "trade_values", "success": True, "degraded": True, "message": message})
        else:
            stages.append({"name": "trade_values", "success": True, "degraded": False, "message": None})
    except Exception as e:
        log.error(f"Sleeper agent failed: {e}")
        steps["sleeper"] = {"ok": False, "error": str(e)}
        stages.append({"name": "sleeper", "success": False, "degraded": False, "message": str(e)})
        return finish(False)

    # Collect all players across leagues once, deduped by Sleeper player_id —
    # reused by both the contract lookup and news matching below.
    all_players = []
    seen_pids = set()
    for league in sleeper_data.get("leagues", []):
        for p in league.get("players", []):
            pid = p.get("player_id")
            if pid and pid not in seen_pids:
                all_players.append(p)
                seen_pids.add(pid)

    # ── STEP 2: Contract Agent (Spotrac) ───────────────────────
    log.info("STEP 2/5: Contract Agent")
    try:
        contracts_by_pid = contract_agent.run(players=all_players)
        found = sum(1 for c in contracts_by_pid.values() if c.get("found"))
        log.info(f"Contracts: {found}/{len(contracts_by_pid)} players matched on Spotrac")
        for league in sleeper_data.get("leagues", []):
            for p in league.get("players", []):
                p["contract"] = contracts_by_pid.get(str(p.get("player_id")))
        steps["contract"] = {"ok": True, "error": None}
        stages.append({"name": "contract", "success": True, "degraded": False, "message": None})
    except Exception as e:
        log.error(f"Contract agent failed: {e}")
        steps["contract"] = {"ok": False, "error": str(e)}
        # Non-fatal — reasoning falls back to hedged general-knowledge notes
        log.warning("Continuing pipeline without fresh contract data.")
        stages.append({"name": "contract", "success": True, "degraded": True, "message": str(e)})

    # ── STEP 3: News Agent ─────────────────────────────────────
    log.info("STEP 3/5: News Agent")
    try:
        news_data = news_agent.run(roster_players=all_players)
        log.info(f"News: {news_data['total_items']} items, {news_data['unique_players']} unique players")
        failed_sources = {k: v for k, v in news_data.get("source_status", {}).items() if not v["ok"]}
        if failed_sources:
            log.warning(f"News sources with errors this run: {list(failed_sources)}")
        steps["news"] = {"ok": True, "error": None, "sources": news_data.get("source_status", {})}
        stages.append({
            "name": "news", "success": True, "degraded": bool(failed_sources),
            "message": f"sources with errors: {list(failed_sources)}" if failed_sources else None,
        })
    except Exception as e:
        log.error(f"News agent failed: {e}")
        steps["news"] = {"ok": False, "error": str(e), "sources": {}}
        # Non-fatal — continue with empty news
        news_data = {
            "total_items": 0,
            "unique_players": 0,
            "news_by_player": {},
            "all_items": [],
            "source_status": {},
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        log.warning("Continuing pipeline with empty news data.")
        stages.append({"name": "news", "success": True, "degraded": True, "message": str(e)})

    # ── STEP 4: Reasoning Agent ────────────────────────────────
    log.info("STEP 4/5: Reasoning Agent")
    try:
        reasoning_data = reasoning_agent.run(sleeper_data=sleeper_data, news_data=news_data)
        total_analysed = sum(
            l.get("stats", {}).get("total", 0)
            for l in reasoning_data.get("leagues", [])
        )
        log.info(f"Reasoning: {total_analysed} players analysed across {len(reasoning_data['leagues'])} league(s)")
        steps["reasoning"] = {"ok": True, "error": None}
        stages.append({"name": "reasoning", "success": True, "degraded": False, "message": None})
        if debug:
            save_pipeline_data(sleeper_data, news_data, reasoning_data)
    except Exception as e:
        log.error(f"Reasoning agent failed: {e}")
        steps["reasoning"] = {"ok": False, "error": str(e)}
        stages.append({"name": "reasoning", "success": False, "degraded": False, "message": str(e)})
        return finish(False)

    # ── STEP 5: Dashboard Agent ────────────────────────────────
    log.info("STEP 5/5: Dashboard Agent")
    output_path = DRY_RUN_PATH if dry_run else OUTPUT_PATH
    try:
        success = dashboard_agent.run(reasoning_data=reasoning_data, output_path=output_path)
        steps["dashboard"] = {"ok": success, "error": None if success else "write_dashboard failed"}
        stages.append({
            "name": "dashboard", "success": success, "degraded": False,
            "message": None if success else "write_dashboard failed",
        })
        if success:
            elapsed = round(time.time() - started_at, 1)
            log.info(f"Pipeline complete in {elapsed}s → {output_path}")
            if dry_run:
                log.info(f"DRY RUN: Open file://{output_path} in your browser to preview.")
        return finish(success)
    except Exception as e:
        log.error(f"Dashboard agent failed: {e}")
        steps["dashboard"] = {"ok": False, "error": str(e)}
        stages.append({"name": "dashboard", "success": False, "degraded": False, "message": str(e)})
        return finish(False)


def save_pipeline_data(sleeper_data: SleeperOutput, news_data: NewsOutput, reasoning_data: ReasoningOutput):
    """Save intermediate pipeline data for debugging."""
    debug_dir = Path(__file__).parent / "debug"
    debug_dir.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    for name, data in [
        ("sleeper", sleeper_data),
        ("news", news_data),
        ("reasoning", reasoning_data),
    ]:
        path = debug_dir / f"{ts}_{name}.json"
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    log.info(f"Debug data saved to {debug_dir}/")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Dynasty Dashboard Pipeline")
    parser.add_argument("--dry-run", action="store_true", help="Write to /tmp instead of web root")
    parser.add_argument("--debug", action="store_true", help="Save intermediate pipeline data")
    args = parser.parse_args()

    if not check_environment(dry_run=args.dry_run):
        sys.exit(1)

    result = run_pipeline(dry_run=args.dry_run, debug=args.debug)
    sys.exit(0 if result["success"] else 1)
