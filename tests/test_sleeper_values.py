import unittest

from dynasty_dashboard.sleeper_values import (
    determine_ranking_format,
    determine_value_format,
    extract_draft_year,
    trade_value_tier,
)


class SleeperValueHelpersTests(unittest.TestCase):
    def test_ranking_format_prefers_dynasty_then_scoring(self):
        self.assertEqual(determine_ranking_format({"type": 2}, {"rec": 0}), "dynasty")
        self.assertEqual(determine_ranking_format({}, {"rec": 1}), "ppr")
        self.assertEqual(determine_ranking_format({}, {"rec": 0.5}), "half_ppr")
        self.assertEqual(determine_ranking_format({}, {}), "standard")

    def test_value_format_detects_both_superflex_shapes(self):
        self.assertEqual(determine_value_format(["QB", "SUPER_FLEX"]), "sf")
        self.assertEqual(determine_value_format(["QB", "QB"]), "sf")
        self.assertEqual(determine_value_format(["QB", "RB", "WR"]), "1qb")

    def test_trade_value_tier_boundaries(self):
        self.assertIsNone(trade_value_tier(None))
        self.assertEqual(trade_value_tier(4), "Elite / Early 1st")
        self.assertEqual(trade_value_tier(5), "Mid 1st")
        self.assertEqual(trade_value_tier(101), "Waiver / Deep Stash")

    def test_draft_year_rejects_malformed_metadata(self):
        self.assertEqual(extract_draft_year({"metadata": {"rookie_year": "2025"}}), 2025)
        self.assertIsNone(extract_draft_year({"metadata": {"rookie_year": "unknown"}}))
        self.assertIsNone(extract_draft_year({}))


if __name__ == "__main__":
    unittest.main()
