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

from validation import ValidationError, validate_reasoning_result


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


if __name__ == "__main__":
    unittest.main()
