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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(Path(__file__).parent / "pipeline.log", mode="a"),
    ]
)
log = logging.getLogger("orchestrator")

OUTPUT_PATH = "/var/www/vhosts/lukesplace.net/httpdocs/dashboard/index.html"
DRY_RUN_PATH = "/tmp/dynasty_dashboard_preview.html"


def check_environment() -> bool:
    """Validate required environment variables are set."""
    key = os.environ.get("DASHBOARD_KEY")
    if not key:
        log.error("DASHBOARD_KEY not set. Add it to your .env file.")
        log.error("  echo 'DASHBOARD_KEY=your-key-here' > .env")
        return False
    if not key.startswith("sk-ant-"):
        log.error("DASHBOARD_KEY does not look like a valid Anthropic API key.")
        return False
    if not os.environ.get("PARSE_BOT_API"):
        log.warning(
            "PARSE_BOT_API not set — Contract Agent and ESPN news fetching will fail "
            "(non-fatal: those steps degrade gracefully with no data)."
        )
    log.info("Environment check passed.")
    return True


def run_pipeline(dry_run: bool = False) -> bool:
    """
    Execute the full agent pipeline.
    Season is resolved dynamically by the Sleeper agent.
    Returns True on success, False on failure.
    """
    started_at = time.time()
    log.info("=" * 60)
    log.info(f"Pipeline starting — season will be resolved dynamically, dry_run={dry_run}")
    log.info(f"Time: {datetime.now(timezone.utc).isoformat()}")
    log.info("=" * 60)

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
    except Exception as e:
        log.error(f"Sleeper agent failed: {e}")
        return False

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
    except Exception as e:
        log.error(f"Contract agent failed: {e}")
        # Non-fatal — reasoning falls back to hedged general-knowledge notes
        log.warning("Continuing pipeline without fresh contract data.")

    # ── STEP 3: News Agent ─────────────────────────────────────
    log.info("STEP 3/5: News Agent")
    try:
        news_data = news_agent.run(roster_players=all_players)
        log.info(f"News: {news_data['total_items']} items, {news_data['unique_players']} unique players")
    except Exception as e:
        log.error(f"News agent failed: {e}")
        # Non-fatal — continue with empty news
        news_data = {
            "total_items": 0,
            "unique_players": 0,
            "news_by_player": {},
            "all_items": [],
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }
        log.warning("Continuing pipeline with empty news data.")

    # ── STEP 4: Reasoning Agent ────────────────────────────────
    log.info("STEP 4/5: Reasoning Agent")
    try:
        reasoning_data = reasoning_agent.run(sleeper_data=sleeper_data, news_data=news_data)
        total_analysed = sum(
            l.get("stats", {}).get("total", 0)
            for l in reasoning_data.get("leagues", [])
        )
        log.info(f"Reasoning: {total_analysed} players analysed across {len(reasoning_data['leagues'])} league(s)")
    except Exception as e:
        log.error(f"Reasoning agent failed: {e}")
        return False

    # ── STEP 5: Dashboard Agent ────────────────────────────────
    log.info("STEP 5/5: Dashboard Agent")
    output_path = DRY_RUN_PATH if dry_run else OUTPUT_PATH
    try:
        success = dashboard_agent.run(reasoning_data=reasoning_data, output_path=output_path)
        if success:
            elapsed = round(time.time() - started_at, 1)
            log.info(f"Pipeline complete in {elapsed}s → {output_path}")
            if dry_run:
                log.info(f"DRY RUN: Open file://{output_path} in your browser to preview.")
        return success
    except Exception as e:
        log.error(f"Dashboard agent failed: {e}")
        return False


def save_pipeline_data(sleeper_data: dict, news_data: dict, reasoning_data: dict):
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

    if not check_environment():
        sys.exit(1)

    success = run_pipeline(dry_run=args.dry_run)
    sys.exit(0 if success else 1)
