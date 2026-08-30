from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from dynasty_dashboard import predictions_market_agent as pma


def player(player_id: str, projection: float, input_hash: str, name: str | None = None) -> dict:
    return {
        "player_id": player_id, "full_name": name or f"Player {player_id}", "position": "WR", "team": "GB",
        "fp_pos_rank": 10, "trade_value": 5000, "trade_value_percentile": 80,
        "roster_designation": "starter", "injury_status": None, "news_items": [],
        "heuristic_projection": projection, "input_hash": input_hash,
        "components": {"position_baseline": 10.0}, "model_version": "v0-heuristic",
    }


def analysis(player_id: str, adjustment: float, interest: float) -> dict:
    return {
        "player_id": player_id, "role_score": 75, "player_quality_score": 80,
        "risk_score": 30, "projection_adjustment": adjustment, "confidence": .7,
        "role_trend": "steady", "market_interest_score": interest, "summary": "Role is stable.",
    }


class PredictionsMarketAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.cache = root / "weekly.json"
        self.markets = root / "markets"
        self.roster = {"season": "2026", "week": 3, "league_id": "L1", "league_name": "Test",
                       "players": [player("a", 14.25, "ha"), player("b", 11.0, "hb")]}

    def tearDown(self):
        self.temp.cleanup()

    def test_one_batch_clamps_adjustments_and_orders_quick_pick(self):
        calls = []
        def analyser(stale):
            calls.append([p["player_id"] for p in stale])
            raw = {"players": [analysis("a", 9, 20), analysis("b", -1, 90)]}
            return pma.validate_analysis(raw, {"a", "b"})
        result = pma.generate_markets(self.roster, cache_path=self.cache, markets_dir=self.markets,
                                      analyser=analyser, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
        self.assertEqual(calls, [["a", "b"]])
        by_id = {m["player_id"]: m for m in result["markets"]}
        self.assertEqual(by_id["a"]["context_adjustment"], 3.0)
        self.assertEqual(by_id["a"]["final_projection"], 17.25)
        self.assertEqual(by_id["a"]["line"], 17.5)
        self.assertEqual(result["markets"][0]["player_id"], "b")
        self.assertEqual(result["quick_pick"][0], result["markets"][0]["market_id"])
        self.assertTrue((self.markets / "2026" / "week_3" / "L1.json").exists())

    def test_cache_reuses_unchanged_players_and_batches_only_stale_player(self):
        first_calls, second_calls = [], []
        def first(stale):
            first_calls.append(stale)
            return {p["player_id"]: pma.validate_analysis({"players": [analysis(p["player_id"], 1, 50)]}, {p["player_id"]})[p["player_id"]] for p in stale}
        pma.generate_markets(self.roster, cache_path=self.cache, markets_dir=self.markets, analyser=first)
        changed = {**self.roster, "players": [player("a", 14.25, "ha"), player("b", 12.0, "hb2")]}
        def second(stale):
            second_calls.append([p["player_id"] for p in stale])
            return {"b": pma.validate_analysis({"players": [analysis("b", 2, 70)]}, {"b"})["b"]}
        pma.generate_markets(changed, cache_path=self.cache, markets_dir=self.markets, analyser=second)
        self.assertEqual(len(first_calls), 1)
        self.assertEqual(second_calls, [["b"]])

    def test_failure_uses_zero_adjustment_and_is_retried(self):
        calls = 0
        def broken(_stale):
            nonlocal calls
            calls += 1
            raise RuntimeError("offline")
        first = pma.generate_markets(self.roster, cache_path=self.cache, markets_dir=self.markets, analyser=broken)
        second = pma.generate_markets(self.roster, cache_path=self.cache, markets_dir=self.markets, analyser=broken)
        self.assertEqual(calls, 2)
        self.assertTrue(all(m["context_adjustment"] == 0 for m in first["markets"]))
        self.assertEqual(first["markets"][0]["heuristic_projection"], first["markets"][0]["final_projection"])
        self.assertEqual(len(second["markets"]), 2)

    def test_anthropic_contract_is_one_multi_player_request(self):
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps({"players": [analysis("a", 1, 60), analysis("b", 0, 40)]}))]
        client = MagicMock()
        client.messages.create.return_value = response
        result = pma.analyse_batch(client, "anthropic", "claude-test", pma.build_payload(self.roster["players"], "2026", 3))
        self.assertEqual(set(result), {"a", "b"})
        client.messages.create.assert_called_once()
        request = client.messages.create.call_args.kwargs
        self.assertIn('"players":[', request["messages"][0]["content"])

    def test_quick_pick_is_capped_at_six_and_market_ids_are_stable(self):
        roster = {**self.roster, "players": [player(str(i), 10 + i / 10, f"h{i}") for i in range(8)]}
        def analyser(stale):
            return {p["player_id"]: pma.validate_analysis(
                {"players": [analysis(p["player_id"], 0, int(p["player_id"]))]}, {p["player_id"]}
            )[p["player_id"]] for p in stale}
        first = pma.generate_markets(roster, cache_path=self.cache, markets_dir=self.markets, analyser=analyser)
        second = pma.generate_markets(roster, cache_path=self.cache, markets_dir=self.markets, analyser=lambda _: self.fail("cache miss"))
        self.assertEqual(len(first["quick_pick"]), 6)
        self.assertEqual(first["quick_pick"], second["quick_pick"])


if __name__ == "__main__":
    unittest.main()
