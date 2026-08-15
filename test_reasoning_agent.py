"""
Unit tests for reasoning_agent.py's batched analysis + prompt caching.

reasoning_agent.run() used to make one Anthropic API call per player. It now
dedupes players across leagues and sends BATCH_SIZE players per call, keyed
by Sleeper player_id, with prompt caching enabled on the shared system
prompt. These tests mock the Anthropic client entirely (no real API calls,
no cost) and check the things that actually matter for this change:
  - a batch response gets mapped back to the right players by player_id
  - a player missing from the response falls back to defaults instead of
    crashing or poisoning the rest of the batch
  - a player rostered in multiple leagues is still only analysed once
  - the system prompt is actually sent with cache_control set

Run directly:  ./venv/bin/python test_reasoning_agent.py
Or via unittest: ./venv/bin/python -m unittest test_reasoning_agent -v
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import reasoning_agent as ra


def make_player(pid: str, name: str) -> dict:
    return {
        "player_id": pid,
        "full_name": name,
        "position": "WR",
        "team": "CHI",
        "age": 25,
        "years_exp": 2,
        "draft_year": 2024,
        "injury_status": None,
        "injury_body_part": "",
        "is_starter": True,
        "is_ir": False,
        "is_taxi": False,
        "source_count": 0,
        "news_items": [],
        "contract": None,
    }


def mock_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.content = [MagicMock(text=json.dumps(payload))]
    return resp


class AnalysePlayersBatchTests(unittest.TestCase):
    def test_maps_response_back_to_players_by_id(self):
        players = [make_player("100", "Player A"), make_player("200", "Player B")]
        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "s", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
            "200": {"trend": "DOWN", "confidence": "LOW", "summary": "s2", "fantasy_impact": "NONE",
                     "recommendation": "r2", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }
        client = MagicMock()
        client.messages.create.return_value = mock_response(payload)

        results = ra.analyse_players_batch(client, players)

        self.assertEqual(results["100"]["trend"], "UP")
        self.assertEqual(results["200"]["trend"], "DOWN")
        client.messages.create.assert_called_once()

    def test_missing_player_id_in_response_is_simply_absent(self):
        players = [make_player("100", "Player A"), make_player("200", "Player B")]
        # Model only returned one of the two expected players.
        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "s", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }
        client = MagicMock()
        client.messages.create.return_value = mock_response(payload)

        results = ra.analyse_players_batch(client, players)

        self.assertIn("100", results)
        self.assertNotIn("200", results)

    def test_unexpected_extra_id_in_response_is_dropped(self):
        players = [make_player("100", "Player A")]
        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "s", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
            "999": {"trend": "UP", "confidence": "HIGH", "summary": "hallucinated", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }
        client = MagicMock()
        client.messages.create.return_value = mock_response(payload)

        results = ra.analyse_players_batch(client, players)

        self.assertEqual(set(results.keys()), {"100"})

    def test_malformed_json_after_retries_returns_empty_dict_not_raise(self):
        players = [make_player("100", "Player A")]
        client = MagicMock()
        bad_response = MagicMock()
        bad_response.content = [MagicMock(text="not json at all")]
        client.messages.create.return_value = bad_response

        with patch.object(ra.time, "sleep", lambda *_: None):
            results = ra.analyse_players_batch(client, players, retries=1)

        self.assertEqual(results, {})
        # One initial attempt + one retry.
        self.assertEqual(client.messages.create.call_count, 2)

    def test_empty_batch_short_circuits_without_a_call(self):
        client = MagicMock()
        results = ra.analyse_players_batch(client, [])
        self.assertEqual(results, {})
        client.messages.create.assert_not_called()

    def test_system_prompt_sent_with_cache_control(self):
        players = [make_player("100", "Player A")]
        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "s", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }
        client = MagicMock()
        client.messages.create.return_value = mock_response(payload)

        ra.analyse_players_batch(client, players)

        _, kwargs = client.messages.create.call_args
        system = kwargs["system"]
        self.assertIsInstance(system, list)
        self.assertEqual(system[0]["cache_control"], {"type": "ephemeral"})
        # No anthropic-beta header: prompt caching is GA now and the old
        # "prompt-caching-2024-07-15" beta flag gets a hard 400 if sent.
        self.assertNotIn("extra_headers", kwargs)


class RunDedupTests(unittest.TestCase):
    """Verifies run() dedupes a player rostered in multiple leagues into a single batch call."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmpdir.name) / "player_analysis_cache.json"
        self._path_patch = patch.object(ra, "CACHE_PATH", self.cache_path)
        self._path_patch.start()
        self._sleep_patch = patch.object(ra.time, "sleep", lambda *_: None)
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        self._path_patch.stop()
        self._tmpdir.cleanup()

    def test_player_in_two_leagues_is_only_analysed_once(self):
        shared_player = make_player("100", "Shared Player")
        sleeper_data = {
            "username": "test",
            "season": "2026",
            "leagues": [
                {"league_id": "L1", "league_name": "League One", "season": "2026",
                 "players": [dict(shared_player)]},
                {"league_id": "L2", "league_name": "League Two", "season": "2026",
                 "players": [dict(shared_player)]},
            ],
        }
        news_data = {"news_by_player": {}}

        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "s", "fantasy_impact": "SHORT",
                     "recommendation": "r", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response(payload)

        with patch.object(ra, "get_client", return_value=mock_client), \
             patch.object(ra, "generate_league_summary", return_value="summary"):
            result = ra.run(sleeper_data=sleeper_data, news_data=news_data)

        # Only one batch call for the shared player, even though it's on two rosters.
        mock_client.messages.create.assert_called_once()
        self.assertEqual(
            result["leagues"][0]["players"][0]["reasoning"]["trend"],
            result["leagues"][1]["players"][0]["reasoning"]["trend"],
        )
        self.assertEqual(result["leagues"][0]["players"][0]["reasoning"]["trend"], "UP")

    def test_fresh_cache_entry_skips_the_api_entirely(self):
        player = make_player("100", "Cached Player")
        sleeper_data = {
            "username": "test",
            "season": "2026",
            "leagues": [
                {"league_id": "L1", "league_name": "League One", "season": "2026",
                 "players": [player]},
            ],
        }
        news_data = {"news_by_player": {}}

        self.cache_path.write_text(json.dumps({
            "100": {
                "full_name": "Cached Player",
                "reasoning": {"trend": "WATCH", "confidence": "MEDIUM", "summary": "cached",
                               "fantasy_impact": "NONE", "recommendation": "hold",
                               "dynasty_note": None, "contract_note": None,
                               "roster_status_note": None, "flags": []},
                "last_analyzed": (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(),
            }
        }))

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = AssertionError("should not call the API for a fresh cache hit")

        with patch.object(ra, "get_client", return_value=mock_client), \
             patch.object(ra, "generate_league_summary", return_value="summary"):
            result = ra.run(sleeper_data=sleeper_data, news_data=news_data)

        mock_client.messages.create.assert_not_called()
        self.assertEqual(result["leagues"][0]["players"][0]["reasoning"]["summary"], "cached")

    def test_stale_entry_with_unchanged_signal_is_reused_without_a_call(self):
        player = make_player("100", "Quiet Player")
        sleeper_data = {
            "username": "test",
            "season": "2026",
            "leagues": [
                {"league_id": "L1", "league_name": "League One", "season": "2026",
                 "players": [player]},
            ],
        }
        news_data = {"news_by_player": {}}

        stale_but_quiet = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        self.cache_path.write_text(json.dumps({
            "100": {
                "full_name": "Quiet Player",
                "reasoning": {"trend": "WATCH", "confidence": "MEDIUM", "summary": "still the same",
                               "fantasy_impact": "NONE", "recommendation": "hold",
                               "dynasty_note": None, "contract_note": None,
                               "roster_status_note": None, "flags": []},
                "last_analyzed": stale_but_quiet,
                "signal": ra.compute_signal_fingerprint(player),
            }
        }))

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = AssertionError(
            "should not call the API when nothing changed since the last analysis"
        )

        with patch.object(ra, "get_client", return_value=mock_client), \
             patch.object(ra, "generate_league_summary", return_value="summary"):
            result = ra.run(sleeper_data=sleeper_data, news_data=news_data)

        mock_client.messages.create.assert_not_called()
        self.assertEqual(result["leagues"][0]["players"][0]["reasoning"]["summary"], "still the same")

        # The reuse should have bumped last_analyzed so it doesn't get
        # re-evaluated again immediately on the next run.
        on_disk = json.loads(self.cache_path.read_text())
        refreshed = datetime.fromisoformat(on_disk["100"]["last_analyzed"])
        self.assertGreater(refreshed, datetime.now(timezone.utc) - timedelta(minutes=1))

    def test_stale_entry_with_new_news_triggers_a_fresh_call(self):
        # reasoning_agent.run() re-derives news_items from news_by_player via
        # match_to_roster on every call (it always overwrites, never merges),
        # so the new headline has to arrive through news_data, not by setting
        # player["news_items"] directly — that would just get discarded.
        from news_agent import normalise_name

        player = make_player("100", "Player With News")
        sleeper_data = {
            "username": "test",
            "season": "2026",
            "leagues": [
                {"league_id": "L1", "league_name": "League One", "season": "2026",
                 "players": [player]},
            ],
        }
        news_data = {
            "news_by_player": {
                normalise_name("Player With News"): {
                    "items": [{"headline": "Traded to a new team", "source": "test"}],
                    "source_count": 1,
                    "has_injury_flag": False,
                    "injury_status": None,
                }
            }
        }

        # Cached signal reflects the OLD state (no news) — current player has new news.
        old_signal = ra.compute_signal_fingerprint(make_player("100", "Player With News"))
        stale = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
        self.cache_path.write_text(json.dumps({
            "100": {
                "full_name": "Player With News",
                "reasoning": {"trend": "WATCH", "confidence": "MEDIUM", "summary": "old news",
                               "fantasy_impact": "NONE", "recommendation": "hold",
                               "dynasty_note": None, "contract_note": None,
                               "roster_status_note": None, "flags": []},
                "last_analyzed": stale,
                "signal": old_signal,
            }
        }))

        payload = {
            "100": {"trend": "UP", "confidence": "HIGH", "summary": "traded", "fantasy_impact": "SHORT",
                     "recommendation": "buy", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": ["trade"]},
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response(payload)

        with patch.object(ra, "get_client", return_value=mock_client), \
             patch.object(ra, "generate_league_summary", return_value="summary"):
            result = ra.run(sleeper_data=sleeper_data, news_data=news_data)

        mock_client.messages.create.assert_called_once()
        self.assertEqual(result["leagues"][0]["players"][0]["reasoning"]["summary"], "traded")

    def test_quiet_reuse_expires_past_max_age_even_with_unchanged_signal(self):
        player = make_player("100", "Ancient Player")
        sleeper_data = {
            "username": "test",
            "season": "2026",
            "leagues": [
                {"league_id": "L1", "league_name": "League One", "season": "2026",
                 "players": [player]},
            ],
        }
        news_data = {"news_by_player": {}}

        too_old = (datetime.now(timezone.utc) - ra.QUIET_REUSE_MAX_AGE - timedelta(days=1)).isoformat()
        self.cache_path.write_text(json.dumps({
            "100": {
                "full_name": "Ancient Player",
                "reasoning": {"trend": "WATCH", "confidence": "MEDIUM", "summary": "ancient",
                               "fantasy_impact": "NONE", "recommendation": "hold",
                               "dynasty_note": None, "contract_note": None,
                               "roster_status_note": None, "flags": []},
                "last_analyzed": too_old,
                "signal": ra.compute_signal_fingerprint(player),
            }
        }))

        payload = {
            "100": {"trend": "WATCH", "confidence": "MEDIUM", "summary": "refreshed", "fantasy_impact": "NONE",
                     "recommendation": "hold", "dynasty_note": None, "contract_note": None,
                     "roster_status_note": None, "flags": []},
        }
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response(payload)

        with patch.object(ra, "get_client", return_value=mock_client), \
             patch.object(ra, "generate_league_summary", return_value="summary"):
            result = ra.run(sleeper_data=sleeper_data, news_data=news_data)

        # Even though the signal is unchanged, the entry is too old to reuse blindly.
        mock_client.messages.create.assert_called_once()
        self.assertEqual(result["leagues"][0]["players"][0]["reasoning"]["summary"], "refreshed")


class SignalFingerprintTests(unittest.TestCase):
    def test_identical_players_produce_identical_fingerprints(self):
        a = make_player("100", "Player A")
        b = make_player("100", "Player A")
        self.assertEqual(ra.compute_signal_fingerprint(a), ra.compute_signal_fingerprint(b))

    def test_new_headline_changes_the_fingerprint(self):
        quiet = make_player("100", "Player A")
        noisy = make_player("100", "Player A")
        noisy["news_items"] = [{"headline": "Something happened", "source": "test"}]
        self.assertNotEqual(ra.compute_signal_fingerprint(quiet), ra.compute_signal_fingerprint(noisy))

    def test_injury_status_change_changes_the_fingerprint(self):
        healthy = make_player("100", "Player A")
        hurt = make_player("100", "Player A")
        hurt["injury_status"] = "Questionable"
        self.assertNotEqual(ra.compute_signal_fingerprint(healthy), ra.compute_signal_fingerprint(hurt))


class LeagueSummaryModelTests(unittest.TestCase):
    def test_generate_league_summary_uses_haiku(self):
        client = MagicMock()
        client.messages.create.return_value = MagicMock(content=[MagicMock(text="A short summary.")])

        ra.generate_league_summary(client, "Test League", [])

        _, kwargs = client.messages.create.call_args
        self.assertEqual(kwargs["model"], "claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
