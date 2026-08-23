"""
Unit tests for validation.py — the runtime schema check on the Anthropic
API's structured per-player reasoning output. json.loads() only proves a
response is valid JSON; these tests prove that a structurally-invalid but
still-parseable response (bad enum, missing field, wrong type, oversized
text, unknown flag) is caught before it can reach the persistent cache.

Run directly:  ./venv/bin/python test_validation.py
Or via unittest: ./venv/bin/python -m unittest test_validation -v
"""

import unittest

from old.validation import (
    ValidationError,
    apply_reasoning_guardrails,
    enforce_injury_evidence_bound,
    enforce_recommendation_confidence,
    validate_reasoning_result,
)


def make_valid() -> dict:
    return {
        "trend": "UP",
        "confidence": "HIGH",
        "summary": "Returned from injury and reclaimed the starting role.",
        "fantasy_impact": "SHORT",
        "recommendation": "Start with confidence this week.",
        "dynasty_note": "Still a strong long-term dynasty asset.",
        "contract_note": "Rookie deal, year 2 of 4.",
        "roster_status_note": "Locked-in starter.",
        "flags": ["injury", "breakout"],
    }


class ValidReasoningResultTests(unittest.TestCase):
    def test_fully_valid_payload_passes_through(self):
        result = validate_reasoning_result(make_valid(), player_id="100")
        self.assertEqual(result["trend"], "UP")
        self.assertEqual(result["flags"], ["injury", "breakout"])

    def test_null_optional_notes_are_allowed(self):
        payload = make_valid()
        payload["dynasty_note"] = None
        payload["contract_note"] = None
        payload["roster_status_note"] = None
        result = validate_reasoning_result(payload, player_id="100")
        self.assertIsNone(result["dynasty_note"])

    def test_missing_flags_defaults_to_empty_list(self):
        payload = make_valid()
        del payload["flags"]
        result = validate_reasoning_result(payload, player_id="100")
        self.assertEqual(result["flags"], [])

    def test_unknown_flag_is_dropped_not_rejected(self):
        # Flags is a set of optional tags — an unrecognised one (model drift
        # from the allowed list) shouldn't cost the player their whole
        # analysis, unlike a bad required enum field.
        payload = make_valid()
        payload["flags"] = ["injury", "made_up_flag"]
        result = validate_reasoning_result(payload, player_id="100")
        self.assertEqual(result["flags"], ["injury"])


class NotAnObjectTests(unittest.TestCase):
    def test_non_dict_top_level_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_reasoning_result(["not", "a", "dict"], player_id="100")

    def test_none_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_reasoning_result(None, player_id="100")


class MissingFieldTests(unittest.TestCase):
    def test_missing_summary_is_rejected(self):
        payload = make_valid()
        del payload["summary"]
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_missing_recommendation_is_rejected(self):
        payload = make_valid()
        del payload["recommendation"]
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_empty_string_summary_is_rejected(self):
        payload = make_valid()
        payload["summary"] = "   "
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")


class EnumValueTests(unittest.TestCase):
    def test_invalid_trend_is_rejected(self):
        payload = make_valid()
        payload["trend"] = "SIDEWAYS"
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_missing_trend_is_rejected(self):
        payload = make_valid()
        del payload["trend"]
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_invalid_confidence_is_rejected(self):
        payload = make_valid()
        payload["confidence"] = "SUPER_HIGH"
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_invalid_fantasy_impact_is_rejected(self):
        payload = make_valid()
        payload["fantasy_impact"] = "FOREVER"
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_lowercase_trend_is_rejected(self):
        # Enum values are case-sensitive against the exact schema the prompt
        # specifies — "up" is not "UP".
        payload = make_valid()
        payload["trend"] = "up"
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")


class WrongTypeTests(unittest.TestCase):
    def test_flags_as_string_is_rejected(self):
        payload = make_valid()
        payload["flags"] = "injury"
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_flags_with_non_string_entry_is_rejected(self):
        payload = make_valid()
        payload["flags"] = ["injury", 42]
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_summary_as_number_is_rejected(self):
        payload = make_valid()
        payload["summary"] = 12345
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_dynasty_note_as_number_is_rejected(self):
        payload = make_valid()
        payload["dynasty_note"] = 42
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_trend_as_list_is_rejected(self):
        payload = make_valid()
        payload["trend"] = ["UP"]
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")


class TextLengthTests(unittest.TestCase):
    def test_excessive_summary_length_is_rejected(self):
        payload = make_valid()
        payload["summary"] = "x" * 501
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")

    def test_summary_at_the_limit_is_accepted(self):
        payload = make_valid()
        payload["summary"] = "x" * 500
        result = validate_reasoning_result(payload, player_id="100")
        self.assertEqual(len(result["summary"]), 500)

    def test_excessive_dynasty_note_length_is_rejected(self):
        payload = make_valid()
        payload["dynasty_note"] = "x" * 401
        with self.assertRaises(ValidationError):
            validate_reasoning_result(payload, player_id="100")


def make_evidence(**overrides) -> dict:
    evidence = {
        "claim": "knee injury",
        "provenance": "STRUCTURED",
        "confidence": "HIGH",
        "corroborated": False,
        "source_count": 0,
        "specific_terms_supported": [],
        "latest_source_at": None,
    }
    evidence.update(overrides)
    return evidence


class InjuryEvidenceGuardTests(unittest.TestCase):
    """
    enforce_injury_evidence_bound() — the P0 guard against Claude escalating
    an unsupported injury claim into a more specific one, e.g. the brief's
    own "knee injury" -> "ACL injury" example.
    """

    def test_unsupported_specific_term_is_rewritten(self):
        result = make_valid()
        result["summary"] = "Suffered a torn ACL and will require surgery."
        evidence = make_evidence(claim="knee injury", specific_terms_supported=[])

        fixed = enforce_injury_evidence_bound(result, evidence)

        self.assertNotIn("acl", fixed["summary"].lower())
        self.assertIn("knee injury", fixed["summary"])

    def test_supported_specific_term_passes_through_untouched(self):
        # The source itself explicitly named ACL — Claude is allowed to say it.
        result = make_valid()
        result["summary"] = "Confirmed torn ACL, will miss the rest of the season."
        evidence = make_evidence(claim="ACL injury", specific_terms_supported=["acl", "torn"])

        fixed = enforce_injury_evidence_bound(result, evidence)

        self.assertEqual(fixed["summary"], result["summary"])

    def test_unsupported_term_caps_confidence_at_evidence_ceiling(self):
        result = make_valid()
        result["confidence"] = "HIGH"
        result["summary"] = "Diagnosed with a torn meniscus."
        evidence = make_evidence(confidence="LOW", specific_terms_supported=[])

        fixed = enforce_injury_evidence_bound(result, evidence)

        self.assertEqual(fixed["confidence"], "LOW")

    def test_no_violation_leaves_result_untouched(self):
        result = make_valid()
        evidence = make_evidence(specific_terms_supported=[])
        fixed = enforce_injury_evidence_bound(result, evidence)
        self.assertEqual(fixed, result)

    def test_dynasty_note_with_unsupported_term_is_cleared_not_summary(self):
        result = make_valid()
        result["dynasty_note"] = "Long-term outlook depends on ACL recovery timeline."
        evidence = make_evidence(specific_terms_supported=[])

        fixed = enforce_injury_evidence_bound(result, evidence)

        self.assertIsNone(fixed["dynasty_note"])
        # summary wasn't the offending field, so it's left alone.
        self.assertEqual(fixed["summary"], result["summary"])


class RecommendationConfidenceGuardTests(unittest.TestCase):
    """
    enforce_recommendation_confidence() — the P0 guard binding recommendation
    strength to confidence: LOW/MEDIUM can't carry an extreme action.
    """

    def test_low_confidence_strong_action_is_replaced(self):
        result = make_valid()
        result["confidence"] = "LOW"
        result["recommendation"] = "Sell immediately before value craters."

        fixed = enforce_recommendation_confidence(result)

        self.assertNotIn("immediately", fixed["recommendation"].lower())

    def test_medium_confidence_strong_action_is_replaced(self):
        result = make_valid()
        result["confidence"] = "MEDIUM"
        result["recommendation"] = "Buy aggressively while the price is low."

        fixed = enforce_recommendation_confidence(result)

        self.assertNotIn("aggressively", fixed["recommendation"].lower())

    def test_high_confidence_strong_action_is_untouched(self):
        result = make_valid()
        result["confidence"] = "HIGH"
        result["recommendation"] = "Sell immediately before value craters."

        fixed = enforce_recommendation_confidence(result)

        self.assertEqual(fixed["recommendation"], result["recommendation"])

    def test_measured_recommendation_is_untouched_regardless_of_confidence(self):
        result = make_valid()
        result["confidence"] = "LOW"
        result["recommendation"] = "Monitor for further updates."

        fixed = enforce_recommendation_confidence(result)

        self.assertEqual(fixed["recommendation"], result["recommendation"])


class ApplyGuardrailsCompositionTests(unittest.TestCase):
    def test_evidence_downgrade_feeds_into_recommendation_guard(self):
        # HIGH confidence + unsupported term -> confidence drops to the
        # evidence ceiling (LOW) -> the now-LOW confidence should then also
        # block the strong-action recommendation, even though HIGH alone
        # would have allowed it through.
        result = make_valid()
        result["confidence"] = "HIGH"
        result["summary"] = "Torn ACL confirmed."
        result["recommendation"] = "Sell immediately."
        evidence = make_evidence(confidence="LOW", specific_terms_supported=[])

        fixed = apply_reasoning_guardrails(result, evidence)

        self.assertEqual(fixed["confidence"], "LOW")
        self.assertNotIn("immediately", fixed["recommendation"].lower())

    def test_fully_supported_high_confidence_result_is_untouched(self):
        result = make_valid()
        result["confidence"] = "HIGH"
        result["summary"] = "Confirmed torn ACL, out for the season."
        result["recommendation"] = "Sell immediately given the season-ending injury."
        evidence = make_evidence(confidence="HIGH", specific_terms_supported=["acl", "torn", "season-ending"])

        fixed = apply_reasoning_guardrails(result, evidence)

        self.assertEqual(fixed, result)


if __name__ == "__main__":
    unittest.main()
