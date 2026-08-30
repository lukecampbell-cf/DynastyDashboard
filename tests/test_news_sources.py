import unittest
from unittest.mock import MagicMock, patch

import httpx

from dynasty_dashboard import news_sources


class FetchHtmlErrorHandlingTests(unittest.TestCase):
    @patch.object(news_sources.time, "sleep", return_value=None)
    @patch.object(news_sources.httpx, "get")
    def test_retries_transport_failure_then_returns_html(self, mock_get, _mock_sleep):
        request = httpx.Request("GET", "https://example.test")
        response = MagicMock(text="<html>ok</html>")
        response.raise_for_status.return_value = None
        mock_get.side_effect = [httpx.ConnectError("offline", request=request), response]

        self.assertEqual(news_sources.fetch_html("https://example.test", retries=1), "<html>ok</html>")
        self.assertEqual(mock_get.call_count, 2)

    @patch.object(news_sources.time, "sleep", return_value=None)
    @patch.object(news_sources.httpx, "get", side_effect=AssertionError("programmer defect"))
    def test_does_not_hide_unexpected_programmer_errors(self, _mock_get, _mock_sleep):
        with self.assertRaisesRegex(AssertionError, "programmer defect"):
            news_sources.fetch_html("https://example.test")


class ParseBotErrorHandlingTests(unittest.TestCase):
    @patch.object(news_sources, "parsebot_headers", return_value={})
    @patch.object(news_sources.httpx, "get")
    def test_invalid_json_is_reported_as_source_failure(self, mock_get, _mock_headers):
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.side_effect = ValueError("invalid JSON")
        mock_get.return_value = response

        items, error = news_sources.scrape_fantasypros_news()

        self.assertEqual(items, [])
        self.assertIn("invalid JSON", error or "")


if __name__ == "__main__":
    unittest.main()
