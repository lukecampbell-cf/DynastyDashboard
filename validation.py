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
