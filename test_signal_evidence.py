"""
Unit tests for signal_evidence.py — the deterministic (no LLM) injury-
evidence provenance snapshot and change-status classification that sit
between raw player data and the reasoning prompt/output, and between
successive pipeline runs.

Run directly:  ./venv/bin/python test_signal_evidence.py
Or via unittest: ./venv/bin/python -m unittest test_signal_evidence -v
"""

import unittest

from signal_evidence import classify_change_status, summarise_injury_evidence


def make_player(**overrides) -> dict:
    player: dict = {
        "injury_status": None,
        "news_injury_status": None,
        "injury_body_part": None,
        "news_items": [],
    }
    player.update(overrides)
    return player


class InjuryEvidenceProvenanceTests(unittest.TestCase):
    def test_structured_status_and_body_part_is_structured_provenance(self):
        player = make_player(injury_status="Questionable", injury_body_part="knee")
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "STRUCTURED")
        self.assertEqual(evidence["confidence"], "HIGH")
        self.assertEqual(evidence["claim"], "knee injury")

    def test_structured_status_without_body_part_still_structured(self):
        player = make_player(injury_status="Doubtful")
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "STRUCTURED")
        self.assertEqual(evidence["claim"], "doubtful")

    def test_two_distinct_sources_is_corroborated(self):
        player = make_player(news_items=[
            {"source": "rotowire", "headline": "Player limited with knee injury"},
            {"source": "espn", "headline": "Player dealing with knee soreness"},
        ])
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "CORROBORATED")
        self.assertEqual(evidence["confidence"], "MEDIUM")
        self.assertTrue(evidence["corroborated"])
        self.assertEqual(evidence["source_count"], 2)

    def test_same_source_repeated_is_not_corroborated(self):
        player = make_player(news_items=[
            {"source": "rotowire", "headline": "Player limited with knee injury"},
            {"source": "rotowire", "headline": "Update: still limited with knee injury"},
        ])
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "SINGLE_SOURCE")
        self.assertEqual(evidence["source_count"], 1)

    def test_single_source_is_single_source_provenance(self):
        player = make_player(news_items=[
            {"source": "rotowire", "headline": "Player questionable with ankle injury"},
        ])
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "SINGLE_SOURCE")
        self.assertEqual(evidence["confidence"], "LOW")
        self.assertEqual(evidence["claim"], "unspecified injury concern")

    def test_no_signal_at_all_is_none_provenance(self):
        player = make_player(news_items=[{"source": "rotowire", "headline": "Player signs autographs at camp"}])
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "NONE")
        self.assertIsNone(evidence["claim"])
        self.assertEqual(evidence["confidence"], "LOW")

    def test_structured_takes_priority_over_news_corroboration(self):
        # Even with 2 corroborating sources, a structured Sleeper status is
        # still the authoritative claim.
        player = make_player(
            injury_status="Out", injury_body_part="hamstring",
            news_items=[
                {"source": "rotowire", "headline": "Player out with hamstring injury"},
                {"source": "espn", "headline": "Player ruled out, hamstring"},
            ],
        )
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["provenance"], "STRUCTURED")
        self.assertEqual(evidence["claim"], "hamstring injury")


class SpecificTermDetectionTests(unittest.TestCase):
    def test_specific_term_present_in_news_is_detected(self):
        player = make_player(news_items=[
            {"source": "rotowire", "headline": "Player suffers torn ACL, out for season", "body": "", "analysis": ""},
        ])
        evidence = summarise_injury_evidence(player)
        self.assertIn("acl", evidence["specific_terms_supported"])
        self.assertIn("torn", evidence["specific_terms_supported"])

    def test_specific_term_absent_when_only_generic_injury_reported(self):
        player = make_player(injury_status="Questionable", injury_body_part="knee")
        evidence = summarise_injury_evidence(player)
        self.assertEqual(evidence["specific_terms_supported"], [])

    def test_specific_term_in_body_part_is_detected(self):
        player = make_player(injury_body_part="Achilles")
        evidence = summarise_injury_evidence(player)
        self.assertIn("achilles", evidence["specific_terms_supported"])


class ChangeStatusClassificationTests(unittest.TestCase):
    def test_zero_signal_is_no_signal_regardless_of_fingerprint_diff(self):
        status = classify_change_status(
            previous_signal={"a": 1}, current_signal={"a": 2},
            is_zero_signal=True, has_injury_flag=False, trend=None,
        )
        self.assertEqual(status, "no_signal")

    def test_no_prior_signal_is_material_change(self):
        status = classify_change_status(
            previous_signal=None, current_signal={"a": 1},
            is_zero_signal=False, has_injury_flag=False, trend="WATCH",
        )
        self.assertEqual(status, "material_change")

    def test_changed_fingerprint_is_material_change(self):
        status = classify_change_status(
            previous_signal={"a": 1}, current_signal={"a": 2},
            is_zero_signal=False, has_injury_flag=False, trend="WATCH",
        )
        self.assertEqual(status, "material_change")

    def test_unchanged_fingerprint_with_injury_flag_is_noteworthy(self):
        status = classify_change_status(
            previous_signal={"a": 1}, current_signal={"a": 1},
            is_zero_signal=False, has_injury_flag=True, trend="WATCH",
        )
        self.assertEqual(status, "noteworthy_unchanged")

    def test_unchanged_fingerprint_with_active_trend_is_noteworthy(self):
        status = classify_change_status(
            previous_signal={"a": 1}, current_signal={"a": 1},
            is_zero_signal=False, has_injury_flag=False, trend="UP",
        )
        self.assertEqual(status, "noteworthy_unchanged")

    def test_unchanged_fingerprint_watch_no_injury_is_stable(self):
        status = classify_change_status(
            previous_signal={"a": 1}, current_signal={"a": 1},
            is_zero_signal=False, has_injury_flag=False, trend="WATCH",
        )
        self.assertEqual(status, "stable")


if __name__ == "__main__":
    unittest.main()
