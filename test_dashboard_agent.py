"""
Unit tests for dashboard_agent.py's HTML escaping.

dashboard_agent.py builds the dashboard via f-string templates rather than a
templating engine with autoescaping, so every field that ultimately traces
back to a third party (scraped news via Claude's summary/notes, Sleeper
league/player names, RosterAudit tier labels) has to be escaped explicitly.
These tests inject an XSS-shaped payload into every such field and assert
none of it survives unescaped in the rendered output.

Run directly:  ./venv/bin/python test_dashboard_agent.py
Or via unittest: ./venv/bin/python -m unittest test_dashboard_agent -v
"""

import unittest

import dashboard_agent as da

PAYLOAD = "<script>alert('xss')</script>"


def make_player(**overrides) -> dict:
    player = {
        "full_name": PAYLOAD,
        "position": PAYLOAD,
        "team": PAYLOAD,
        "age": PAYLOAD,
        "injury_status": PAYLOAD,
        "is_starter": True,
        "is_ir": False,
        "is_taxi": False,
        "source_count": 3,
        "news_items": [{"source": PAYLOAD, "headline": PAYLOAD, "url": "javascript:alert(1)"}],
        "roster_designation": PAYLOAD,
        "trade_value": PAYLOAD,
        "reasoning": {
            "trend": PAYLOAD,
            "confidence": PAYLOAD,
            "summary": PAYLOAD,
            "recommendation": PAYLOAD,
            "dynasty_note": PAYLOAD,
            "contract_note": PAYLOAD,
            "roster_status_note": PAYLOAD,
            "flags": [PAYLOAD],
        },
    }
    player.update(overrides)
    return player


def make_league(**overrides) -> dict:
    league = {
        "league_id": PAYLOAD,
        "league_name": PAYLOAD,
        "season": PAYLOAD,
        "summary": PAYLOAD,
        "stats": {"trending_up": 1, "trending_down": 0, "watch": 0, "injured": 0, "total": 1},
        "trending_up": [make_player()],
        "trending_down": [],
        "watch_list": [],
    }
    league.update(overrides)
    return league


class EscapeHelperTests(unittest.TestCase):
    def test_escapes_html_special_chars(self):
        self.assertEqual(da.esc("<script>"), "&lt;script&gt;")

    def test_escapes_quotes_for_attribute_safety(self):
        # quote=True matters here: values are spliced into double-quoted
        # attributes (e.g. class="pos-{position}") as well as text content.
        result = da.esc('"><img src=x>')
        self.assertNotIn('"', result)

    def test_none_becomes_empty_string(self):
        self.assertEqual(da.esc(None), "")

    def test_non_string_is_stringified_then_escaped(self):
        self.assertEqual(da.esc(3), "3")


class PlayerCardEscapingTests(unittest.TestCase):
    def test_no_raw_payload_in_player_card(self):
        html = da.render_player_card(make_player())
        self.assertNotIn(PAYLOAD, html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn('href="javascript:', html)

    def test_position_class_cannot_break_out_of_attribute(self):
        html = da.render_player_card(make_player(position='qb"><svg onload=alert(1)>'))
        self.assertNotIn("<svg", html)


class SafeUrlTests(unittest.TestCase):
    def test_allows_http_and_https(self):
        self.assertEqual(da.safe_url("https://rotowire.com/x"), "https://rotowire.com/x")
        self.assertEqual(da.safe_url("http://espn.com/y"), "http://espn.com/y")

    def test_rejects_javascript_scheme(self):
        # esc() alone does not neutralize this — javascript: contains no
        # HTML-special characters, so it survives escaping unchanged and
        # would still execute on click if used as-is in an href.
        self.assertIsNone(da.safe_url("javascript:alert(1)"))

    def test_rejects_data_and_vbscript_schemes(self):
        self.assertIsNone(da.safe_url("data:text/html,<script>alert(1)</script>"))
        self.assertIsNone(da.safe_url("vbscript:msgbox(1)"))

    def test_rejects_none_and_empty(self):
        self.assertIsNone(da.safe_url(None))
        self.assertIsNone(da.safe_url(""))


class SourceAttributionTests(unittest.TestCase):
    def test_dedupes_by_source_and_links_when_url_present(self):
        news_items = [
            {"source": "rotowire", "headline": "A", "url": "https://rotowire.com/a"},
            {"source": "rotowire", "headline": "B", "url": "https://rotowire.com/b"},
            {"source": "espn", "headline": "C", "url": "https://espn.com/c"},
        ]
        html = da.source_attribution_html(news_items)
        self.assertEqual(html.count("source-chip"), 2)  # deduped to 2 sources
        self.assertIn('href="https://rotowire.com/a"', html)  # first item's URL wins

    def test_no_url_renders_non_clickable_chip(self):
        html = da.source_attribution_html([{"source": "fantasypros_injuries", "headline": "Q", "url": None}])
        self.assertIn('class="source-chip no-link"', html)
        self.assertNotIn("<a ", html)

    def test_javascript_url_does_not_become_a_link(self):
        html = da.source_attribution_html([{"source": "rotowire", "headline": "x", "url": "javascript:alert(1)"}])
        self.assertNotIn("<a ", html)
        self.assertIn("no-link", html)

    def test_empty_news_items_renders_nothing(self):
        self.assertEqual(da.source_attribution_html([]), "")

    def test_unknown_source_falls_back_to_raw_key_escaped(self):
        html = da.source_attribution_html([{"source": "some_new_site", "headline": "x", "url": None}])
        self.assertIn("some_new_site", html)


class FullDashboardEscapingTests(unittest.TestCase):
    def test_no_raw_payload_anywhere_in_rendered_dashboard(self):
        mock_data = {
            "username": PAYLOAD,
            "season": PAYLOAD,
            "leagues": [make_league()],
            "global_trends": {"trending_up": [], "trending_down": [], "watch_list": []},
        }
        html = da.render_html(mock_data)
        self.assertNotIn(PAYLOAD, html)
        self.assertGreater(html.count("&lt;script&gt;"), 0)

    def test_league_nav_link_escapes_name(self):
        html = da.render_league_nav([make_league()])
        self.assertNotIn(PAYLOAD, html)


if __name__ == "__main__":
    unittest.main()
