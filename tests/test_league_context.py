import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dynasty_dashboard import league_context as lc


def roster(roster_id, owner_id, wins, losses, fpts):
    return {"roster_id": roster_id, "owner_id": owner_id, "settings": {"wins": wins, "losses": losses, "ties": 0, "fpts": fpts}}


class LeagueContextTests(unittest.TestCase):
    def setUp(self):
        self.rosters = [roster(1, "u1", 6, 2, 900), roster(2, "u2", 3, 5, 700)]
        self.users = {
            "u1": {"display_name": "Me", "metadata": {"team_name": "My Team"}},
            "u2": {"display_name": "Them", "metadata": {}},
        }
        self.league = {"league_id": "L1", "season": "2026", "settings": {"playoff_week_start": 10, "playoff_teams": 1}}

    def test_builds_record_standing_odds_and_current_matchup(self):
        def fetch(_league_id, week):
            return [
                {"roster_id": 1, "matchup_id": 1, "points": 101.2, "projected_points": 115.4},
                {"roster_id": 2, "matchup_id": 1, "points": 99.1, "projected_points": 108.3},
            ]

        result = lc.build_context(self.league, self.rosters, self.users, 1, 9, fetch)
        self.assertEqual(result["my_standing"]["rank"], 1)
        self.assertGreater(result["my_standing"]["playoff_chance"], 50)
        self.assertEqual(result["matchup"]["opponent_team"], "Them")
        self.assertEqual(result["matchup"]["my_projection"], 115.4)
        self.assertTrue(result["my_standing"]["playoff_status"])

    def test_playoff_status_uses_nfl_style_probability_bands(self):
        cases = [
            (100, "Locked In"),
            (99, "Locked In"),
            (85, "Controls Their Destiny"),
            (65, "In the Playoff Picture"),
            (45, "On the Bubble"),
            (20, "In the Hunt"),
            (5, "Needs Help"),
            (0, "Eliminated"),
        ]
        for chance, expected in cases:
            with self.subTest(chance=chance):
                self.assertEqual(lc.playoff_status(chance), expected)

    def test_initializes_empty_cache_before_any_league_is_processed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "league_context_cache.json"
            self.assertTrue(lc.initialize_cache(path))
            self.assertTrue(path.exists())
            self.assertEqual(path.read_text().strip(), "{}")

    def test_cache_initialization_failure_is_non_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "league_context_cache.json"
            with patch.object(lc, "write_json_atomic", side_effect=PermissionError("read-only")):
                self.assertFalse(lc.initialize_cache(path))
            self.assertFalse(path.exists())

    def test_repairs_existing_empty_or_corrupt_cache_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "league_context_cache.json"
            path.write_text("")
            self.assertTrue(lc.initialize_cache(path))
            self.assertEqual(path.read_text().strip(), "{}")

            path.write_text("[]")
            self.assertTrue(lc.initialize_cache(path))
            self.assertEqual(path.read_text().strip(), "{}")

    def test_projection_is_none_when_feed_does_not_supply_it(self):
        result = lc.build_context(
            self.league, self.rosters, self.users, 1, 9,
            lambda _league_id, _week: [
                {"roster_id": 1, "matchup_id": 1, "points": 10},
                {"roster_id": 2, "matchup_id": 1, "points": 20},
            ],
        )
        self.assertIsNone(result["matchup"]["my_projection"])

    def test_weekly_cache_refreshes_on_first_tuesday_run(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "context.json"
            fetch = lambda _league_id, week: calls.append(week) or []
            monday = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
            tuesday = datetime(2026, 10, 6, 12, tzinfo=timezone.utc)
            lc.get_league_context(self.league, self.rosters, self.users, 1, 9, fetch, now=monday, cache_path=path)
            first_count = len(calls)
            lc.get_league_context(self.league, self.rosters, self.users, 1, 9, fetch, now=monday, cache_path=path)
            self.assertEqual(len(calls), first_count)
            lc.get_league_context(self.league, self.rosters, self.users, 1, 9, fetch, now=tuesday, cache_path=path)
            self.assertGreater(len(calls), first_count)

    def test_cache_write_failure_still_returns_live_context(self):
        feed = [
            {"roster_id": 1, "matchup_id": 1, "points": 10},
            {"roster_id": 2, "matchup_id": 1, "points": 20},
        ]
        with patch.object(lc, "write_json_atomic", side_effect=PermissionError("read-only directory")):
            result = lc.get_league_context(
                self.league, self.rosters, self.users, 1, 9,
                lambda _league_id, _week: feed,
            )
        self.assertEqual(len(result["standings"]), 2)
        self.assertEqual(result["my_standing"]["team_name"], "My Team")


if __name__ == "__main__":
    unittest.main()
