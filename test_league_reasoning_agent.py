import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import league_reasoning_agent as lra


def player(pid="1", news=False):
    value = {"player_id": pid, "full_name": "Test Player", "position": "WR", "team": "CHI",
             "age": 24, "draft_year": 2024, "trade_value": "Mid 2nd",
             "roster_designation": "WR2", "is_starter": True, "is_ir": False,
             "is_taxi": False, "injury_status": None, "contract": None}
    if news:
        value.update({"news_items": [{"headline": "Moves into the starting lineup", "source": "test"}],
                      "source_count": 1, "has_injury_flag": False})
    return value


def inputs(p):
    return ({"username": "u", "season": "2026", "leagues": [{"league_id": "L1",
        "league_name": "League", "season": "2026", "ranking_format": "dynasty", "players": [p]}]},
        {"news_by_player": {}})


class CompactPayloadTests(unittest.TestCase):
    def test_provider_models_have_defaults_and_overrides(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(lra.provider_name(), "openai")
            self.assertEqual(lra.provider_model("anthropic"), "claude-haiku-4-5")
            self.assertEqual(lra.provider_model("openai"), "gpt-5-mini")
        with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_MODEL": "gpt-custom"}, clear=True):
            self.assertEqual(lra.provider_name(), "openai")
            self.assertEqual(lra.provider_model("openai"), "gpt-custom")

    def test_openai_responses_api_shape(self):
        client = MagicMock()
        client.responses.create.return_value = MagicMock(output_text='{"overview":"Good.","actions":[]}')
        payload = {"league": {"id": "L1"}, "roster": [{"id": "1"}]}
        result = lra.analyse_league(client, "openai", "gpt-5-mini", payload)
        self.assertEqual(result["overview"], "Good.")
        _, kwargs = client.responses.create.call_args
        self.assertEqual(kwargs["model"], "gpt-5-mini")
        self.assertEqual(kwargs["max_output_tokens"], 900)
        self.assertIn("instructions", kwargs)
        self.assertIn("input", kwargs)

    def test_news_is_deduplicated_and_capped(self):
        p = player(news=True)
        p["news_items"] = [
            {"headline": "Same headline", "source": "a"},
            {"headline": "Same headline", "source": "b"},
            {"headline": "Second", "source": "c"},
            {"headline": "Third", "source": "d"},
        ]
        self.assertEqual([n["h"] for n in lra.canonical_player(p)["news"]], ["Same headline", "Second"])

    def test_stable_player_omits_news_fields_from_model_payload(self):
        p = player()
        payload = lra.build_model_payload(inputs(p)[0]["leagues"][0], [p])
        self.assertNotIn("news", payload["roster"][0])
        self.assertNotIn("inj", payload["roster"][0])


class LeagueRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [
            patch.object(lra, "PLAYER_STORE_PATH", root / "players.json"),
            patch.object(lra, "SNAPSHOT_DIR", root / "snapshots"),
            patch.object(lra, "ANALYSIS_CACHE_PATH", root / "analysis.json"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_no_signal_makes_no_model_call_and_writes_local_views(self):
        sleeper, news = inputs(player())
        with patch.object(lra, "build_client") as constructor:
            result = lra.run(sleeper, news)
        constructor.assert_not_called()
        self.assertEqual(result["leagues"][0]["summary"], "No material roster news or injury changes this cycle.")
        self.assertTrue(lra.PLAYER_STORE_PATH.exists())
        self.assertTrue((lra.SNAPSHOT_DIR / "L1.json").exists())

    def test_changed_league_uses_one_call_and_maps_only_returned_action(self):
        p = player()
        sleeper, news = inputs(p)
        news["news_by_player"] = {"test player": {"items": [{"headline": "Moves into the starting lineup", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None}}
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps({"overview": "Role improved.", "actions": [{
            "player_id": "1", "trend": "UP", "confidence": "MEDIUM", "action": "Hold.",
            "reason": "He moved into the starting lineup.", "flags": ["depth_chart"]}]}))]
        client = MagicMock(); client.messages.create.return_value = response
        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), patch.object(lra, "build_client", return_value=client):
            result = lra.run(sleeper, news)
        client.messages.create.assert_called_once()
        reasoning = result["leagues"][0]["players"][0]["reasoning"]
        self.assertEqual(reasoning["trend"], "UP")
        self.assertEqual(reasoning["summary"], "He moved into the starting lineup.")

    def test_identical_second_run_reuses_league_cache(self):
        p = player()
        sleeper, news = inputs(p)
        news["news_by_player"] = {"test player": {"items": [{"headline": "Role news", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None}}
        response = MagicMock(); response.content = [MagicMock(text='{"overview":"Watch role.","actions":[]}')]
        client = MagicMock(); client.messages.create.return_value = response
        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), patch.object(lra, "build_client", return_value=client):
            lra.run(sleeper, news)
            lra.run(sleeper, news)
        client.messages.create.assert_called_once()


if __name__ == "__main__":
    unittest.main()
