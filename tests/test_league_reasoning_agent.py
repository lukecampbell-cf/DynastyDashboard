import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from dynasty_dashboard import league_reasoning_agent as lra


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
        self.assertEqual(kwargs["reasoning"], {"effort": "minimal"})
        self.assertEqual(kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(kwargs["text"]["format"]["strict"])
        self.assertIn("instructions", kwargs)
        self.assertIn("input", kwargs)

    def test_empty_openai_response_reports_diagnostics(self):
        client = MagicMock()
        client.responses.create.return_value = MagicMock(
            output_text="", status="incomplete", incomplete_details={"reason": "max_output_tokens"}, output=[]
        )
        payload = {"league": {"id": "L1"}, "roster": [{"id": "1"}]}
        with self.assertRaisesRegex(ValueError, "status='incomplete'.*max_output_tokens"):
            lra.analyse_league(client, "openai", "gpt-5-mini", payload)

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
        with patch.object(lra, "build_client") as constructor, self.assertLogs(lra.log, level="INFO") as logs:
            result = lra.run(sleeper, news)
        constructor.assert_not_called()
        self.assertEqual(result["leagues"][0]["summary"], "No material roster news or injury changes this cycle.")
        output = "\n".join(logs.output)
        self.assertIn("quiet league with no material signals", output)
        self.assertIn("source=quiet: No material roster news or injury changes this cycle.", output)
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
        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), patch.object(lra, "build_client", return_value=client), \
             self.assertLogs(lra.log, level="INFO") as logs:
            result = lra.run(sleeper, news)
        client.messages.create.assert_called_once()
        output = "\n".join(logs.output)
        self.assertIn("provider=anthropic model=claude-haiku-4-5", output)
        self.assertIn('"roster": [', output)
        self.assertIn('"Moves into the starting lineup"', output)
        self.assertIn("source=model: Role improved.", output)
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

    def test_provider_failure_is_not_cached_and_identical_run_retries(self):
        p = player()
        sleeper, news = inputs(p)
        news["news_by_player"] = {"test player": {"items": [{"headline": "Role news", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None}}
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("temporary outage")

        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), \
             patch.object(lra, "build_client", return_value=client), \
             self.assertLogs(lra.log, level="INFO") as logs:
            first = lra.run(sleeper, news)
            second = lra.run(sleeper, news)

        self.assertEqual(client.messages.create.call_count, 2)
        self.assertEqual(first["leagues"][0]["summary"], "Analysis unavailable; showing current roster facts.")
        self.assertEqual(second["leagues"][0]["summary"], "Analysis unavailable; showing current roster facts.")
        self.assertNotIn("L1", json.loads(lra.ANALYSIS_CACHE_PATH.read_text()))
        self.assertIn("the next run will retry", "\n".join(logs.output))

    def test_provider_or_model_switch_invalidates_identical_payload_cache(self):
        p = player()
        sleeper, news = inputs(p)
        news["news_by_player"] = {"test player": {"items": [{"headline": "Role news", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None}}
        analyse = MagicMock(return_value={"overview": "Fresh analysis.", "actions": []})

        with patch.object(lra, "build_client", return_value=MagicMock()), patch.object(lra, "analyse_league", analyse):
            with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_MODEL": "gpt-model-a"}, clear=True):
                lra.run(sleeper, news)
                lra.run(sleeper, news)  # exact match: cache hit
            with patch.dict("os.environ", {"AI_PROVIDER": "openai", "OPENAI_MODEL": "gpt-model-b"}, clear=True):
                lra.run(sleeper, news)  # model changed: fresh call
            with patch.dict("os.environ", {"AI_PROVIDER": "anthropic", "ANTHROPIC_MODEL": "claude-model-a"}, clear=True):
                lra.run(sleeper, news)  # provider changed: fresh call

        self.assertEqual(analyse.call_count, 3)
        self.assertEqual(
            [(call.args[1], call.args[2]) for call in analyse.call_args_list],
            [("openai", "gpt-model-a"), ("openai", "gpt-model-b"), ("anthropic", "claude-model-a")],
        )

    def test_failure_preserves_prior_cache_entry_exactly(self):
        p = player()
        sleeper, news = inputs(p)
        news["news_by_player"] = {"test player": {"items": [{"headline": "New role news", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None}}
        prior = {"fingerprint": "old-fingerprint", "generated_at": "2026-01-01T00:00:00+00:00",
                 "analysis": {"overview": "Previous valid analysis.", "actions": []}}
        lra.ANALYSIS_CACHE_PATH.write_text(json.dumps({"L1": prior}))
        client = MagicMock()
        client.messages.create.side_effect = RuntimeError("temporary outage")

        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), patch.object(lra, "build_client", return_value=client):
            result = lra.run(sleeper, news)

        self.assertEqual(result["leagues"][0]["summary"], "Previous valid analysis.")
        self.assertEqual(json.loads(lra.ANALYSIS_CACHE_PATH.read_text())["L1"], prior)

    def test_only_action_players_enter_watch_and_remainder_are_no_action(self):
        watched = player("1")
        watched["full_name"] = "Watched Player"
        stable = player("2")
        stable["full_name"] = "Stable Player"
        sleeper, news = inputs(watched)
        sleeper["leagues"][0]["players"] = [watched, stable]
        news["news_by_player"] = {"watched player": {
            "items": [{"headline": "Role remains uncertain", "source": "test"}],
            "source_count": 1, "has_injury_flag": False, "injury_status": None,
        }}
        response = MagicMock()
        response.content = [MagicMock(text=json.dumps({"overview": "One situation to watch.", "actions": [{
            "player_id": "1", "trend": "WATCH", "confidence": "MEDIUM", "action": "Monitor.",
            "reason": "His role remains uncertain.", "flags": ["depth_chart"]}]}))]
        client = MagicMock(); client.messages.create.return_value = response

        with patch.dict("os.environ", {"AI_PROVIDER": "anthropic"}), patch.object(lra, "build_client", return_value=client):
            result = lra.run(sleeper, news)

        league = result["leagues"][0]
        self.assertEqual([p["player_id"] for p in league["watch_list"]], ["1"])
        self.assertEqual([p["player_id"] for p in league["no_action"]], ["2"])
        self.assertEqual(league["no_action"][0]["reasoning"]["trend"], "NO_ACTION")
        self.assertEqual(league["stats"]["watch"], 1)
        self.assertEqual(league["stats"]["no_action"], 1)


if __name__ == "__main__":
    unittest.main()
