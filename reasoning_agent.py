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

from anthropic.types import TextBlockParam

from schemas import (
    AnalysedPlayer,
    AnalysisCacheEntry,
    ContractInfo,
    EnrichedPlayer,
    LeagueResult,
    NewsOutput,
    ReasoningOutput,
    ReasoningResult,
    SleeperOutput,
    SummaryCacheEntry,
)

log = logging.getLogger(__name__)

SONNET_MODEL = os.environ.get("REASONING_SONNET_MODEL", "claude-sonnet-4-6")
HAIKU_MODEL = os.environ.get("REASONING_HAIKU_MODEL", "claude-haiku-4-5")

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

# Per-league executive summary cache, keyed by league_id. Regenerated only
# when compute_summary_fingerprint() changes — the summary is templated
# prose over counts/names already computed from the (already-cached) player
# analyses, so re-running it against an unchanged fingerprint would just pay
# Haiku to restate the same sentence.
SUMMARY_CACHE_PATH = Path(__file__).resolve().parent / "league_summary_cache.json"

# Beyond CACHE_FRESHNESS, a player is still re-checked against their prior
# analysis (see is_quiet_reuse_eligible) rather than re-run through the LLM,
# as long as nothing about their news/injury/roster status has changed. This
# bounds how long a "nothing changed" player can go without a real LLM call —
# past this, force a fresh analysis regardless, since the write-up itself
# (age, career-year references, etc.) can go stale even when the inputs
# didn't change.
QUIET_REUSE_MAX_AGE = timedelta(days=7)

# If the Anthropic API is down for a whole run, every batch fails only after
# burning its own full retry budget (see analyse_players_batch's `retries`),
# batch after batch, for the same result. This caps how many consecutive
# batches analyse_in_batches will let come back completely empty (every
# player in the batch missing — the signature of an API-wide outage, not a
# few players being individually hard to analyse) before it bails on the
# remaining batches for that call. Unanalysed players are simply absent from
# the cache, same as any other miss, and get retried on the next run.
MAX_CONSECUTIVE_BATCH_FAILURES = 3

# Players per reasoning call. Big enough to meaningfully cut the per-call
# system-prompt overhead (283 individual calls -> ~15 batched ones), small
# enough that one truncated/malformed response only costs this many players'
# analysis for a single run (they just fall back to defaults and retry next
# run) rather than the whole roster, and that max_tokens stays comfortably
# under the standard 8192 ceiling without needing beta high-output headers.
BATCH_SIZE = 20

# Prompt caching is GA (no beta header needed/accepted anymore — sending the
# old "anthropic-beta: prompt-caching-2024-07-15" header now gets a hard 400).
# cache_control below actually activates for Sonnet calls: Sonnet's minimum
# cacheable block is 1,024 tokens, and SYSTEM_PROMPT (see the confidence/flag/
# aging-curve sections added below) now clears that. Within a run, batches
# fire back-to-back well inside the 5-minute cache TTL, so only the first
# Sonnet batch pays full price — the rest read the cached system prompt at a
# steep discount. Haiku's minimum is 4,096 tokens (4x Sonnet's), far past what
# a genuinely useful system prompt would need, so Haiku batches never hit
# their cache — cache_control is a harmless no-op for them, not a cost.


def get_client() -> anthropic.Anthropic:
    api_key = os.environ.get("DASHBOARD_KEY")
    if not api_key:
        raise EnvironmentError(
            "DASHBOARD_KEY environment variable not set. "
            "Add it to your .env file or export it in your shell."
        )
    return anthropic.Anthropic(api_key=api_key)


def load_analysis_cache() -> dict[str, AnalysisCacheEntry]:
    """Load the persisted per-player analysis cache, keyed by Sleeper player_id."""
    if not CACHE_PATH.exists():
        return {}
    try:
        with open(CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read analysis cache, starting fresh: {e}")
        return {}


def save_analysis_cache(cache: dict[str, AnalysisCacheEntry]) -> None:
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError as e:
        log.error(f"Failed to save analysis cache: {e}")


def load_summary_cache() -> dict[str, SummaryCacheEntry]:
    """Load the persisted per-league summary cache, keyed by league_id."""
    if not SUMMARY_CACHE_PATH.exists():
        return {}
    try:
        with open(SUMMARY_CACHE_PATH) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"Could not read summary cache, starting fresh: {e}")
        return {}


def save_summary_cache(cache: dict[str, SummaryCacheEntry]) -> None:
    try:
        with open(SUMMARY_CACHE_PATH, "w") as f:
            json.dump(cache, f, indent=2, sort_keys=True)
    except OSError as e:
        log.error(f"Failed to save summary cache: {e}")


def is_cache_entry_fresh(entry: Optional[AnalysisCacheEntry]) -> bool:
    """True if the cached entry has a reasoning result analysed within CACHE_FRESHNESS."""
    if not entry or not entry.get("last_analyzed") or not entry.get("reasoning"):
        return False
    try:
        last_analyzed = datetime.fromisoformat(entry["last_analyzed"])
    except ValueError:
        return False
    return datetime.now(timezone.utc) - last_analyzed < CACHE_FRESHNESS


def compute_signal_fingerprint(player: EnrichedPlayer) -> dict:
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


def has_zero_signal(player: EnrichedPlayer) -> bool:
    """
    True if there's nothing for the model to react to: no news items, no
    injury designation, and not on IR/taxi. These players get a templated
    response instead of an LLM call — Sonnet has nothing to say about a
    healthy bench player with zero news beyond a generic hold, so paying
    per-token for that verdict is wasted spend. The cache entry still
    carries a signal fingerprint, so the moment real news/injury/roster
    status shows up, is_quiet_reuse_eligible() returns False and the player
    gets a real analysis on the next run.
    """
    return (
        not (player.get("news_items") or [])
        and not (player.get("injury_status") or player.get("news_injury_status"))
        and not player.get("is_ir", False)
        and not player.get("is_taxi", False)
    )


def requires_sonnet_analysis(player: EnrichedPlayer) -> bool:
    """
    True if a player's signal is significant enough to warrant Sonnet's
    deeper reasoning: a flagged injury (news_agent.cross_reference's
    injury-keyword match) or an IR/taxi roster status. Everything else that
    still clears has_zero_signal() — e.g. a routine transaction or
    depth-chart blurb with no injury angle — is templated-quality territory
    rather than nuance territory, so it's routed to Haiku at a fraction of
    the cost with no meaningful quality loss.
    """
    return bool(
        player.get("has_injury_flag")
        or player.get("is_ir", False)
        or player.get("is_taxi", False)
    )


def is_quiet_reuse_eligible(entry: Optional[AnalysisCacheEntry], player: EnrichedPlayer) -> bool:
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

confidence calibration:
- HIGH: verified injury/trade/depth-chart news from a named source, or a confirmed roster move with no real ambiguity left.
- MEDIUM: credible reporting but incomplete — e.g. "week-to-week" with no target return date, or beat reporters giving conflicting takes on a role.
- LOW: thin or stale signal — a single unconfirmed report, or an assessment leaning mostly on general knowledge rather than fresh news.

flag definitions — include only tags that genuinely apply, omit the rest:
- injury: an active or recently resolved injury designation is driving this assessment.
- trade: a trade, waiver claim, or free-agent signing changed this player's situation.
- depth_chart: a role or starting-job change (promotion, demotion, committee shift) not tied to injury or a trade.
- breakout: usage or performance trending meaningfully upward beyond what their prior role predicted.
- bust_risk: real risk this player underperforms their current draft/trade value (age cliff, crowded backfield, contract dispute, etc.).
- target_share: a notable shift, up or down, in receiving or carry share specifically — distinct from a full depth-chart change.

positional dynasty aging curves — use as a prior alongside the player's actual situation, not a substitute for it:
- RB: value typically declines sharply from age 27-28 onward; a 24-and-under back on a clear path to touches is worth more than raw current production alone would suggest.
- WR: value tends to peak later and hold longer than RB, often into the late 20s; route-running and separation skills age better than pure speed.
- TE: production is usually back-loaded — most see their best fantasy seasons in years 3-5, not as rookies, so early-career quiet stretches are less concerning than they'd be at other positions.
- QB: the longest earning window of any position, and especially valuable in superflex/2-QB formats where replacement-level value at the position is much higher than in single-QB leagues.
"""


def format_contract_block(contract: Optional[ContractInfo]) -> str:
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


def build_zero_signal_reasoning(player: EnrichedPlayer) -> ReasoningResult:
    """
    Templated reasoning for a has_zero_signal() player — no LLM call. The
    contract note is pulled straight from the verified Spotrac fields
    (ground truth already, no need to have Sonnet restate it); dynasty_note
    and roster_status_note have no cheap non-LLM substitute so are left
    blank rather than guessed.
    """
    contract = player.get("contract")
    contract_note = None
    if contract and contract.get("found"):
        span = contract.get("contract_span")
        if span:
            deal_type = contract.get("contract_type")
            contract_note = f"{deal_type + ' ' if deal_type else ''}contract, {span}".strip()

    return {
        "trend": "WATCH",
        "confidence": "LOW",
        "summary": "No recent news or injury designation — nothing new this cycle.",
        "fantasy_impact": "NONE",
        "recommendation": "Hold. No action needed based on current information.",
        "dynasty_note": None,
        "contract_note": contract_note,
        "roster_status_note": None,
        "flags": [],
    }


# Per-item news text fields (body/analysis) are capped at this many
# characters before being spliced into the prompt — some scraped articles
# run to several paragraphs, and the headline alone already carries most of
# the trend-relevant signal. Bounds prompt size per player regardless of how
# verbose a given source's write-up happens to be, without dropping the item
# outright (a truncated body still beats no context at all).
NEWS_FIELD_MAX_CHARS = 250


def truncate_news_field(text: Optional[str]) -> Optional[str]:
    if not text or len(text) <= NEWS_FIELD_MAX_CHARS:
        return text
    return text[:NEWS_FIELD_MAX_CHARS].rstrip() + "…"


def build_player_block(player: EnrichedPlayer) -> str:
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
            truncate_news_field(item.get("body")),
            truncate_news_field(item.get("analysis")),
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


def build_player_prompt(player: EnrichedPlayer) -> str:
    """Build a full single-player analysis prompt (used for one-off/manual analysis)."""
    return f"""Analyse this dynasty fantasy NFL player:

{build_player_block(player)}

Provide your structured JSON assessment."""


def build_batch_prompt(players: list[EnrichedPlayer]) -> str:
    """Build one prompt analysing multiple players in a single call, each tagged by PLAYER_ID."""
    ids = ", ".join(str(p.get("player_id")) for p in players)
    blocks = "\n\n".join(build_player_block(p) for p in players)
    return f"""Analyse each of the following {len(players)} dynasty fantasy NFL players.

Respond with a single JSON object whose top-level keys are exactly these PLAYER_IDs, each appearing
exactly once: {ids}

{blocks}

Provide your structured JSON assessment for every player above, keyed by PLAYER_ID."""


def cached_system_block() -> list[TextBlockParam]:
    """System prompt wrapped for Anthropic prompt caching — identical content across every
    call (single or batched) so the cache actually hits."""
    return [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]


def analyse_player(client: anthropic.Anthropic, player: EnrichedPlayer, retries: int = 2) -> Optional[ReasoningResult]:
    """
    Call the Anthropic API to analyse a single player.
    Returns parsed JSON result or None on failure.
    """
    prompt = build_player_prompt(player)

    for attempt in range(retries + 1):
        try:
            response = client.messages.create(
                model=SONNET_MODEL,
                max_tokens=1000,
                system=cached_system_block(),
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()  # type: ignore[union-attr]  # no tools ever passed, always a TextBlock

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


def analyse_players_batch(
    client: anthropic.Anthropic, players: list[EnrichedPlayer], retries: int = 2, model: str = SONNET_MODEL
) -> dict[str, ReasoningResult]:
    """
    Call the Anthropic API once to analyse a whole batch of players, returning
    a dict of player_id (str) -> parsed reasoning dict.

    `model` lets callers route low-stakes batches (no injury/roster flag —
    see requires_sonnet_analysis) to Haiku instead of Sonnet; the prompt and
    schema are identical either way.

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
                model=model,
                max_tokens=max_tokens,
                system=cached_system_block(),
                messages=[{"role": "user", "content": prompt}],
            )

            raw = response.content[0].text.strip()  # type: ignore[union-attr]  # no tools ever passed, always a TextBlock
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


def compute_summary_fingerprint(analysed_players: list[AnalysedPlayer]) -> dict:
    """
    Everything generate_league_summary()'s prompt is actually built from:
    the trend/injury counts and the same top-5 name lists that get spliced
    into the prompt text. If this is unchanged since the last run, Haiku
    would be asked to restate the same facts, so the cached summary is
    reused instead of paying for a new call.
    """
    up = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "UP"]
    down = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "DOWN"]
    watch = [p for p in analysed_players if p.get("reasoning", {}).get("trend") == "WATCH"]
    injured = [p for p in analysed_players if p.get("has_injury_flag")]
    return {
        "total": len(analysed_players),
        "up": len(up),
        "down": len(down),
        "watch": len(watch),
        "injured": len(injured),
        "up_names": sorted(p["full_name"] for p in up[:5]),
        "down_names": sorted(p["full_name"] for p in down[:5]),
        "injured_names": sorted(p["full_name"] for p in injured[:5]),
    }


def generate_league_summary(client: anthropic.Anthropic, league_name: str, analysed_players: list[AnalysedPlayer]) -> str:
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
            model=HAIKU_MODEL,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()  # type: ignore[union-attr]  # no tools ever passed, always a TextBlock
    except Exception as e:
        log.error(f"Failed to generate league summary: {e}")
        return f"{league_name}: {len(up)} trending up, {len(down)} trending down, {len(injured)} injury concerns."


def analyse_in_batches(
    client: anthropic.Anthropic, players: list[EnrichedPlayer], model: str,
    analysis_cache: dict[str, AnalysisCacheEntry]
) -> int:
    """
    Batch-analyse `players` BATCH_SIZE at a time using `model`, writing
    results (and re-saving incrementally, so a crash partway through a long
    run doesn't throw away batches already done) into analysis_cache.
    Returns the number of players successfully analysed.

    Bails early after MAX_CONSECUTIVE_BATCH_FAILURES consecutive completely
    empty batches — see that constant's docstring.
    """
    analysed_count = 0
    consecutive_failures = 0
    for batch_start in range(0, len(players), BATCH_SIZE):
        batch = players[batch_start:batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        total_batches = (len(players) + BATCH_SIZE - 1) // BATCH_SIZE
        log.info(f"Analysing {model} batch {batch_num}/{total_batches} ({len(batch)} players)...")

        batch_results = analyse_players_batch(client, batch, model=model)

        if not batch_results:
            consecutive_failures += 1
        else:
            consecutive_failures = 0

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
                analysed_count += 1
            else:
                log.warning(f"  No result for {player.get('full_name')} (pid={pid}) — will retry next run")

        save_analysis_cache(analysis_cache)

        if consecutive_failures >= MAX_CONSECUTIVE_BATCH_FAILURES:
            remaining = len(players) - (batch_start + len(batch))
            log.error(
                f"{consecutive_failures} consecutive {model} batches returned no results — "
                f"bailing on the remaining {remaining} player(s) for this run instead of "
                "burning their retry budget too. They'll be retried on the next run."
            )
            break

        time.sleep(0.5)
    return analysed_count


def run(sleeper_data: SleeperOutput, news_data: NewsOutput) -> ReasoningOutput:
    """
    Main entry point for the Reasoning agent.
    Takes Sleeper and News agent outputs, returns enriched data with AI analysis.
    """
    log.info("Reasoning agent starting...")
    client = get_client()

    analysis_cache = load_analysis_cache()
    cache_hits = 0
    cache_misses = 0

    summary_cache = load_summary_cache()
    summary_cache_hits = 0
    summary_cache_misses = 0

    result: ReasoningOutput = {
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
    from news_agent import match_to_roster

    league_players: dict[str, list[EnrichedPlayer]] = {}
    unique_players: dict[str, EnrichedPlayer] = {}

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
    to_analyse: list[EnrichedPlayer] = []
    quiet_reuses = 0
    zero_signal_skips = 0
    now_iso = datetime.now(timezone.utc).isoformat()
    for pid, player in unique_players.items():
        cached_entry = analysis_cache.get(pid)
        if is_cache_entry_fresh(cached_entry):
            continue
        if is_quiet_reuse_eligible(cached_entry, player):
            # is_quiet_reuse_eligible() returns False outright when entry is
            # falsy, so True here means cached_entry is non-None.
            assert cached_entry is not None
            cached_entry["last_analyzed"] = now_iso
            quiet_reuses += 1
            continue
        if has_zero_signal(player):
            analysis_cache[pid] = {
                "full_name": player.get("full_name", "Unknown"),
                "reasoning": build_zero_signal_reasoning(player),
                "last_analyzed": now_iso,
                "signal": compute_signal_fingerprint(player),
            }
            zero_signal_skips += 1
            continue
        to_analyse.append(player)

    cache_hits = len(unique_players) - len(to_analyse) - quiet_reuses - zero_signal_skips

    # Pass 1b: batch-analyse the real cache misses, BATCH_SIZE players per
    # call, routed by requires_sonnet_analysis — injury/IR/taxi-flagged
    # players get Sonnet's deeper reasoning; routine news-only players get
    # Haiku at a fraction of the cost.
    sonnet_players = [p for p in to_analyse if requires_sonnet_analysis(p)]
    haiku_players = [p for p in to_analyse if not requires_sonnet_analysis(p)]

    sonnet_misses = analyse_in_batches(client, sonnet_players, SONNET_MODEL, analysis_cache)
    haiku_misses = analyse_in_batches(client, haiku_players, HAIKU_MODEL, analysis_cache)
    cache_misses += sonnet_misses + haiku_misses

    # Pass 2: assemble each league's output from the now fully-populated cache.
    for league in sleeper_data.get("leagues", []):
        league_name = league["league_name"]
        log.info(f"Assembling league: {league_name}")

        enriched_players = league_players[league["league_id"]]

        analysed: list[AnalysedPlayer] = []
        for player in enriched_players:
            pid = str(player.get("player_id"))
            cached_entry = analysis_cache.get(pid)
            reasoning = cached_entry.get("reasoning") if cached_entry else None

            player_result: AnalysedPlayer = {**player}
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
            player_result["last_analyzed"] = cached_entry.get("last_analyzed") if cached_entry else None

            analysed.append(player_result)

        # Generate league summary, reusing the cached one if nothing about
        # the league's trend/injury picture has changed since last time.
        league_id = league["league_id"]
        summary_fingerprint = compute_summary_fingerprint(analysed)
        cached_summary_entry = summary_cache.get(league_id)
        if cached_summary_entry and cached_summary_entry.get("fingerprint") == summary_fingerprint:
            summary = cached_summary_entry["summary"]
            summary_cache_hits += 1
        else:
            summary = generate_league_summary(client, league_name, analysed)
            summary_cache[league_id] = {"summary": summary, "fingerprint": summary_fingerprint}
            summary_cache_misses += 1

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

        league_result: LeagueResult = {
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
    save_summary_cache(summary_cache)
    log.info(
        f"Reasoning agent complete. {cache_hits} cached (fresh), "
        f"{quiet_reuses} reused (no new signal), {zero_signal_skips} templated "
        f"(no news/injury, no LLM call), {cache_misses} freshly analysed "
        f"({sonnet_misses} via Sonnet, {haiku_misses} via Haiku). "
        f"League summaries: {summary_cache_hits} cached, {summary_cache_misses} regenerated."
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [REASONING] %(message)s")
    # Test with mock data
    mock_player: EnrichedPlayer = {
        "player_id": "mock-waddle",
        "full_name": "Jaylen Waddle",
        "position": "WR",
        "team": "MIA",
        "age": 25,
        "years_exp": 3,
        "college": "Alabama",
        "draft_year": 2021,
        "injury_status": "Questionable",
        "injury_body_part": "knee",
        "status": "Active",
        "is_starter": True,
        "is_ir": False,
        "is_taxi": False,
        "fp_player_id": None,
        "fp_rank": None,
        "fp_pos_rank": None,
        "fp_tier": None,
        "fp_scoring_format": "ppr",
        "trade_value": None,
        "contract": None,
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
