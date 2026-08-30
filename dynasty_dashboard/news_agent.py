"""Aggregate source-specific NFL news and enrich Sleeper roster players."""

import concurrent.futures
import logging
from datetime import datetime, timezone
from typing import Callable, Optional

from .common import normalise_name
from .news_sources import (
    scrape_cbssports_nfl, scrape_espn_nfl, scrape_fantasypros_injuries,
    scrape_fantasypros_news, scrape_nfl_news, scrape_rotowire,
)
from .schemas import EnrichedPlayer, NewsByPlayerEntry, NewsItem, NewsOutput, ResolvedPlayer, SourceStatus

log = logging.getLogger(__name__)

INJURY_KEYWORDS = [
    "injured", "injury", "out", "questionable", "doubtful", "ir",
    "placed on", "limited", "did not practice", "dnp", "hamstring",
    "knee", "ankle", "shoulder", "concussion", "surgery",
]

Scraper = Callable[[], tuple[list[NewsItem], Optional[str]]]


def cross_reference(all_items: list[NewsItem]) -> dict[str, NewsByPlayerEntry]:
    """Group news items by normalised player name across sources."""
    grouped: dict[str, NewsByPlayerEntry] = {}
    for item in all_items:
        raw_name = item.get("player_name") or ""
        key = normalise_name(raw_name)
        if not key:
            continue
        if key not in grouped:
            grouped[key] = {
                "player_name": raw_name, "sources": [], "items": [],
                "source_count": 0, "has_injury_flag": False, "injury_status": None,
            }
        entry = grouped[key]
        entry["items"].append(item)
        entry["sources"].append(item["source"])
        entry["source_count"] = len(set(entry["sources"]))
        text = " ".join(filter(None, [
            item.get("headline", ""), item.get("body", ""), item.get("injury_status", ""),
        ])).lower()
        if any(keyword in text for keyword in INJURY_KEYWORDS):
            entry["has_injury_flag"] = True
        if item.get("injury_status"):
            entry["injury_status"] = item["injury_status"]
    return grouped


def match_to_roster(
    news_by_player: dict[str, NewsByPlayerEntry], roster_players: list[ResolvedPlayer]
) -> list[EnrichedPlayer]:
    """Return roster players enriched with their aggregated news."""
    enriched: list[EnrichedPlayer] = []
    for player in roster_players:
        news_data = news_by_player.get(normalise_name(player.get("full_name", "")))
        player_with_news: EnrichedPlayer = {**player}
        if news_data:
            player_with_news.update({
                "news_items": news_data["items"], "source_count": news_data["source_count"],
                "has_injury_flag": news_data["has_injury_flag"],
                "news_injury_status": news_data.get("injury_status"), "has_news": True,
            })
        else:
            player_with_news.update({
                "news_items": [], "source_count": 0, "has_injury_flag": False,
                "news_injury_status": None, "has_news": False,
            })
        enriched.append(player_with_news)
    return enriched


SOURCES: list[tuple[str, Scraper]] = [
    ("rotowire", scrape_rotowire),
    ("fantasypros_news", scrape_fantasypros_news),
    ("fantasypros_injuries", scrape_fantasypros_injuries),
    ("espn", scrape_espn_nfl),
    ("nfl_com", scrape_nfl_news),
    ("cbssports", scrape_cbssports_nfl),
]


def run(roster_players: Optional[list[ResolvedPlayer]] = None) -> NewsOutput:
    """Scrape all sources concurrently, aggregate them, and enrich a roster."""
    log.info("News agent starting...")
    all_items: list[NewsItem] = []
    source_status: dict[str, SourceStatus] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = [executor.submit(scraper) for _, scraper in SOURCES]
        for (label, _), future in zip(SOURCES, futures):
            items, error = future.result()
            all_items.extend(items)
            source_status[label] = {"ok": error is None, "error": error, "items": len(items)}

    news_by_player = cross_reference(all_items)
    result: NewsOutput = {
        "total_items": len(all_items), "unique_players": len(news_by_player),
        "news_by_player": news_by_player, "all_items": all_items,
        "source_status": source_status, "scraped_at": datetime.now(timezone.utc).isoformat(),
    }
    if roster_players:
        result["enriched_roster"] = match_to_roster(news_by_player, roster_players)
    log.info("News agent complete.")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [NEWS] %(message)s")
    data = run()
    print(f"News: {data['total_items']} items for {data['unique_players']} players")
