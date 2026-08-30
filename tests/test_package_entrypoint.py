import runpy
import unittest
from unittest.mock import patch


class PackageEntrypointTests(unittest.TestCase):
    def test_module_propagates_pipeline_exit_code(self) -> None:
        with patch("dynasty_dashboard.orchestrator.main", return_value=7):
            with self.assertRaises(SystemExit) as raised:
                runpy.run_module("dynasty_dashboard", run_name="__main__")

        self.assertEqual(raised.exception.code, 7)


if __name__ == "__main__":
    unittest.main()

