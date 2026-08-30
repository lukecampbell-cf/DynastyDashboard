"""
News Agent
Scrapes NFL injury reports, depth chart changes, and trade news from:
- Rotowire
- FantasyPros news + injuries (via the Parse Bot API)
- ESPN NFL news (via the Parse Bot API)
- NFL.com news
- CBS Sports NFL news
Cross-references sources and returns structured news per player.
"""

import concurrent.futures
import httpx
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from typing import Optional

from .common import USER_AGENT, normalise_name, parsebot_headers
from .schemas import EnrichedPlayer, NewsByPlayerEntry, NewsItem, NewsOutput, ResolvedPlayer, SourceStatus

log = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

# ESPN's own site returns an empty 202 "bot challenge" response to scraping,
# which is why its news feed goes through Parse Bot instead of HTML scraping
# (https://parse.bot/scrapers/1682503b-990a-4f2a-b44a-c95c30c1d08f). Auth is
# a Parse Bot API key (PARSE_BOT_API in .env), shared by every Parse Bot call
# in this file — see common.parsebot_headers().

ESPN_SCRAPER_ID = "1682503b-990a-4f2a-b44a-c95c30c1d08f"
ESPN_BASE_URL = f"https://api.parse.bot/scraper/{ESPN_SCRAPER_ID}"

# FantasyPros' own /nfl/news/ page's markup had drifted from the selectors
# below (they were silently returning zero items — see get_player_news);
# this scraper (https://parse.bot/scrapers/6f2ca995-75cd-4151-ae78-ea03c18f8012)
# also exposes a week-keyed injuries endpoint that the old HTML scrape never covered.
FANTASYPROS_SCRAPER_ID = "6f2ca995-75cd-4151-ae78-ea03c18f8012"
FANTASYPROS_BASE_URL = f"https://api.parse.bot/scraper/{FANTASYPROS_SCRAPER_ID}"

# nfl.com API (https://parse.bot/marketplace/f5dc4749-3bbe-416c-be21-c1b189dd6f00/nfl-com-api),
# called via its canonical scraper id for get_current_week only — used to
# resolve the current NFL week/season for the FantasyPros injuries call
# above, which is keyed by week number rather than a date.
NFL_API_SCRAPER_ID = "6305bd0d-6e22-47aa-80cb-ce184496e63e"
NFL_API_BASE_URL = f"https://api.parse.bot/scraper/{NFL_API_SCRAPER_ID}"

# FantasyPros news titles read like "Jared Bartlett waived by Jaguars" — a
# capitalised name run followed by a lowercase verb, with no delimiter Parse
# Bot exposes separately. This captures the name run reliably because the
# site's transaction-wire headlines are grammatically consistent.
FANTASYPROS_NAME_RE = re.compile(r"^([A-Z][A-Za-z'.-]*(?:\s+[A-Z][A-Za-z'.-]*)*)\s+[a-z]")


def fetch_html(url: str, retries: int = 2, headers: Optional[dict] = None) -> Optional[str]:
    """Fetch HTML with retry logic and polite delay."""
    for attempt in range(retries + 1):
        try:
            time.sleep(1.5)  # polite crawl delay
            r = httpx.get(url, headers=headers or HEADERS, timeout=15, follow_redirects=True)
            r.raise_for_status()
            return r.text
        except httpx.HTTPStatusError as e:
            log.warning(f"HTTP {e.response.status_code} for {url} (attempt {attempt + 1})")
        except Exception as e:
            log.warning(f"Fetch failed for {url}: {e} (attempt {attempt + 1})")
        if attempt < retries:
            time.sleep(3)
    return None


def scrape_rotowire() -> tuple[list[NewsItem], Optional[str]]:
    """
    Scrape Rotowire NFL news feed.
    Returns (news items, error) — error is None on success (even with 0 items
    found, since that just means nothing new today), or a short reason the
    fetch itself failed, for the per-source health check in orchestrator.py.
    """
    url = "https://www.rotowire.com/football/news.php"
    log.info(f"Scraping Rotowire: {url}")
    html = fetch_html(url)
    if not html:
        log.warning("Rotowire scrape failed")
        return [], "HTML fetch failed"

    soup = BeautifulSoup(html, "html.parser")
    news_items: list[NewsItem] = []

    # Rotowire news cards
    cards = soup.select(".news-update") or soup.select("[class*='news']")

    for card in cards[:50]:  # cap at 50 most recent
        try:
            # Player name — the wildcard fallback below used to match the
            # wrapping ".news-update__playerhead" container, which includes
            # the headline link's text too (e.g. "Josh DownsNursing groin
            # injury"), so it never matched any real roster player.
            player_el = card.select_one(".news-update__player-link, .player-name")
            player_name = player_el.get_text(strip=True) if player_el else None

            # Team
            team_el = card.select_one(".news-update__team, [class*='team']")
            team = team_el.get_text(strip=True) if team_el else None

            # Headline / impact
            headline_el = card.select_one(".news-update__headline, [class*='headline']")
            headline = headline_el.get_text(strip=True) if headline_el else None

            # Body text
            body_el = card.select_one(".news-update__news, [class*='news-text'], p")
            body = body_el.get_text(strip=True) if body_el else None

            # Analysis
            analysis_el = card.select_one(".news-update__analysis, [class*='analysis']")
            analysis = analysis_el.get_text(strip=True) if analysis_el else None

            if player_name and (headline or body):
                news_items.append({
                    "source": "rotowire",
                    "player_name": player_name,
                    "team": team,
                    "headline": headline,
                    "body": body,
                    "analysis": analysis,
                    "url": url,
                    "scraped_at": datetime.now(timezone.utc).isoformat(),
                })
        except Exception as e:
            log.debug(f"Error parsing Rotowire card: {e}")
            continue

    log.info(f"Rotowire: {len(news_items)} items found")
    return news_items, None


def scrape_fantasypros_news() -> tuple[list[NewsItem], Optional[str]]:
    """
    Fetch FantasyPros' latest player news via the Parse Bot API
    (get_player_news), replacing the old direct HTML scrape of
    fantasypros.com/nfl/news/ — the site's markup had drifted from those
    selectors and it was silently returning zero items.

    Headlines here read like "Jared Bartlett waived by Jaguars" with no
    delimiter, so player names are pulled with FANTASYPROS_NAME_RE (the
    leading capitalised-name run) rather than the ":" split the other
    general-news scrapers below use — that split never matches this site's
    transaction-wire phrasing.

    Returns (news items, error) — see scrape_rotowire() for the convention.
    """
    log.info("Fetching FantasyPros player news via Parse Bot")
    try:
        r = httpx.get(f"{FANTASYPROS_BASE_URL}/get_player_news", headers=parsebot_headers(), timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"FantasyPros Parse Bot fetch failed: {e}")
        return [], f"Parse Bot fetch failed: {e}"

    if payload.get("status") != "success":
        log.warning(f"FantasyPros Parse Bot returned non-success status: {payload}")
        return [], "Parse Bot returned non-success status"

    news_items: list[NewsItem] = []
    for item in payload.get("data", {}).get("news", []):
        title = item.get("title")
        if not title:
            continue

        m = FANTASYPROS_NAME_RE.match(title)
        player_name = m.group(1) if m else None

        news_items.append({
            "source": "fantasypros",
            "player_name": player_name,
            "team": None,
            "headline": title,
            "body": item.get("content"),
            "analysis": item.get("impact"),
            "url": item.get("url"),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    log.info(f"FantasyPros (Parse Bot): {len(news_items)} items found")
    return news_items, None


def get_current_nfl_week() -> Optional[dict]:
    """
    Get the current NFL week/season from the nfl.com API on Parse Bot
    (https://parse.bot/marketplace/f5dc4749-3bbe-416c-be21-c1b189dd6f00/nfl-com-api).
    Used to pick the right week/year for FantasyPros' injuries endpoint,
    which is keyed by week number rather than a live page scrape.
    """
    try:
        r = httpx.get(f"{NFL_API_BASE_URL}/get_current_week", headers=parsebot_headers(), timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"Failed to fetch current NFL week: {e}")
        return None

    if payload.get("status") != "success":
        log.warning(f"get_current_week returned non-success status: {payload}")
        return None

    return payload.get("data")


def scrape_fantasypros_injuries() -> tuple[list[NewsItem], Optional[str]]:
    """
    Fetch structured NFL injury designations from FantasyPros via Parse Bot
    (get_injuries), keyed by NFL week/season rather than scraped live off a
    page. The week/season come from get_current_nfl_week() above.

    FantasyPros' own off-season default (week="draft") returns only a
    handful of stale "retired" entries even mid-preseason — tested directly
    against the live API — so outside the regular season this requests
    week 1 instead: PUP/IR designations set during camp/preseason carry
    forward into it, which is what's actually current right now.

    Returns (news items, error) — see scrape_rotowire() for the convention.
    """
    current = get_current_nfl_week()
    if not current or not current.get("season"):
        return [], "could not resolve current NFL week (nfl.com API)"

    season = current["season"]
    week, season_type = current.get("week"), current.get("seasonType")
    fp_week = str(week) if season_type == "REG" and week else "1"

    log.info(f"Fetching FantasyPros injuries: week={fp_week} season={season} (current NFL: {season_type} week {week})")
    try:
        r = httpx.get(
            f"{FANTASYPROS_BASE_URL}/get_injuries",
            headers=parsebot_headers(),
            params={"week": fp_week, "year": str(season)},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"FantasyPros injuries fetch failed: {e}")
        return [], f"Parse Bot fetch failed: {e}"

    if payload.get("status") != "success":
        log.warning(f"FantasyPros injuries returned non-success status: {payload}")
        return [], "Parse Bot returned non-success status"

    news_items: list[NewsItem] = []
    for item in payload.get("data", {}).get("injuries", []):
        name = item.get("name")
        if not name:
            continue

        status = item.get("status")
        injury_type = item.get("injury_type") or None
        headline = f"{status}: {injury_type}" if status and injury_type else status

        news_items.append({
            "source": "fantasypros_injuries",
            "player_name": name,
            "team": item.get("team_id"),
            "headline": headline,
            "body": item.get("comment") or None,
            "analysis": None,
            "injury_status": status,
            "injury_type": injury_type,
            "url": None,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        })

    log.info(f"FantasyPros injuries: {len(news_items)} items found")
    return news_items, None


def scrape_espn_nfl(limit: int = 50) -> tuple[list[NewsItem], Optional[str]]:
    """
    Fetch ESPN's NFL news feed via the Parse Bot API, which does ESPN's own
    scraping for us and returns structured JSON (this replaced two HTML
    scrapers — a general news feed and a separate injuries table — since
    ESPN's site blocks scraping with an empty 202 "bot challenge" response).

    Each article Parse Bot returns is tagged with the players and teams it's
    about via its "categories" list, rather than needing to be guessed from
    the headline the way the other general-news sources here still are. An
    article naming several players (e.g. a trade or camp roundup) is emitted
    as one news item per player so cross-referencing downstream treats it
    the same as a single-player story.

    Returns (news items, error) — see scrape_rotowire() for the convention.
    """
    log.info("Fetching ESPN NFL news via Parse Bot")
    try:
        r = httpx.post(
            f"{ESPN_BASE_URL}/get_news",
            headers=parsebot_headers(),
            json={"league": "nfl", "limit": limit},
            timeout=20,
        )
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        log.warning(f"ESPN Parse Bot fetch failed: {e}")
        return [], f"Parse Bot fetch failed: {e}"

    if payload.get("status") != "success":
        log.warning(f"ESPN Parse Bot returned non-success status: {payload}")
        return [], "Parse Bot returned non-success status"

    articles = payload.get("data", {}).get("articles", [])
    news_items: list[NewsItem] = []

    for article in articles:
        headline = article.get("headline")
        if not headline:
            continue

        categories = article.get("categories") or []
        athletes = [c["description"] for c in categories if c.get("type") == "athlete" and c.get("description")]
        teams = [c["description"] for c in categories if c.get("type") == "team" and c.get("description")]

        base_item: NewsItem = {
            "source": "espn",
            "team": teams[0] if teams else None,
            "headline": headline,
            "body": article.get("description"),
            "analysis": None,
            "url": article.get("link"),
            "published_at": article.get("published"),
            "scraped_at": datetime.now(timezone.utc).isoformat(),
        }

        for player_name in (athletes or [None]):
            news_items.append({**base_item, "player_name": player_name})

    log.info(f"ESPN (Parse Bot): {len(news_items)} items found ({len(articles)} articles)")
    return news_items, None


def scrape_nfl_news() -> tuple[list[NewsItem], Optional[str]]:
    """
    Scrape NFL.com news feed.
    Returns (news items, error) — see scrape_rotowire() for the convention.
    """
    url = "https://www.nfl.com/news/"
    log.info(f"Scraping NFL.com: {url}")
    html = fetch_html(url)
    if not html:
        log.warning("NFL.com scrape failed")
        return [], "HTML fetch failed"

    soup = BeautifulSoup(html, "html.parser")
    news_items: list[NewsItem] = []
    seen_links = set()

    cards = soup.select("a[href*='/news/']")

    for card in cards[:50]:
        try:
            link = card.get("href")
            if not link or link in seen_links:
                continue

            headline_el = card.select_one("h3, h2")
            headline = headline_el.get_text(strip=True) if headline_el else None

            body_el = card.select_one("p")
            body = body_el.get_text(strip=True) if body_el else None

            if not headline:
                continue
            seen_links.add(link)

            # Headlines are often formatted "Player: update" - best-effort extraction.
            player_name = headline.split(":", 1)[0].strip() if ":" in headline else None

            news_items.append({
                "source": "nfl_com",
                "player_name": player_name,
                "team": None,
                "headline": headline,
                "body": body,
                "analysis": None,
                "url": link,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            log.debug(f"Error parsing NFL.com card: {e}")
            continue

    log.info(f"NFL.com: {len(news_items)} items found")
    return news_items, None


def scrape_cbssports_nfl() -> tuple[list[NewsItem], Optional[str]]:
    """
    Scrape CBS Sports NFL news feed.
    Returns (news items, error) — see scrape_rotowire() for the convention.
    """
    url = "https://www.cbssports.com/nfl/"
    log.info(f"Scraping CBS Sports NFL: {url}")
    html = fetch_html(url)
    if not html:
        log.warning("CBS Sports NFL scrape failed")
        return [], "HTML fetch failed"

    soup = BeautifulSoup(html, "html.parser")
    news_items: list[NewsItem] = []
    seen_links = set()

    cards = soup.select("a[href*='/nfl/news/']")

    for card in cards[:50]:
        try:
            link = card.get("href")
            headline = card.get_text(strip=True)
            if not link or not headline or len(headline) < 10 or link in seen_links:
                continue
            seen_links.add(link)

            full_link = urljoin(url, link)

            # Headlines are often formatted "Player: update" - best-effort extraction.
            player_name = headline.split(":", 1)[0].strip() if ":" in headline else None

            news_items.append({
                "source": "cbssports",
                "player_name": player_name,
                "team": None,
                "headline": headline,
                "body": None,
                "analysis": None,
                "url": full_link,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            log.debug(f"Error parsing CBS Sports NFL card: {e}")
            continue

    log.info(f"CBS Sports NFL: {len(news_items)} items found")
    return news_items, None


# Case-insensitive substring markers for "this news item is about an injury"
# — used by cross_reference() below to set has_injury_flag, and reused by
# signal_evidence.py to decide which news items count as injury evidence for
# a player (source corroboration, freshness) without re-deriving the list.
INJURY_KEYWORDS = [
    "injured", "injury", "out", "questionable", "doubtful", "ir",
    "placed on", "limited", "did not practice", "dnp", "hamstring",
    "knee", "ankle", "shoulder", "concussion", "surgery",
]


def cross_reference(all_items: list[NewsItem]) -> dict[str, NewsByPlayerEntry]:
    """
    Group news items by player name across sources.
    Returns dict keyed by normalised player name with merged news.
    """
    grouped: dict[str, NewsByPlayerEntry] = {}

    for item in all_items:
        raw_name = item.get("player_name") or ""
        key = normalise_name(raw_name)
        if not key:
            continue

        if key not in grouped:
            grouped[key] = {
                "player_name": raw_name,
                "sources": [],
                "items": [],
                "source_count": 0,
                "has_injury_flag": False,
                "injury_status": None,
            }

        grouped[key]["items"].append(item)
        grouped[key]["sources"].append(item["source"])
        grouped[key]["source_count"] = len(set(grouped[key]["sources"]))

        # Flag injury-related items
        text = " ".join(filter(None, [
            item.get("headline", ""),
            item.get("body", ""),
            item.get("injury_status", ""),
        ])).lower()

        if any(kw in text for kw in INJURY_KEYWORDS):
            grouped[key]["has_injury_flag"] = True

        # Capture highest-confidence injury status
        if item.get("injury_status"):
            grouped[key]["injury_status"] = item["injury_status"]

    return grouped


def match_to_roster(
    news_by_player: dict[str, NewsByPlayerEntry], roster_players: list[ResolvedPlayer]
) -> list[EnrichedPlayer]:
    """
    Match scraped news items to players on the Sleeper roster.
    Returns roster players enriched with news data.
    """
    enriched: list[EnrichedPlayer] = []

    for player in roster_players:
        norm_name = normalise_name(player.get("full_name", ""))
        news_data = news_by_player.get(norm_name)

        player_with_news: EnrichedPlayer = {**player}

        if news_data:
            player_with_news["news_items"] = news_data["items"]
            player_with_news["source_count"] = news_data["source_count"]
            player_with_news["has_injury_flag"] = news_data["has_injury_flag"]
            player_with_news["news_injury_status"] = news_data.get("injury_status")
            player_with_news["has_news"] = True
        else:
            player_with_news["news_items"] = []
            player_with_news["source_count"] = 0
            player_with_news["has_injury_flag"] = False
            player_with_news["news_injury_status"] = None
            player_with_news["has_news"] = False

        enriched.append(player_with_news)

    return enriched


# (source label, zero-arg scraper) pairs run() fans out to concurrently.
# scrape_espn_nfl takes an optional `limit`, wrapped in a lambda here purely
# so every entry has the same zero-arg call signature.
SOURCES = [
    ("rotowire", scrape_rotowire),
    ("fantasypros_news", scrape_fantasypros_news),
    ("fantasypros_injuries", scrape_fantasypros_injuries),
    ("espn", lambda: scrape_espn_nfl()),
    ("nfl_com", scrape_nfl_news),
    ("cbssports", scrape_cbssports_nfl),
]


def run(roster_players: Optional[list[ResolvedPlayer]] = None) -> NewsOutput:
    """
    Main entry point for the News agent.
    Scrapes all sources, cross-references, and optionally matches to roster.
    """
    log.info("News agent starting...")

    all_items: list[NewsItem] = []
    source_status: dict[str, SourceStatus] = {}

    # Six independent third-party sources — different domains/APIs, no
    # shared rate limit between them (each scraper still has its own
    # internal retry/backoff and "polite crawl delay" for its own site, see
    # fetch_html) — so they run concurrently instead of one after another.
    # futures are submitted and read back in SOURCES' own order (not
    # completion order) purely so source_status's key order stays stable
    # and readable in logs/JSON; the actual scraping itself is concurrent.
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(SOURCES)) as executor:
        futures = [executor.submit(scraper) for _, scraper in SOURCES]
        for (label, _), future in zip(SOURCES, futures):
            items, error = future.result()
            all_items.extend(items)
            source_status[label] = {"ok": error is None, "error": error, "items": len(items)}

    log.info(f"Total raw news items: {len(all_items)}")

    # Cross-reference across sources
    news_by_player = cross_reference(all_items)
    log.info(f"Unique players in news: {len(news_by_player)}")

    result: NewsOutput = {
        "total_items": len(all_items),
        "unique_players": len(news_by_player),
        "news_by_player": news_by_player,
        "all_items": all_items,
        "source_status": source_status,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
    }

    # If roster provided, enrich it with news
    if roster_players:
        result["enriched_roster"] = match_to_roster(news_by_player, roster_players)

    log.info("News agent complete.")
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [NEWS] %(message)s")
    data = run()
    # Print summary
    print("\nNews agent summary:")
    print(f"  Total items: {data['total_items']}")
    print(f"  Unique players: {data['unique_players']}")
    print(f"  Players with injury flags: {sum(1 for p in data['news_by_player'].values() if p['has_injury_flag'])}")
