"""
Unit tests for contract_agent.py's cache-first behaviour.

The whole point of caching a player's contract in player_cache.json is to
avoid re-hitting Spotrac (via Parse Bot) on every pipeline run — contract
terms barely change, so a fresh cache entry should short-circuit before any
network call, and a lookup that *does* run must persist its result back to
disk so the next run can reuse it too. These tests exercise both directions
against a temp cache file (never the real player_cache.json) with httpx
mocked out, so a regression that silently starts ignoring the cache (or
stops saving results) fails loudly instead of just costing extra API calls.

Run directly:  ./venv/bin/python test_contract_agent.py
Or via unittest: ./venv/bin/python -m unittest test_contract_agent -v
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import sleeper_agent
import contract_agent


def iso(dt: datetime) -> str:
    return dt.isoformat()


class ContractAgentCacheTests(unittest.TestCase):
    def setUp(self):
        # Every test gets its own throwaway cache file — patch the path on
        # sleeper_agent (not contract_agent) since load_player_cache /
        # save_player_cache close over sleeper_agent's module-level constant.
        self._tmpdir = tempfile.TemporaryDirectory()
        self.cache_path = Path(self._tmpdir.name) / "player_cache.json"
        self._path_patch = patch.object(sleeper_agent, "PLAYER_CACHE_PATH", self.cache_path)
        self._path_patch.start()
        # Skip the real 0.6s throttle between lookups.
        self._sleep_patch = patch.object(contract_agent.time, "sleep", lambda *_: None)
        self._sleep_patch.start()

    def tearDown(self):
        self._sleep_patch.stop()
        self._path_patch.stop()
        self._tmpdir.cleanup()

    def write_cache(self, data: dict):
        self.cache_path.write_text(json.dumps(data))

    def read_cache(self) -> dict:
        return json.loads(self.cache_path.read_text())

    def test_fresh_cached_contract_is_reused_without_any_network_call(self):
        fresh_contract = {
            "found": True,
            "spotrac_id": "999",
            "spotrac_url": "https://www.spotrac.com/nfl/player/_/id/999/",
            "contract_span": "2022-2026",
            "contract_type": "Extension",
            "current_year": "2026",
            "final_year": "2026",
            "current_year_cap_hit": "$10,000,000",
            "current_year_cap_pct": "4.00%",
            "contract_updated_at": iso(datetime.now(timezone.utc) - timedelta(days=1)),
        }
        self.write_cache({
            "100": {
                "sleeper_player_id": "100",
                "full_name": "Test Player",
                "contract": fresh_contract,
            }
        })

        with patch.object(contract_agent.httpx, "get") as mock_get:
            mock_get.side_effect = AssertionError(
                "contract_agent hit the network for a player with a fresh cached contract"
            )
            result = contract_agent.run(
                players=[{"player_id": "100", "full_name": "Test Player", "position": "QB"}]
            )

        mock_get.assert_not_called()
        self.assertEqual(result["100"], fresh_contract)

    def test_stale_cached_contract_is_refreshed_and_persisted_to_disk(self):
        stale_contract = {
            "found": True,
            "spotrac_id": "999",
            "contract_span": "2018-2022",
            "contract_updated_at": iso(datetime.now(timezone.utc) - timedelta(days=400)),
        }
        self.write_cache({
            "100": {
                "sleeper_player_id": "100",
                "full_name": "Test Player",
                "contract": stale_contract,
            }
        })

        contract_response = MagicMock()
        contract_response.raise_for_status.return_value = None
        contract_response.json.return_value = {
            "status": "success",
            "data": {
                "tables": [
                    {
                        "title": "2024-2029Extension (CURRENT)",
                        "headers": ["Year", "Cap Hit", "Cap %"],
                        "rows": [["2026", "$12,000,000", "5.50%"]],
                    }
                ]
            },
        }

        with patch.object(contract_agent.httpx, "get", return_value=contract_response) as mock_get:
            result = contract_agent.run(
                players=[{"player_id": "100", "full_name": "Test Player", "position": "QB"}]
            )

        # A known spotrac_id skips search_players and calls get_player_contract directly.
        mock_get.assert_called_once()
        self.assertIn("get_player_contract", mock_get.call_args.args[0])

        self.assertTrue(result["100"]["found"])
        self.assertEqual(result["100"]["contract_span"], "2024-2029")
        self.assertNotEqual(result["100"]["contract_updated_at"], stale_contract["contract_updated_at"])

        # The refreshed contract must actually land back on disk, not just
        # in the in-memory return value, so the next run sees it as fresh.
        on_disk = self.read_cache()
        self.assertEqual(on_disk["100"]["contract"]["contract_span"], "2024-2029")

    def test_missing_cache_entry_searches_then_persists_to_disk(self):
        self.write_cache({})

        search_response = MagicMock()
        search_response.raise_for_status.return_value = None
        search_response.json.return_value = {
            "status": "success",
            "data": {
                "results": [
                    {"player_id": "555", "name": "Test Player", "position": "Quarterback"}
                ]
            },
        }
        contract_response = MagicMock()
        contract_response.raise_for_status.return_value = None
        contract_response.json.return_value = {
            "status": "success",
            "data": {
                "tables": [
                    {
                        "title": "2023-2027Rookie (CURRENT)",
                        "headers": ["Year", "Cap Hit", "Cap %"],
                        "rows": [["2026", "$7,000,000", "2.80%"]],
                    }
                ]
            },
        }

        def fake_get(url, headers=None, params=None, timeout=None):
            if "search_players" in url:
                return search_response
            if "get_player_contract" in url:
                return contract_response
            raise AssertionError(f"unexpected URL: {url}")

        with patch.object(contract_agent.httpx, "get", side_effect=fake_get):
            result = contract_agent.run(
                players=[{"player_id": "200", "full_name": "Test Player", "position": "QB"}]
            )

        self.assertTrue(result["200"]["found"])
        self.assertEqual(result["200"]["spotrac_id"], "555")
        self.assertEqual(result["200"]["contract_span"], "2023-2027")

        on_disk = self.read_cache()
        self.assertEqual(on_disk["200"]["contract"]["spotrac_id"], "555")


if __name__ == "__main__":
    unittest.main()
