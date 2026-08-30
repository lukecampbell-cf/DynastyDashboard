from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from dynasty_dashboard import predictions_generate_markets as pgm
from dynasty_dashboard import predictions_market_agent as pma


class FakeClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses[path]


class PredictionsGenerateMarketsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self._write("authorised_users.json", {"authorised_users": [
            {"sleeper_username": "Allowed", "enabled": True},
            {"sleeper_username": "Disabled", "enabled": False},
        ]})
        self._write("player_directory.json", {})
        self._write("player_cache.json", {})

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, name, value):
        (self.root / name).write_text(json.dumps(value), encoding="utf-8")

    def args(self, **changes):
        values = dict(username="Allowed", season="2026", week=4, all_leagues=False, league_id=["L1"])
        values.update(changes)
        return argparse.Namespace(**values)

    def test_unknown_and_disabled_are_rejected_before_sleeper(self):
        client = FakeClient({})
        with patch.dict("os.environ", {"DASHBOARD_DATA_DIR": str(self.root)}):
            with self.assertRaises(pgm.GenerationError):
                pgm.run(self.args(username="Unknown"), client=client)
            with self.assertRaises(pgm.GenerationError):
                pgm.run(self.args(username="Disabled"), client=client)
        self.assertEqual(client.calls, [])

    def test_php_binary_override_and_missing_php_error(self):
        with patch("dynasty_dashboard.predictions_generate_markets.shutil.which", return_value=None):
            with self.assertRaisesRegex(pgm.GenerationError, "Configured PHP CLI is not executable"):
                pgm.resolve_php_binary("/definitely/missing/php")
        with patch("dynasty_dashboard.predictions_generate_markets.shutil.which", return_value="/usr/local/bin/php"):
            self.assertEqual(pgm.resolve_php_binary(), "/usr/local/bin/php")

    def test_rejects_unowned_league_and_finds_owned_roster(self):
        responses = {
            "user/Allowed": {"user_id": "U1", "username": "Allowed"},
            "user/U1/leagues/nfl/2026": [{"league_id": "L1", "name": "One"}],
            "players/nfl": {},
            "league/L1/rosters": [{"owner_id": "other"}, {"owner_id": "U1", "players": []}],
        }
        with self.assertRaises(pgm.GenerationError):
            pgm.resolve_inputs("Allowed", "2026", ["L2"], FakeClient(responses))
        seen = []
        def builder(_root, _season, _week, league, roster, _players):
            seen.append(roster["owner_id"])
            return {"season": "2026", "week": 4, "league_id": league["league_id"], "players": []}
        def publisher(_snapshot, root):
            return {"markets": []}, root / "prediction_markets/2026/week_4/L1.json", "skipped"
        with patch.dict("os.environ", {"DASHBOARD_DATA_DIR": str(self.root)}):
            pgm.run(self.args(), client=FakeClient(responses), snapshot_builder=builder, publisher=publisher)
        self.assertEqual(seen, ["U1"])

    def test_one_and_all_league_selection(self):
        responses = {
            "user/Allowed": {"user_id": "U1", "username": "Allowed"},
            "user/U1/leagues/nfl/2026": [{"league_id": "L1"}, {"league_id": "L2"}],
            "players/nfl": {},
            "league/L1/rosters": [{"owner_id": "U1"}], "league/L2/rosters": [{"owner_id": "U1"}],
        }
        built = []
        def builder(_root, season, week, league, _roster, _players):
            built.append(league["league_id"])
            return {"season": season, "week": week, "league_id": league["league_id"], "players": []}
        def publisher(snapshot, root):
            path = root / "prediction_markets" / "2026" / "week_4" / f"{snapshot['league_id']}.json"
            return {"markets": []}, path, "skipped"
        with patch.dict("os.environ", {"DASHBOARD_DATA_DIR": str(self.root)}):
            pgm.run(self.args(), client=FakeClient(responses), snapshot_builder=builder, publisher=publisher)
            self.assertEqual(built, ["L1"])
            built.clear()
            pgm.run(self.args(all_leagues=True, league_id=None), client=FakeClient(responses), snapshot_builder=builder, publisher=publisher)
        self.assertEqual(built, ["L1", "L2"])

    def test_atomic_failure_preserves_existing_market_and_exact_override_path(self):
        final = self.root / "prediction_markets/2026/week_4/L1.json"
        final.parent.mkdir(parents=True)
        final.write_text('{"old":true}', encoding="utf-8")
        snapshot = {"season": "2026", "week": 4, "league_id": "L1", "players": []}
        def broken(*_args, **_kwargs):
            raise pgm.GenerationError("provider failed")
        with self.assertRaises(pgm.GenerationError):
            pgm.publish_one(snapshot, self.root, generator=broken)
        self.assertEqual(final.read_text(encoding="utf-8"), '{"old":true}')
        def successful(roster, *, cache_path, markets_dir):
            document = {"season": roster["season"], "week": roster["week"], "league_id": "L1", "markets": []}
            path = markets_dir / "2026/week_4/L1.json"
            pgm.write_json_atomic(path, document)
            return document
        _, path, _ = pgm.publish_one(snapshot, self.root, generator=successful)
        self.assertEqual(path, final)
        self.assertEqual(json.loads(final.read_text())["league_id"], "L1")

    def test_nonblocking_per_league_lock(self):
        path = self.root / "lock"
        with pgm.generation_lock(path):
            with self.assertRaises(pgm.GenerationError):
                with pgm.generation_lock(path):
                    pass

    def test_snapshot_matches_canonical_php_projection_and_schema(self):
        try:
            php_binary = pgm.resolve_php_binary()
        except pgm.GenerationError:
            self.skipTest("PHP CLI is not available")

        directory = {
            "wr-low": {"position": "WR", "values": {"sf": {"value": 10}}},
            "wr-mid-a": {"position": "WR", "values": {"sf": {"value": 20}}},
            "wr-mid-b": {"position": "WR", "values": {"sf": {"value": 20}}},
            "wr-high": {"position": "WR", "values": {"sf": {"value": 40}}},
        }
        details = {"wr-high": {"fp_pos_rank": 6, "age": 25, "years_exp": 3, "news_items": []}}
        self._write("player_directory.json", directory)
        self._write("player_cache.json", details)
        league = {"league_id": "L1", "name": "Fixture", "roster_positions": ["QB", "SUPER_FLEX"]}
        roster = {"owner_id": "U1", "players": ["wr-high"], "starters": ["wr-high"]}
        sleeper_players = {"wr-high": {"full_name": "Fixture WR", "position": "WR", "team": "GB",
                                        "injury_status": "Questionable"}}
        snapshot = pgm.build_snapshot(
            self.root,
            "2026",
            1,
            league,
            roster,
            sleeper_players,
            php_binary=php_binary,
        )
        self.assertEqual((snapshot["season"], snapshot["week"], snapshot["league_id"], snapshot["league_name"]),
                         ("2026", 1, "L1", "Fixture"))
        projected = snapshot["players"][0]
        self.assertEqual(projected["heuristic_projection"], 15.5)
        self.assertEqual(projected["components"]["rank_adjustment"], 5)
        self.assertEqual(projected["model_version"], "v0-heuristic")
        self.assertRegex(projected["input_hash"], r"^[0-9a-f]{64}$")

    def test_unchanged_publish_reuses_cache_without_second_analysis(self):
        calls = []
        snapshot = {"season": "2026", "week": 4, "league_id": "L1", "league_name": "One", "players": [{
            "player_id": "p1", "full_name": "Player One", "position": "WR", "team": "GB",
            "heuristic_projection": 12.0, "input_hash": "same", "components": {}, "model_version": "v0-heuristic",
        }]}
        def analyser(stale):
            calls.append([p["player_id"] for p in stale])
            return {"p1": {"role_score": 50, "player_quality_score": 50, "risk_score": 50,
                           "projection_adjustment": 0, "confidence": .5, "role_trend": "steady",
                           "market_interest_score": 50, "summary": "Stable."}}
        def generator(roster, *, cache_path, markets_dir):
            return pma.generate_markets(roster, cache_path=cache_path, markets_dir=markets_dir, analyser=analyser)
        _, first_path, first_status = pgm.publish_one(snapshot, self.root, generator=generator)
        _, second_path, second_status = pgm.publish_one(snapshot, self.root, generator=generator)
        self.assertEqual(calls, [["p1"]])
        self.assertEqual((first_status, second_status), ("generated", "reused"))
        self.assertEqual(first_path, second_path)


if __name__ == "__main__":
    unittest.main()
