"""
Unit tests for common.py's atomic write helpers.

write_json_atomic()/write_text_atomic() exist so a process interruption
mid-write (crash, kill -9, disk full) can never leave a persistent cache or
the published dashboard HTML truncated/corrupted on disk — the write either
completes in full (via a temp-file-then-os.replace() swap) or the original
file is left untouched. These tests exercise both the happy path and a
simulated failure mid-write.

Run directly:  ./venv/bin/python test_common.py
Or via unittest: ./venv/bin/python -m unittest test_common -v
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import common


class WriteJsonAtomicTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "sub" / "data.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_creates_parent_dirs_and_writes_valid_json(self):
        common.write_json_atomic(self.path, {"a": 1, "b": [1, 2, 3]})
        self.assertTrue(self.path.exists())
        self.assertEqual(json.loads(self.path.read_text()), {"a": 1, "b": [1, 2, 3]})

    def test_no_leftover_tmp_file_after_a_successful_write(self):
        common.write_json_atomic(self.path, {"a": 1})
        leftovers = list(self.path.parent.glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_failure_mid_write_leaves_original_file_untouched(self):
        common.write_json_atomic(self.path, {"version": 1})
        original_content = self.path.read_text()

        with patch.object(common.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                common.write_json_atomic(self.path, {"version": 2, "corrupt": "should never land"})

        self.assertEqual(self.path.read_text(), original_content)

    def test_failure_mid_write_cleans_up_the_temp_file(self):
        with patch.object(common.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                common.write_json_atomic(self.path, {"a": 1})

        leftovers = list(self.path.parent.glob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_write_when_no_prior_file_exists_succeeds(self):
        self.assertFalse(self.path.exists())
        common.write_json_atomic(self.path, {"fresh": True})
        self.assertEqual(json.loads(self.path.read_text()), {"fresh": True})

    def test_sort_keys_is_honoured(self):
        common.write_json_atomic(self.path, {"z": 1, "a": 2}, sort_keys=True)
        content = self.path.read_text()
        self.assertLess(content.index('"a"'), content.index('"z"'))


class WriteTextAtomicTests(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.path = Path(self._tmpdir.name) / "index.html"

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_writes_full_text_content(self):
        common.write_text_atomic(self.path, "<html><body>hi</body></html>")
        self.assertEqual(self.path.read_text(), "<html><body>hi</body></html>")

    def test_failure_mid_write_leaves_previously_published_html_intact(self):
        common.write_text_atomic(self.path, "<html>original good page</html>")

        with patch.object(common.os, "replace", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                common.write_text_atomic(self.path, "<html>truncated garba")

        self.assertEqual(self.path.read_text(), "<html>original good page</html>")


if __name__ == "__main__":
    unittest.main()
