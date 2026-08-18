"""
Unit tests for trade_value_agent.py's per-format partial-refresh preservation.

run() used to merge both value formats (sf, 1qb) into one combined result
before checking whether *anything* came back empty — so one format's clean
fetch could make the whole refresh look non-empty and let the *other*
format's failed, empty fetch silently replace its own valid cached data
(e.g. SF succeeds, 1QB fails -> 1QB's cache gets wiped). These tests prove
each format is now evaluated and preserved independently.

Run directly:  ./venv/bin/python test_trade_value_agent.py
Or via unittest: ./venv/bin/python -m unittest test_trade_value_agent -v
"""

import unittest
from unittest.mock import patch

import trade_value_agent as tva


def make_players(n: int, prefix: str = "p") -> dict:
    return {f"{prefix}{i}": {"name": f"Player {i}", "value": 100 - i} for i in range(n)}


def cached_blob(sf_players: dict, qb_players: dict) -> dict:
    return {
        "fetched_at": "2026-01-01T00:00:00+00:00",
        "source": "rosteraudit.com dynasty rankings (via Parse Bot)",
        "formats": {
            "sf": {"fetched_at": "2026-01-01T00:00:00+00:00", "players": sf_players, "tier_chart": []},
            "1qb": {"fetched_at": "2026-01-01T00:00:00+00:00", "players": qb_players, "tier_chart": []},
        },
    }


class BothFormatsSucceedTests(unittest.TestCase):
    def test_both_formats_are_replaced_with_fresh_data(self):
        fresh_by_format = {"sf": (make_players(3, "sf"), []), "1qb": (make_players(2, "qb"), [])}

        with patch.object(tva, "load_trade_values", return_value=cached_blob({"old": {}}, {"old": {}})), \
             patch.object(tva, "fetch_dynasty_rankings", side_effect=lambda fmt, **_: fresh_by_format[fmt]), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(set(result["formats"]["sf"]["players"]), {"sf0", "sf1", "sf2"})
        self.assertEqual(set(result["formats"]["1qb"]["players"]), {"qb0", "qb1"})
        self.assertEqual(result["degraded_formats"], [])
        mock_save.assert_called_once()


class BothFormatsFailTests(unittest.TestCase):
    def test_both_formats_retain_cache_and_result_is_not_saved(self):
        cached = cached_blob(make_players(3, "sf"), make_players(2, "qb"))

        with patch.object(tva, "load_trade_values", return_value=cached), \
             patch.object(tva, "fetch_dynasty_rankings", return_value=({}, [])), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(result["formats"]["sf"]["players"], cached["formats"]["sf"]["players"])
        self.assertEqual(result["formats"]["1qb"]["players"], cached["formats"]["1qb"]["players"])
        self.assertEqual(set(result["degraded_formats"]), {"sf", "1qb"})
        mock_save.assert_not_called()


class PartialFailureTests(unittest.TestCase):
    """The brief's exact motivating scenario: one format succeeds, the other fails."""

    def test_sf_succeeds_1qb_fails_retains_only_1qb_cache(self):
        cached = cached_blob(make_players(3, "old_sf"), make_players(2, "old_qb"))
        fresh_by_format = {"sf": (make_players(5, "new_sf"), []), "1qb": ({}, [])}

        with patch.object(tva, "load_trade_values", return_value=cached), \
             patch.object(tva, "fetch_dynasty_rankings", side_effect=lambda fmt, **_: fresh_by_format[fmt]), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(set(result["formats"]["sf"]["players"]), {f"new_sf{i}" for i in range(5)})
        self.assertEqual(result["formats"]["1qb"]["players"], cached["formats"]["1qb"]["players"])
        self.assertEqual(result["degraded_formats"], ["1qb"])
        mock_save.assert_called_once()

    def test_1qb_succeeds_sf_fails_retains_only_sf_cache(self):
        cached = cached_blob(make_players(3, "old_sf"), make_players(2, "old_qb"))
        fresh_by_format = {"sf": ({}, []), "1qb": (make_players(4, "new_qb"), [])}

        with patch.object(tva, "load_trade_values", return_value=cached), \
             patch.object(tva, "fetch_dynasty_rankings", side_effect=lambda fmt, **_: fresh_by_format[fmt]), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(result["formats"]["sf"]["players"], cached["formats"]["sf"]["players"])
        self.assertEqual(set(result["formats"]["1qb"]["players"]), {f"new_qb{i}" for i in range(4)})
        self.assertEqual(result["degraded_formats"], ["sf"])
        mock_save.assert_called_once()


class NoExistingCacheTests(unittest.TestCase):
    def test_failed_format_with_no_cache_stays_empty_without_crashing(self):
        fresh_by_format = {"sf": (make_players(3, "sf"), []), "1qb": ({}, [])}

        with patch.object(tva, "load_trade_values", return_value=None), \
             patch.object(tva, "fetch_dynasty_rankings", side_effect=lambda fmt, **_: fresh_by_format[fmt]), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(set(result["formats"]["sf"]["players"]), {"sf0", "sf1", "sf2"})
        self.assertEqual(result["formats"]["1qb"]["players"], {})
        self.assertEqual(result["degraded_formats"], ["1qb"])
        mock_save.assert_called_once()

    def test_both_formats_fail_with_no_cache_at_all(self):
        with patch.object(tva, "load_trade_values", return_value=None), \
             patch.object(tva, "fetch_dynasty_rankings", return_value=({}, [])), \
             patch.object(tva, "save_trade_values") as mock_save:
            result = tva.run(force=True)

        self.assertEqual(result["formats"]["sf"]["players"], {})
        self.assertEqual(result["formats"]["1qb"]["players"], {})
        self.assertEqual(set(result["degraded_formats"]), {"sf", "1qb"})
        mock_save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
