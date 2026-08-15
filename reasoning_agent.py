"""
Reasoning Agent
Calls the Anthropic API to analyse each player's news and injury status,
classify dynasty fantasy trend (UP / DOWN / WATCH), and generate
actionable recommendations.
"""

import anthropic
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

API_KEY = os.environ.get("DASHBOARD_KEY")

# Cross-league, cross-run analysis cache. A player rostered in multiple
# leagues is analysed once and reused everywhere else within the freshness
# window, instead of burning an API call per league.
#
# Matches the recommended cron cadence (every 4h, see SETUP.md) rather than
# being shorter than it — at 1h, every 4h cron tick found the whole cache
# stale and re-analysed all 175+ players from scratch every single run, with
# zero reuse between runs. If you change the cron interval, keep this in sync
# (slightly under the interval, so a run is never coincidentally right on the
# boundary).
CACHE_PATH = Path(__file__).resolve().parent / "player_analysis_cache.json"
CACHE_FRESHNESS = timedelta(hours=4)

# Beyond CACHE_FRESHNESS, a player is still re-checked against their prior
# analysis (see is_quiet_reuse_eligible) rather than re-run through the LLM,
# as long as nothing about their news/injury/roster status has changed. This
# bounds how long a "nothing changed" player can go without a real LLM call —
# past this, force a fresh analysis regardless, since the write-up itself
# (age, career-year references, etc.) can go stale even when the inputs
# didn't change.
QUIET_REUSE_MAX_AGE = timedelta(days=7)

# Players per reasoning call. Big enough to meaningfully cut the per-call
# system-prompt overhead (283 individual calls -> ~15 batched ones), small
# enough that one truncated/malformed response only costs this many players'
# analysis for a single run (they just fall back to defaults and retry next
# run) rather than the whole roster, and that max_tokens stays comfortably
# under the standard 8192 ceiling without needing beta high-output headers.
BATCH_SIZE = 20

# Prompt caching is GA (no beta header needed/accepted anymore — sending the
# old "anthropic-beta: prompt-caching-2024-07-15" header now gets a hard 400).
# cache_control below is left in as a forward-compatible no-op: verified live
# against the real API that it currently creates zero cache (cache_creation/
# read_input_tokens both stayed 0 across repeated identical calls) because
# SYSTEM_PROMPT (~820 tokens) is under Anthropic's ~1024-token minimum
# cacheable block size for Sonnet. Batching is the actual win here; caching
# would only start paying off if the system prompt grows past that floor.


def get_client() -> anthropic.Anthropic:
    if not API_KEY:
        raise EnvironmentError(
            "DASHBOARD_KEY environment variable not set. "
            "Add it to your .env file or export it in your shell."
        )
    return anthropic.Anthropic(api_key=API_KEY)


def load_analysis_cache() -> dict:
    """Load the persisted per-player analysis cache, keyed by Sleeper player_id."""
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read analysis cache, starting fresh: {e}")
        return {}


def save_analysis_cache(cache: dict) -> None:
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError as e:
        log.error(f"Failed to save analysis cache: {e}")


def is_cache_entry_fresh(entry: Optional[dict]) -> bool:
    """True if the cached entry has a reasoning result analysed within CACHE_FRESHNESS."""
    if not entry or not entry.get("last_analyzed") or not entry.get("reasoning"):
        return False
    try:
        last_analyzed = datetime.fromisoformat(entry["last_analyzed"])
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_analyzed < CACHE_FRESHNESS


def compute_signal_fingerprint(player: dict) -> dict:
    """
    A small, JSON-serialisable snapshot of everything that could change a
    player's analysis: injury status, roster flags, and the actual news
    headlines (not just a count — a headline can be swapped for a different
    one without the count changing). Two players with an identical
    fingerprint have nothing new for the model to react to.
    """
    return {
        "injury_status": player.get("injury_status") or player.get("news_injury_status"),
        "is_starter": player.get("is_starter", False),
        "is_ir": player.get("is_ir", False),
        "is_taxi": player.get("is_taxi", False),
        "news_headlines": sorted(
            item.get("headline") or "" for item in (player.get("news_items") or [])
        ),
    }


def is_quiet_reuse_eligible(entry: Optional[dict], player: dict) -> bool:
    """
    True if a stale (beyond CACHE_FRESHNESS) cache entry can be reused
    without a fresh LLM call: the player has a prior analysis, it's not so
    old that the write-up itself risks going stale (QUIET_REUSE_MAX_AGE), and
    nothing about their news/injury/roster status has changed since then.
    """
    if not entry or not entry.get("reasoning") or not entry.get("last_analyzed") or "signal" not in entry:
        return False
    try:
        last_analyzed = datetime.fromisoformat(entry["last_analyzed"])
    except ValueError:
        return False
    if last_analyzed.tzinfo is None:
        last_analyzed = last_analyzed.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) - last_analyzed >= QUIET_REUSE_MAX_AGE:
        return False
    return entry["signal"] == compute_signal_fingerprint(player)


SYSTEM_PROMPT = """You are an expert dynasty fantasy NFL analyst with deep knowledge of:
- Player injury impacts and typical recovery timelines
- Dynasty value: age curves, contract years, depth chart implications
- Positional scarcity and replacement-level players
- Trade and waiver wire strategy

Your job is to analyse a player's current news, injury status, and roster context,
then provide a structured fantasy assessment.

You must respond ONLY with valid JSON — no preamble, no markdown fences, no explanation outside the JSON.

Each individual player's assessment must follow this exact schema:
{
  "trend": "UP" | "DOWN" | "WATCH",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "summary": "One sentence: the key fact affecting this player right now.",
  "fantasy_impact": "SHORT" | "MEDIUM" | "LONG" | "NONE",
  "recommendation": "One clear action sentence (start/sit/hold/sell/buy/monitor).",
  "dynasty_note": "One sentence on long-term dynasty value implication, if relevant.",
  "contract_note": "One short phrase on the player's real-world NFL contract situation (e.g. 'Rookie deal, year 2 of 4', 'Signed extension through 2027', 'Final year, pending free agent'). If CONTRACT DATA below is marked verified, base this on those actual terms — do not contradict them. If it says no verified data is available, give your best general characterization and hedge honestly (e.g. 'Veteran, contract terms unclear') rather than inventing specific numbers or years. Always use the given DRAFT YEAR (not a guess) when describing what year of their career/rookie deal a player is in.",
  "roster_status_note": "One short phrase on this player's role/standing beyond the starter/IR/taxi flags already tracked — e.g. 'Committee back, splitting touches', 'Handcuff, no standalone value while starter healthy', 'Camp battle for WR3 role', 'Locked-in every-down starter'.",
  "flags": ["injury", "trade", "depth_chart", "breakout", "bust_risk", "target_share"]
}

If you are given a single player to analyse, respond with exactly one JSON object following that schema.

If you are given multiple players — each one a block starting with "=== PLAYER_ID: <id> ===" — respond
with a single JSON object whose top-level keys are exactly those PLAYER_IDs (as strings, one entry per
player, no player omitted, no extra keys added), and whose values each follow the schema above.

trend rules:
- UP: positive news — return from injury, increased role, target share up, healthy and producing
- DOWN: negative news — injury, demotion, suspension, poor usage, age concern active
- WATCH: unclear, conflicting reports, recovering but uncertain timeline, situation developing

flags should only include relevant tags from the allowed list.
fantasy_impact = how long the news affects their fantasy value (SHORT=1-2 weeks, MEDIUM=3-6 weeks, LONG=season+, NONE=no impact).
CONTRACT DATA, when marked verified, comes from a live Spotrac lookup — treat it as ground truth over your own knowledge.
DRAFT YEAR, when given, comes directly from the league's own player database — treat it as ground truth over your own knowledge of when a player was drafted.
roster_status_note is a best-effort characterization from your general knowledge, not a lookup against a live depth chart feed — favor honest hedging over fabricated precision.
"""


def format_contract_block(contract: Optional[dict]) -> str:
    """Render Spotrac contract data (from contract_agent.py, via the Parse Bot API) for the prompt, if any."""
    fallback = (
        "No verified contract data available for this player — give your best general "
        "characterization and hedge honestly if unsure, rather than inventing numbers or years."
    )
    if not contract or not contract.get("found"):
        return fallback

    lines = ["VERIFIED (Spotrac):"]
    span = contract.get("contract_span")
    if span:
        deal_type = contract.get("contract_type")
        lines.append(f"{deal_type + ' ' if deal_type else ''}contract, {span}")

    facts = []
    for label, key in [
        ("Current Cap Hit", "current_year_cap_hit"),
        ("Cap %", "current_year_cap_pct"),
        ("Under Contract Through", "final_year"),
    ]:
        value = contract.get(key)
        if value:
            facts.append(f"{label}: {value}")
    if facts:
        lines.append(" | ".join(facts))

    return "\n".join(lines) if len(lines) > 1 else fallback


def build_player_block(player: dict) -> str:
    """
    Build one player's analysis block, tagged with its Sleeper player_id so a
    batched response can be mapped back to the right player unambiguously.
    """
    pid = player.get("player_id")
    name = player.get("full_name", "Unknown")
    position = player.get("position", "UNK")
    team = player.get("team", "FA")
    age = player.get("age", "Unknown")
    years_exp = player.get("years_exp", 0)
    draft_year = player.get("draft_year")
    season = player.get("season")
    injury_status = player.get("injury_status") or player.get("news_injury_status") or "None reported"
    injury_body = player.get("injury_body_part", "")
    is_starter = player.get("is_starter", False)
    is_ir = player.get("is_ir", False)
    is_taxi = player.get("is_taxi", False)

    # Compile news text
    news_texts = []
    for item in (player.get("news_items") or [])[:5]:  # cap at 5 items
        parts = filter(None, [
            item.get("headline"),
            item.get("body"),
            item.get("analysis"),
        ])
        text = " | ".join(parts)
        if text:
            news_texts.append(f"[{item.get('source', 'unknown')}] {text}")

    news_block = "\n".join(news_texts) if news_texts else "No recent news found."

    roster_context = []
    if is_starter:
        roster_context.append("Currently in starting lineup")
    if is_ir:
        roster_context.append("On IR")
    if is_taxi:
        roster_context.append("On taxi squad")

    if draft_year:
        draft_line = f"DRAFT YEAR: {draft_year}" + (f" (current season: {season})" if season else "")
    else:
        draft_line = "DRAFT YEAR: Unknown — do not guess a specific year, describe experience level generally instead."

    return f"""=== PLAYER_ID: {pid} ===
NAME: {name}
POSITION: {position}
TEAM: {team}
AGE: {age}
YEARS EXPERIENCE: {years_exp}
{draft_line}
INJURY STATUS: {injury_status}{f' ({injury_body})' if injury_body else ''}
ROSTER STATUS: {', '.join(roster_context) if roster_context else 'Active roster'}

CONTRACT DATA:
{format_contract_block(player.get("contract"))}

RECENT NEWS ({player.get('source_count', 0)} source(s)):
{news_block}"""


def build_player_prompt(player: dict) -> str:
    """Build a full single-player analysis prompt (used for one-off/manual analysis)."""
    return f"""Analyse this dynasty fantasy NFL player:

{build_player_block(player)}

Provide your structured JSON assessment."""


def build_batch_prompt(players: list[dict]) -> str:
    """Build one prompt analysing multiple players in a single call, each tagged by PLAYER_ID."""
    ids = ", ".join(str(p.get("player_id")) for p in players)
    blocks = "\n\n".join(build_player_block(p) for p in players)
    return f"""Analyse each of the following {len(players)} dynasty fantasy NFL players.

Respond with a single JSON object whose top-level keys are exactly these PLAYER_IDs, each appearing
exactly once: {ids}

{blocks}

Provide your structured JSON assessment for every player above, keyed by PLAYER_ID."""


def cached_system_block() -> list[dict]:
    """System prompt wrapped for Anthropic prompt caching — identical content across every
    call (single or batched) so the cache actually hits."""
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def analyse_player(client: anthropic.Anthropic, player: dict, retries: int = 2) -> Optional[dict]:
    """
    Call the Anthropic API to analyse a single player.
    Returns parsed JSON result or None on failure.
    """
    prompt = build_player_prompt(player)

    for attempt in range(retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1000,
                system=cached_system_block(),
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()

            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            result = json.loads(raw)
            return result

        except json.JSONDecodeError as e:
            log.warning(f"JSON parse error for {player.get('full_name')}: {e}")
            if attempt < retries:
                time.sleep(2)
        except anthropic.RateLimitError:
            log.warning("Rate limited — waiting 10s")
            time.sleep(10)
        except anthropic.APIError as e:
            log.error(f"API error for {player.get('full_name')}: {e}")
            if attempt < retries:
                time.sleep(3)
        except Exception as e:
            log.error(f"Unexpected error for {player.get('full_name')}: {e}")
            break

    return None


def analyse_players_batch(client: anthropic.Anthropic, players: list[dict], retries: int = 2) -> dict[str, dict]:
    """
    Call the Anthropic API once to analyse a whole batch of players, returning
    a dict of player_id (str) -> parsed reasoning dict.

    A player_id missing from the response — because the model dropped it, or
    the whole batch failed to parse even after retries — is simply absent
    from the returned dict rather than raising. Callers already treat a
    missing/falsy reasoning as "insufficient data" and retry that player on
    the next run, so a bad batch only costs this one batch's players for one
    run instead of the whole roster.
    """
    if not players:
        return {}

    prompt = build_batch_prompt(players)
    expected_ids = {str(p.get("player_id")) for p in players}
    # Generous per-player output budget, capped at the standard (non-beta) ceiling.
    max_tokens = min(8192, 350 * len(players) + 500)

    for attempt in range(retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=cached_system_block(),
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError(f"expected a JSON object keyed by player_id, got {type(parsed).__name__}")

            results = {pid: value for pid, value in parsed.items() if pid in expected_ids}
            missing = expected_ids - results.keys()
            if missing:
                log.warning(f"Batch response missing {len(missing)}/{len(players)} player_id(s): {sorted(missing)}")
            return results

        except (json.JSONDecodeError, ValueError) as e:
            log.warning(f"Batch JSON parse error ({len(players)} players): {e}")
            if attempt < retries:
                time.sleep(2)
        except anthropic.RateLimitError:
            log.warning("Rate limited — waiting 10s")
            time.sleep(10)
        except anthropic.APIError as e:
            log.error(f"API error analysing batch of {len(players)} players: {e}")
            if attempt < retries:
                time.sleep(3)
        except Exception as e:
            log.error(f"Unexpected error analysing batch of {len(players)} players: {e}")
            break

    return {}


def generate_league_summary(client: anthropic.Anthropic, league_name: str, analysed_players: list[dict]) -> str:
    """
    Generate a short executive summary for the league dashboard header.

    Runs on Haiku, not Sonnet — this is pure templating (2-3 sentences of
    prose from counts and names already computed below), not a fantasy
    judgment call, so the cheaper model costs nothing in quality.
    """
    up = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "UP"]
    down = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "DOWN"]
    watch = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "WATCH"]

    injured = [p for p in analysed_players if p.get("has_injury_flag")]

    prompt = f"""Dynasty fantasy NFL league summary for: {league_name}

Roster snapshot:
- {len(analysed_players)} total players
- {len(up)} trending UP, {len(down)} trending DOWN, {len(watch)} on WATCH
- {len(injured)} players with injury flags

Players trending UP: {', '.join(p['full_name'] for p in up[:5])}
Players trending DOWN: {', '.join(p['full_name'] for p in down[:5])}
Key injury concerns: {', '.join(p['full_name'] for p in injured[:5])}

Write a 2-3 sentence executive summary a dynasty manager would want to read first thing.
Be specific, direct, and use the data above. No generic filler. Respond with plain text only."""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        log.error(f"Failed to generate league summary: {e}")
        return f"{league_name}: {len(up)} trending up, {len(down)} trending down, {len(injured)} injury concerns."


def run(sleeper_data: dict, news_data: dict) -> dict:
    """
    Main entry point for the Reasoning agent.
    Takes Sleeper and News agent outputs, returns enriched data with AI analysis.
    """
    log.info("Reasoning agent starting...")
    client = get_client()

    analysis_cache = load_analysis_cache()
    cache_hits = 0
    cache_misses = 0

    result = {
        "username": sleeper_data.get("username"),
        "season": sleeper_data.get("season"),
        "leagues": [],
        "global_trends": {
            "trending_up": [],
            "trending_down": [],
            "watch_list": [],
        }
    }

    news_by_player = news_data.get("news_by_player", {})

    # Pass 1: enrich every league's roster with news, and build one deduped
    # (by player_id) roster across all leagues — a player on multiple rosters
    # only needs to be analysed once. The first league to see a given pid
    # supplies the snapshot (roster flags etc.) used for its analysis, same
    # as the old per-run cache dedup behaviour.
    from news_agent import match_to_roster, normalise_name

    league_players: dict[str, list[dict]] = {}
    unique_players: dict[str, dict] = {}

    for league in sleeper_data.get("leagues", []):
        players = league.get("players", [])
        enriched_players = match_to_roster(news_by_player, players)
        for player in enriched_players:
            player["season"] = league.get("season")
        league_players[league["league_id"]] = enriched_players
        for player in enriched_players:
            pid = str(player.get("player_id"))
            unique_players.setdefault(pid, player)

    # Split into three buckets: fresh cache hits (no work), stale-but-quiet
    # entries (nothing about the player changed, so the old write-up is
    # reused as-is instead of burning an LLM call), and real misses that need
    # a fresh analysis.
    to_analyse = []
    quiet_reuses = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for pid, player in unique_players.items():
        cached_entry = analysis_cache.get(pid)
        if is_cache_entry_fresh(cached_entry):
            continue
        if is_quiet_reuse_eligible(cached_entry, player):
            cached_entry["last_analyzed"] = now_iso
            quiet_reuses += 1
            continue
        to_analyse.append(player)

    cache_hits = len(unique_players) - len(to_analyse) - quiet_reuses

    # Pass 1b: batch-analyse the real cache misses, BATCH_SIZE players per call.
    for batch_start in range(0, len(to_analyse), BATCH_SIZE):
        batch = to_analyse[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(to_analyse) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info(f"Analysing batch {batch_num}/{total_batches} ({len(batch)} players)...")

        batch_results = analyse_players_batch(client, batch)
        now = datetime.now(timezone.utc).isoformat()
        for player in batch:
            pid = str(player.get("player_id"))
            reasoning = batch_results.get(pid)
            if reasoning:
                analysis_cache[pid] = {
                    "full_name": player.get("full_name", "Unknown"),
                    "reasoning": reasoning,
                    "last_analyzed": now,
                    "signal": compute_signal_fingerprint(player),
                }
                cache_misses += 1
            else:
                log.warning(f"  No result for {player.get('full_name')} (pid={pid}) — will retry next run")

        # Save incrementally after each batch, not just at the end — a crash
        # partway through a long run shouldn't throw away batches already done.
        save_analysis_cache(analysis_cache)
        time.sleep(0.5)

    # Pass 2: assemble each league's output from the now fully-populated cache.
    for league in sleeper_data.get("leagues", []):
        league_name = league["league_name"]
        log.info(f"Assembling league: {league_name}")

        enriched_players = league_players[league["league_id"]]

        analysed = []
        for player in enriched_players:
            pid = str(player.get("player_id"))
            cached_entry = analysis_cache.get(pid)
            reasoning = (cached_entry or {}).get("reasoning")

            player_result = dict(player)
            player_result["reasoning"] = reasoning or {
                "trend": "WATCH",
                "confidence": "LOW",
                "summary": "Insufficient data to analyse.",
                "fantasy_impact": "NONE",
                "recommendation": "Monitor for updates.",
                "dynasty_note": None,
                "contract_note": None,
                "roster_status_note": None,
                "flags": [],
            }
            player_result["last_analyzed"] = (cached_entry or {}).get("last_analyzed")

            analysed.append(player_result)

        # Generate league summary
        summary = generate_league_summary(client, league_name, analysed)

        # Sort into trend buckets
        trending_up = sorted(
            [p for p in analysed if p["reasoning"]["trend"] == "UP"],
            key=lambda p: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(p["reasoning"]["confidence"], 2)
        )
        trending_down = sorted(
            [p for p in analysed if p["reasoning"]["trend"] == "DOWN"],
            key=lambda p: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(p["reasoning"]["confidence"], 2)
        )
        watch_list = [p for p in analysed if p["reasoning"]["trend"] == "WATCH"]

        league_result = {
            "league_id": league["league_id"],
            "league_name": league_name,
            "season": league["season"],
            "summary": summary,
            "players": analysed,
            "trending_up": trending_up,
            "trending_down": trending_down,
            "watch_list": watch_list,
            "stats": {
                "total": len(analysed),
                "trending_up": len(trending_up),
                "trending_down": len(trending_down),
                "watch": len(watch_list),
                "injured": sum(1 for p in analysed if p.get("has_injury_flag")),
            }
        }

        result["leagues"].append(league_result)

        # Merge into global trends (deduped by player name)
        seen_up = {p["full_name"] for p in result["global_trends"]["trending_up"]}
        seen_down = {p["full_name"] for p in result["global_trends"]["trending_down"]}
        seen_watch = {p["full_name"] for p in result["global_trends"]["watch_list"]}

        for p in trending_up:
            if p["full_name"] not in seen_up:
                result["global_trends"]["trending_up"].append(p)
                seen_up.add(p["full_name"])
        for p in trending_down:
            if p["full_name"] not in seen_down:
                result["global_trends"]["trending_down"].append(p)
                seen_down.add(p["full_name"])
        for p in watch_list:
            if p["full_name"] not in seen_watch:
                result["global_trends"]["watch_list"].append(p)
                seen_watch.add(p["full_name"])

    save_analysis_cache(analysis_cache)
    log.info(
        f"Reasoning agent complete. {cache_hits} cached (fresh), "
        f"{quiet_reuses} reused (no new signal), {cache_misses} freshly analysed."
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [REASONING] %(message)s")
    # Test with mock data
    mock_player = {
        "full_name": "Jaylen Waddle",
        "position": "WR",
        "team": "MIA",
        "age": 25,
        "years_exp": 3,
        "injury_status": "Questionable",
        "injury_body_part": "knee",
        "is_starter": True,
        "is_ir": False,
        "is_taxi": False,
        "news_items": [
            {
                "source": "rotowire",
                "headline": "Waddle listed as questionable with knee injury",
                "body": "Waddle was limited in practice Wednesday and Thursday with a knee injury.",
                "analysis": "His status for Sunday is uncertain. Monitor practice reports Friday.",
            }
        ],
        "source_count": 1,
        "has_injury_flag": True,
    }

    client = get_client()
    result = analyse_player(client, mock_player)
    print(json.dumps(result, indent=2))
