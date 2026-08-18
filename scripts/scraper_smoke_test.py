"""
Scraper Smoke Test
Hits every news_agent.py source for real (live network, no mocks) and
asserts each one returns non-empty, well-formed news items — the same
selectors/Parse Bot scrapers that have already drifted silently once (see
FANTASYPROS_SCRAPER_ID's history in news_agent.py) before anyone noticed
outside of a quiet health.json entry.

Deliberately NOT named test_*.py: it makes live requests to third-party
sites and an external API, so it must not run on every push/PR alongside
the mocked unit tests. Run manually with:

    python scripts/scraper_smoke_test.py

or on the weekly cron in .github/workflows/scraper-smoke.yml.

Exit code is 0 only if every source that's expected to work in this
environment returned at least one item. Parse Bot-backed sources
(fantasypros_news, fantasypros_injuries, espn) are skipped rather than
failed when PARSE_BOT_API isn't set, since that's a paid key not every
environment running this script will have.
"""

import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import news_agent  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [SMOKE] %(message)s")
log = logging.getLogger("scraper_smoke_test")

# Sources that need PARSE_BOT_API — skipped (not failed) when it's unset.
PARSE_BOT_SOURCES = {"fantasypros_news", "fantasypros_injuries", "espn"}


def main() -> int:
    have_parsebot_key = bool(os.environ.get("PARSE_BOT_API"))
    if not have_parsebot_key:
        log.warning("PARSE_BOT_API not set — Parse Bot-backed sources will be skipped, not tested.")

    failures = []
    skipped = []

    for label, scraper in news_agent.SOURCES:
        if label in PARSE_BOT_SOURCES and not have_parsebot_key:
            skipped.append(label)
            continue

        log.info(f"Checking {label}...")
        items, error = scraper()

        if error:
            failures.append(f"{label}: fetch failed ({error})")
            log.error(f"{label}: FAILED — {error}")
            continue

        if not items:
            failures.append(f"{label}: returned zero items")
            log.error(f"{label}: FAILED — zero items (selectors likely drifted)")
            continue

        malformed = [
            item for item in items
            if not item.get("headline") and not item.get("player_name")
        ]
        if malformed:
            failures.append(f"{label}: {len(malformed)}/{len(items)} item(s) missing headline and player_name")
            log.error(f"{label}: FAILED — {len(malformed)}/{len(items)} malformed item(s)")
            continue

        log.info(f"{label}: OK — {len(items)} item(s)")

    log.info("=" * 60)
    if skipped:
        log.warning(f"Skipped (no PARSE_BOT_API): {', '.join(skipped)}")
    if failures:
        log.error(f"{len(failures)} source(s) failed:")
        for f in failures:
            log.error(f"  - {f}")
        return 1

    log.info("All checked sources returned non-empty, well-formed output.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
