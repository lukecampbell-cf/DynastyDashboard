"""
Validation
Runtime validation for the Anthropic API's structured per-player reasoning
output (see reasoning_agent.SYSTEM_PROMPT for the schema Claude is asked to
follow).

json.loads() only proves a response is *some* valid JSON — it says nothing
about whether it actually matches the expected schema, so a hallucinated
enum value, a missing field, or a wrong type would otherwise flow straight
into the persistent analysis cache and downstream dashboard rendering. This
module is a hand-rolled validator rather than a dependency (pydantic etc.):
the repo already expresses its cross-module shapes as TypedDicts + explicit
checks (see schemas.py) rather than a validation framework, so this follows
that same convention instead of introducing a new one for a single call site.
"""

from typing import Any, Optional

from schemas import ReasoningResult
from signal_evidence import SPECIFIC_INJURY_TERMS

TREND_VALUES = {"UP", "DOWN", "WATCH"}
CONFIDENCE_VALUES = {"HIGH", "MEDIUM", "LOW"}
FANTASY_IMPACT_VALUES = {"SHORT", "MEDIUM", "LONG", "NONE"}

# Kept in sync with reasoning_agent.SYSTEM_PROMPT's flag definitions list.
ALLOWED_FLAGS = {"injury", "trade", "depth_chart", "breakout", "bust_risk", "target_share"}

# Generous bounds meant to catch a runaway/degenerate response (e.g. the
# model dumping paragraphs into a field meant to be one sentence), not to
# police normal prose length.
MAX_TEXT_LEN = {
    "summary": 500,
    "recommendation": 400,
    "dynasty_note": 400,
    "contract_note": 300,
    "roster_status_note": 300,
}


class ValidationError(ValueError):
    """Raised when a player's parsed JSON doesn't satisfy the reasoning schema."""


def _require_str(raw: dict, field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field!r} must be a non-empty string, got {value!r}")
    max_len = MAX_TEXT_LEN.get(field)
    if max_len is not None and len(value) > max_len:
        raise ValidationError(f"{field!r} exceeds {max_len} characters ({len(value)})")
    return value


def _optional_str(raw: dict, field: str) -> Optional[str]:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(f"{field!r} must be a string or null, got {type(value).__name__}")
    max_len = MAX_TEXT_LEN.get(field)
    if max_len is not None and len(value) > max_len:
        raise ValidationError(f"{field!r} exceeds {max_len} characters ({len(value)})")
    return value or None


def _validate_flags(raw: dict) -> list:
    flags = raw.get("flags")
    if flags is None:
        return []
    if not isinstance(flags, list):
        raise ValidationError(f"'flags' must be a list, got {type(flags).__name__}")
    clean = []
    for flag in flags:
        if not isinstance(flag, str):
            raise ValidationError(f"'flags' entries must be strings, got {flag!r}")
        # An unknown flag value is dropped rather than rejecting the whole
        # record — the enum fields above are the ones worth failing hard on;
        # flags is a set of optional tags where Claude drifting from the
        # allowed list shouldn't cost the player their whole analysis.
        if flag in ALLOWED_FLAGS:
            clean.append(flag)
    return clean


def _require_enum(raw: dict, field: str, allowed: set) -> str:
    value = raw.get(field)
    # Membership-test only after confirming it's a hashable-safe string —
    # an unhashable value (list/dict) would otherwise raise TypeError from
    # `in allowed` instead of the intended ValidationError.
    if not isinstance(value, str) or value not in allowed:
        raise ValidationError(f"{field!r} must be one of {sorted(allowed)}, got {value!r}")
    return value


def validate_reasoning_result(raw: Any, *, player_id: str) -> ReasoningResult:
    """
    Validate one player's parsed reasoning dict against the schema
    reasoning_agent.SYSTEM_PROMPT asks Claude to follow. Raises
    ValidationError (with the specific reason) on any violation; returns a
    clean ReasoningResult — only known fields, flags filtered to
    ALLOWED_FLAGS — on success.
    """
    if not isinstance(raw, dict):
        raise ValidationError(f"expected a JSON object for player {player_id}, got {type(raw).__name__}")

    trend = _require_enum(raw, "trend", TREND_VALUES)
    confidence = _require_enum(raw, "confidence", CONFIDENCE_VALUES)
    fantasy_impact = _require_enum(raw, "fantasy_impact", FANTASY_IMPACT_VALUES)

    summary = _require_str(raw, "summary")
    recommendation = _require_str(raw, "recommendation")
    dynasty_note = _optional_str(raw, "dynasty_note")
    contract_note = _optional_str(raw, "contract_note")
    roster_status_note = _optional_str(raw, "roster_status_note")
    flags = _validate_flags(raw)

    return {
        "trend": trend,
        "confidence": confidence,
        "summary": summary,
        "fantasy_impact": fantasy_impact,
        "recommendation": recommendation,
        "dynasty_note": dynasty_note,
        "contract_note": contract_note,
        "roster_status_note": roster_status_note,
        "flags": flags,
    }


# ── Product-reliability guardrails ─────────────────────────────────────────
#
# validate_reasoning_result() above only checks that Claude's response
# satisfies the *schema* (right keys, right types, right enum values). The
# functions below run afterward and check the response against the actual
# facts available (signal_evidence.py) and against a fixed policy — "the
# strength of the recommendation must never exceed the strength of the
# evidence supporting it." Both are deterministic, code-enforced guarantees
# rather than something left to prompt wording alone, since prompt
# instructions alone can't be regression-tested without a live API call.


def _unsupported_specific_terms(text: Optional[str], supported: set) -> list:
    """Which SPECIFIC_INJURY_TERMS appear in `text` that aren't in `supported`
    (the terms signal_evidence.py actually found in this player's own
    evidence) — i.e. terms Claude stated with nothing behind them."""
    if not text:
        return []
    text_lower = text.lower()
    return [term for term in SPECIFIC_INJURY_TERMS if term in text_lower and term not in supported]


CONFIDENCE_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def enforce_injury_evidence_bound(result: ReasoningResult, evidence: dict) -> ReasoningResult:
    """
    Guards against Claude stating a more specific injury/diagnosis than the
    player's actual evidence supports (signal_evidence.summarise_injury_evidence)
    — e.g. upgrading a plain "knee injury" into "ACL injury" with nothing
    behind it. Checked deterministically against the generated text rather
    than relying on the system prompt alone, so the guarantee holds even if
    a response doesn't follow that instruction.

    On violation: only the offending field(s) are replaced — `summary` with
    a deterministic evidence-grounded sentence, `dynasty_note`/
    `roster_status_note` cleared if they're the ones carrying the
    unsupported term — and confidence is capped at the evidence's own
    ceiling. The rest of the analysis (trend, flags, an unaffected
    recommendation) is left intact rather than discarding an otherwise-good
    result over one oversold phrase.
    """
    supported = set(evidence.get("specific_terms_supported") or [])
    hits = {
        "summary": _unsupported_specific_terms(result.get("summary"), supported),
        "dynasty_note": _unsupported_specific_terms(result.get("dynasty_note"), supported),
        "roster_status_note": _unsupported_specific_terms(result.get("roster_status_note"), supported),
    }
    if not any(hits.values()):
        return result

    fallback_claim = evidence.get("claim") or "an unspecified status concern"
    updated: ReasoningResult = dict(result)  # type: ignore[assignment]
    if hits["summary"]:
        updated["summary"] = (
            f"Reported {fallback_claim} — a more specific diagnosis is not confirmed by available sources."
        )
    if hits["dynasty_note"]:
        updated["dynasty_note"] = None
    if hits["roster_status_note"]:
        updated["roster_status_note"] = None

    ceiling = evidence.get("confidence", "LOW")
    if CONFIDENCE_RANK.get(updated.get("confidence", "LOW"), 2) < CONFIDENCE_RANK.get(ceiling, 2):
        updated["confidence"] = ceiling  # type: ignore[typeddict-item]

    return updated


# Deliberately small and curated — these are the phrasings the brief calls
# out as overstating LOW/MEDIUM-confidence evidence, not a general profanity/
# sentiment filter. Matched case-insensitively as substrings.
STRONG_ACTION_PHRASES = [
    "sell immediately", "sell now", "sell him now", "sell her now",
    "buy aggressively", "buy now aggressively",
    "drop him", "drop her", "drop them",
    "cut him", "cut her", "cut them",
    "must sell", "must buy", "must drop",
]

# Deterministic fallback recommendations for a confidence tier whose
# generated recommendation overstepped STRONG_ACTION_PHRASES — phrased to
# match the brief's own examples for that tier ("Monitor" for LOW, "Hold"/
# "Explore trade value" for MEDIUM).
RECOMMENDATION_DEFAULTS = {
    "LOW": "Monitor for further updates before acting.",
    "MEDIUM": "Hold and monitor — consider exploring trade value if the situation clarifies.",
}


def enforce_recommendation_confidence(result: ReasoningResult) -> ReasoningResult:
    """
    Bounds recommendation strength by the result's own confidence: LOW/
    MEDIUM confidence cannot carry an extreme action ("sell immediately,"
    "buy aggressively," "drop") — those are reserved for HIGH confidence,
    where the evidence genuinely warrants a stronger call. Only
    `recommendation` is replaced; the rest of the analysis is unaffected.
    """
    confidence = result.get("confidence")
    if confidence not in RECOMMENDATION_DEFAULTS:
        return result
    recommendation = result.get("recommendation") or ""
    if not any(phrase in recommendation.lower() for phrase in STRONG_ACTION_PHRASES):
        return result
    return {**result, "recommendation": RECOMMENDATION_DEFAULTS[confidence]}


def apply_reasoning_guardrails(result: ReasoningResult, evidence: dict) -> ReasoningResult:
    """
    Single entry point reasoning_agent.py calls once per already schema-
    validated result (validate_reasoning_result() has already run) —
    enforces both product-reliability guarantees: an injury/status claim
    can't exceed its evidence, and a recommendation's strength can't exceed
    its own confidence. Order matters here: if the evidence guard downgrades
    confidence, the recommendation guard below checks against that
    (possibly lowered) confidence, not the original.
    """
    result = enforce_injury_evidence_bound(result, evidence)
    result = enforce_recommendation_confidence(result)
    return result
