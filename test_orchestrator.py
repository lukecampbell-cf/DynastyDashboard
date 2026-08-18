"""
Unit tests for orchestrator.run_pipeline()'s externally-observable contract:
which failures are fatal vs. degraded-but-continuing, and what actually
reaches disk when a later stage fails. Every agent module's run() is mocked
out — no real network calls, no real file writes outside a temp dir — and
health_agent.record_run() is mocked too so these tests never touch the
real project's health.json.

Run directly:  ./venv/bin/python test_orchestrator.py
Or via unittest: ./venv/bin/python -m unittest test_orchestrator -v
"""

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import common
import orchestrator


def make_sleeper_data(trade_values_degraded=None) -> dict:
    return {
        "username": "test",
        "season": "2026",
        "user": {"user_id": "u1"},
        "leagues": [
            {"league_id": "L1", "league_name": "League One", "season": "2026",
             "roster_id": 1, "record": {}, "players": [], "total_players": 0,
             "settings": {}, "scoring_settings": {}, "ranking_format": "dynasty"},
        ],
        "trade_values_degraded": trade_values_degraded or [],
    }


def make_news_data() -> dict:
    return {
        "total_items": 0, "unique_players": 0, "news_by_player": {}, "all_items": [],
        "source_status": {}, "scraped_at": datetime.now(timezone.utc).isoformat(),
    }


def make_reasoning_data() -> dict:
    return {
        "username": "test", "season": "2026", "leagues": [],
        "global_trends": {"trending_up": [], "trending_down": [], "watch_list": []},
    }


class SleeperFailureTests(unittest.TestCase):
    def test_sleeper_failure_is_fatal(self):
        with patch.object(orchestrator.sleeper_agent, "run", side_effect=RuntimeError("Sleeper API down")), \
             patch.object(orchestrator.health_agent, "record_run") as mock_record, \
             patch.object(orchestrator.contract_agent, "run") as mock_contract, \
             patch.object(orchestrator.reasoning_agent, "run") as mock_reasoning, \
             patch.object(orchestrator.dashboard_agent, "run") as mock_dashboard:
            result = orchestrator.run_pipeline(dry_run=True)

        self.assertFalse(result["success"])
        sleeper_stage = next(s for s in result["stages"] if s["name"] == "sleeper")
        self.assertFalse(sleeper_stage["success"])
        mock_record.assert_called_once()
        self.assertFalse(mock_record.call_args.args[1])  # pipeline_success arg
        mock_contract.assert_not_called()
        mock_reasoning.assert_not_called()
        mock_dashboard.assert_not_called()


class ContractDegradedTests(unittest.TestCase):
    def test_contract_failure_is_degraded_not_fatal(self):
        with patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data()), \
             patch.object(orchestrator.contract_agent, "run", side_effect=RuntimeError("Spotrac down")), \
             patch.object(orchestrator.news_agent, "run", return_value=make_news_data()), \
             patch.object(orchestrator.reasoning_agent, "run", return_value=make_reasoning_data()), \
             patch.object(orchestrator.dashboard_agent, "run", return_value=True), \
             patch.object(orchestrator.health_agent, "record_run") as mock_record:
            result = orchestrator.run_pipeline(dry_run=True)

        self.assertTrue(result["success"])
        contract_stage = next(s for s in result["stages"] if s["name"] == "contract")
        self.assertTrue(contract_stage["success"])
        self.assertTrue(contract_stage["degraded"])
        self.assertTrue(mock_record.call_args.args[1])


class NewsDegradedTests(unittest.TestCase):
    def test_news_failure_continues_with_empty_news_data(self):
        with patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data()), \
             patch.object(orchestrator.contract_agent, "run", return_value={}), \
             patch.object(orchestrator.news_agent, "run", side_effect=RuntimeError("all scrapers down")), \
             patch.object(orchestrator.reasoning_agent, "run", return_value=make_reasoning_data()) as mock_reasoning, \
             patch.object(orchestrator.dashboard_agent, "run", return_value=True), \
             patch.object(orchestrator.health_agent, "record_run"):
            result = orchestrator.run_pipeline(dry_run=True)

        self.assertTrue(result["success"])
        news_stage = next(s for s in result["stages"] if s["name"] == "news")
        self.assertTrue(news_stage["degraded"])
        # Reasoning still ran, against empty (not missing) news data.
        mock_reasoning.assert_called_once()
        self.assertEqual(mock_reasoning.call_args.kwargs["news_data"]["news_by_player"], {})


class ReasoningFailureTests(unittest.TestCase):
    def test_reasoning_failure_is_fatal_and_dashboard_never_runs(self):
        with patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data()), \
             patch.object(orchestrator.contract_agent, "run", return_value={}), \
             patch.object(orchestrator.news_agent, "run", return_value=make_news_data()), \
             patch.object(orchestrator.reasoning_agent, "run", side_effect=RuntimeError("Claude unavailable")), \
             patch.object(orchestrator.dashboard_agent, "run") as mock_dashboard, \
             patch.object(orchestrator.health_agent, "record_run") as mock_record:
            result = orchestrator.run_pipeline(dry_run=True)

        self.assertFalse(result["success"])
        reasoning_stage = next(s for s in result["stages"] if s["name"] == "reasoning")
        self.assertFalse(reasoning_stage["success"])
        mock_dashboard.assert_not_called()
        self.assertFalse(mock_record.call_args.args[1])


class DashboardWriteFailureTests(unittest.TestCase):
    """
    dashboard_agent.run() is NOT mocked here — it runs for real against a
    temp output path, so this proves item 4's atomic-write guarantee holds
    at the orchestrator level: a write failure during the final stage must
    never touch (let alone truncate) a previously published index.html.
    """

    def test_dashboard_write_failure_leaves_prior_html_untouched(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "index.html"
            output_path.write_text("<html>previously published good page</html>")

            with patch.object(orchestrator, "DRY_RUN_PATH", str(output_path)), \
                 patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data()), \
                 patch.object(orchestrator.contract_agent, "run", return_value={}), \
                 patch.object(orchestrator.news_agent, "run", return_value=make_news_data()), \
                 patch.object(orchestrator.reasoning_agent, "run", return_value=make_reasoning_data()), \
                 patch.object(orchestrator.health_agent, "record_run"), \
                 patch.object(common.os, "replace", side_effect=OSError("disk full")):
                result = orchestrator.run_pipeline(dry_run=True)

            self.assertFalse(result["success"])
            dashboard_stage = next(s for s in result["stages"] if s["name"] == "dashboard")
            self.assertFalse(dashboard_stage["success"])
            self.assertEqual(output_path.read_text(), "<html>previously published good page</html>")


class TradeValuesDegradedStageTests(unittest.TestCase):
    """
    RosterAudit's per-format fetch happens inside sleeper_agent.run() (see
    trade_value_agent.py's own partial-refresh tests for that layer);
    trade_values_degraded is how that reaches orchestrator.py, which turns
    it into its own "trade_values" pipeline stage distinct from "sleeper" —
    e.g. SF succeeds / 1QB fails should surface as trade_values=degraded
    while sleeper itself still reports a clean success.
    """

    def test_one_degraded_format_surfaces_as_a_degraded_trade_values_stage(self):
        with patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data(trade_values_degraded=["1qb"])), \
             patch.object(orchestrator.contract_agent, "run", return_value={}), \
             patch.object(orchestrator.news_agent, "run", return_value=make_news_data()), \
             patch.object(orchestrator.reasoning_agent, "run", return_value=make_reasoning_data()), \
             patch.object(orchestrator.dashboard_agent, "run", return_value=True), \
             patch.object(orchestrator.health_agent, "record_run"):
            result = orchestrator.run_pipeline(dry_run=True)

        self.assertTrue(result["success"])
        sleeper_stage = next(s for s in result["stages"] if s["name"] == "sleeper")
        trade_values_stage = next(s for s in result["stages"] if s["name"] == "trade_values")
        self.assertFalse(sleeper_stage["degraded"])
        self.assertTrue(trade_values_stage["degraded"])
        self.assertIn("1qb", trade_values_stage["message"])

    def test_no_degraded_formats_is_a_clean_trade_values_stage(self):
        with patch.object(orchestrator.sleeper_agent, "run", return_value=make_sleeper_data()), \
             patch.object(orchestrator.contract_agent, "run", return_value={}), \
             patch.object(orchestrator.news_agent, "run", return_value=make_news_data()), \
             patch.object(orchestrator.reasoning_agent, "run", return_value=make_reasoning_data()), \
             patch.object(orchestrator.dashboard_agent, "run", return_value=True), \
             patch.object(orchestrator.health_agent, "record_run"):
            result = orchestrator.run_pipeline(dry_run=True)

        trade_values_stage = next(s for s in result["stages"] if s["name"] == "trade_values")
        self.assertFalse(trade_values_stage["degraded"])


if __name__ == "__main__":
    unittest.main()
