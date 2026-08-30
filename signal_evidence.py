"""
Signal Evidence
Deterministic (no LLM) evidence and change-classification helpers that sit
between the raw enriched player data and the reasoning prompt/output, and
between successive pipeline runs. Two independent concerns live here:

  - summarise_injury_evidence(): a provenance snapshot of what's actually
    known about a player's injury/status. Dashboard cards use this to show
    how strongly an injury claim is supported without inferring details.

  - classify_change_status(): compares a player's current signal fingerprint
    against the fingerprint stored from their *previous* analysis, to answer
    "what's actually new since last time" without any additional LLM calls.

These stay pure, easily unit-testable functions — callers pass in
whatever plain values they already have rather than this module reaching
into EnrichedPlayer/ReasoningResult shapes itself.
"""

from datetime import datetime
from typing import Optional

from news_agent import INJURY_KEYWORDS
from schemas import EnrichedPlayer, NewsItem

# Specific structural/medical terms that are explicitly present in the
# player's own evidence (structured injury_body_part or a news item's text).
SPECIFIC_INJURY_TERMS = [
    "acl", "mcl", "pcl", "achilles", "meniscus", "labrum", "lisfranc",
    "torn", "rupture", "ruptured", "fracture", "fractured",
    "season-ending", "season ending", "surgery",
]

# Provenance tiers, strongest first — see summarise_injury_evidence().
PROVENANCE_CONFIDENCE = {
    "STRUCTURED": "HIGH",
    "CORROBORATED": "MEDIUM",
    "SINGLE_SOURCE": "LOW",
    "NONE": "LOW",
}


def _injury_text_fields(item: NewsItem) -> str:
    return " ".join(filter(None, [item.get("headline"), item.get("body"), item.get("analysis")])).lower()


def _is_injury_item(item: NewsItem) -> bool:
    return any(kw in _injury_text_fields(item) for kw in INJURY_KEYWORDS)


def _find_specific_terms(text_sources: list) -> list:
    """Which SPECIFIC_INJURY_TERMS actually appear (verbatim, case-insensitive)
    across the given text sources — order matches SPECIFIC_INJURY_TERMS, deduped."""
    combined = " ".join(t for t in text_sources if t).lower()
    return [term for term in SPECIFIC_INJURY_TERMS if term in combined]


def _latest_timestamp(timestamps: list) -> Optional[str]:
    """Latest of a list of ISO timestamp strings, parsed for correct
    chronological (not lexicographic) comparison; returns the original
    string, not the parsed datetime. None if none parse."""
    dated = []
    for ts in timestamps:
        try:
            dated.append((ts, datetime.fromisoformat(ts)))
        except (TypeError, ValueError):
            continue
    if not dated:
        return None
    return max(dated, key=lambda pair: pair[1])[0]


def summarise_injury_evidence(player: EnrichedPlayer) -> dict:
    """
    Deterministic snapshot of what's actually known about a player's
    injury/status, built only from facts already present on `player` —
    never inferred or guessed:

      claim: the most specific fact actually available — Sleeper's own
        structured injury_status/injury_body_part if present (authoritative,
        comes from the league's own player database), else a generic
        "unspecified injury concern" if only scraped-news keyword matches
        exist, else None. Never a specific diagnosis unless one is already
        present verbatim in the evidence (see specific_terms_supported).

      provenance: STRUCTURED (Sleeper's own status/body-part field) >
        CORROBORATED (2+ distinct news sources hit an injury keyword) >
        SINGLE_SOURCE (1) > NONE.

      confidence: PROVENANCE_CONFIDENCE[provenance].

      specific_terms_supported: which SPECIFIC_INJURY_TERMS actually appear
        in the evidence.
    """
    structured_status = player.get("injury_status") or player.get("news_injury_status")
    body_part = player.get("injury_body_part") or None
    news_items = player.get("news_items") or []
    injury_items = [item for item in news_items if _is_injury_item(item)]
    distinct_sources = {item.get("source") for item in injury_items if item.get("source")}

    if body_part:
        claim = f"{body_part} injury" if structured_status else f"reported {body_part} issue"
    elif structured_status:
        claim = structured_status.lower()
    elif injury_items:
        claim = "unspecified injury concern"
    else:
        claim = None

    if structured_status or body_part:
        provenance = "STRUCTURED"
    elif len(distinct_sources) >= 2:
        provenance = "CORROBORATED"
    elif len(distinct_sources) == 1:
        provenance = "SINGLE_SOURCE"
    else:
        provenance = "NONE"

    text_sources = [body_part or ""]
    for item in injury_items:
        text_sources.extend([item.get("headline") or "", item.get("body") or "", item.get("analysis") or ""])

    timestamps = [
        item.get("scraped_at") or item.get("published_at")
        for item in injury_items
        if item.get("scraped_at") or item.get("published_at")
    ]

    return {
        "claim": claim,
        "provenance": provenance,
        "confidence": PROVENANCE_CONFIDENCE[provenance],
        "corroborated": len(distinct_sources) >= 2,
        "source_count": len(distinct_sources),
        "specific_terms_supported": _find_specific_terms(text_sources),
        "latest_source_at": _latest_timestamp(timestamps),
    }


def classify_change_status(
    previous_signal: Optional[dict],
    current_signal: dict,
    *,
    is_zero_signal: bool,
    has_injury_flag: bool,
    trend: Optional[str],
) -> str:
    """
    Classify what's actually new for a player since their previous analysis,
    purely from comparing signal fingerprints already computed for the
    quiet-reuse cache logic — no additional LLM call. Answers "what changed
    since I last looked?" rather than restating every player's full state.

    "material_change": no prior fingerprint on record (first time this
      player has had real signal) or the fingerprint itself moved — team,
      injury/roster status, trade value tier, contract span, or news
      headlines (see reasoning_agent.compute_signal_fingerprint).
    "noteworthy_unchanged": fingerprint unchanged since last run, but the
      player still carries an active injury flag or a non-WATCH trend —
      worth a glance even though nothing new happened this cycle.
    "stable": fingerprint unchanged, no injury flag, WATCH trend — settled.
    "no_signal": nothing for the model to react to at all (see
      reasoning_agent.has_zero_signal) — the templated, no-LLM-call case.
    """
    if is_zero_signal:
        return "no_signal"
    if previous_signal is None or previous_signal != current_signal:
        return "material_change"
    if has_injury_flag or trend in ("UP", "DOWN"):
        return "noteworthy_unchanged"
    return "stable"
