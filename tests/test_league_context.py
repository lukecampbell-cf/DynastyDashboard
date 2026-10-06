import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
