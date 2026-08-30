from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parent.parent


class RepositoryLayoutTests(unittest.TestCase):
    def test_application_sources_do_not_return_to_repository_root(self) -> None:
        root_sources = sorted(
            path.name
            for path in PROJECT_ROOT.iterdir()
            if path.is_file() and path.suffix in {".py", ".php"}
        )
        self.assertEqual(root_sources, [])

    def test_expected_namespaces_exist(self) -> None:
        self.assertTrue((PROJECT_ROOT / "dynasty_dashboard" / "__init__.py").is_file())
        self.assertTrue((PROJECT_ROOT / "tests" / "__init__.py").is_file())
        self.assertTrue((PROJECT_ROOT / "web" / "trade_calculator.php").is_file())


if __name__ == "__main__":
    unittest.main()

