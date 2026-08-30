import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from dynasty_dashboard.prediction_settlement_agent import load_actual_scores, pick_result, settle


def make_database(path):
    with closing(sqlite3.connect(path)) as db:
        db.executescript("""
        CREATE TABLE prediction_cards (id INTEGER PRIMARY KEY, season INTEGER, week INTEGER, status TEXT,
          settled_at TEXT, total_points INTEGER DEFAULT 0);
        CREATE TABLE predictions (id INTEGER PRIMARY KEY, card_id INTEGER, player_id TEXT, selection TEXT,
          line_taken REAL, result TEXT DEFAULT 'PENDING', actual_points REAL);
        INSERT INTO prediction_cards VALUES (1, 2026, 4, 'submitted', NULL, 0);
        INSERT INTO predictions VALUES (1, 1, 'over', 'OVER', 10.5, 'PENDING', NULL);
        INSERT INTO predictions VALUES (2, 1, 'under', 'UNDER', 10.5, 'PENDING', NULL);
        INSERT INTO predictions VALUES (3, 1, 'push', 'OVER', 10.5, 'PENDING', NULL);
        INSERT INTO predictions VALUES (4, 1, 'void', 'UNDER', 10.5, 'PENDING', NULL);
        """)
        db.commit()


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def test_results(self):
        self.assertEqual(pick_result('OVER', 10.5, 11), 'WIN')
        self.assertEqual(pick_result('UNDER', 10.5, 11), 'LOSS')
        self.assertEqual(pick_result('OVER', 10.5, 10.5), 'PUSH')
        self.assertEqual(pick_result('OVER', 10.5, None), 'VOID')

    def test_settlement_is_complete_and_idempotent(self):
        database = self.root / 'predictions.sqlite'; make_database(database)
        score_file = self.root / 'actual_scores_2026_04.json'
        score_file.write_text(json.dumps({'season': 2026, 'week': 4, 'scores': {'over': 11, 'under': 9, 'push': 10.5, 'void': None}}))
        self.assertEqual(settle(database, score_file), {'cards_completed': 1, 'cards_pending': 0, 'picks_settled': 3, 'picks_voided': 1})
        self.assertEqual(settle(database, score_file), {'cards_completed': 0, 'cards_pending': 0, 'picks_settled': 0, 'picks_voided': 0})
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('SELECT status, total_points FROM prediction_cards').fetchone(), ('settled', 200))
            self.assertEqual([row[0] for row in db.execute('SELECT result FROM predictions ORDER BY id')], ['WIN', 'WIN', 'PUSH', 'VOID'])

    def test_missing_score_stays_pending(self):
        database = self.root / 'predictions.sqlite'; make_database(database)
        score_file = self.root / 'actual_scores_2026_04.json'
        score_file.write_text(json.dumps({'season': 2026, 'week': 4, 'scores': {'over': 11}}))
        self.assertEqual(settle(database, score_file)['cards_pending'], 1)
        with closing(sqlite3.connect(database)) as db:
            self.assertEqual(db.execute('SELECT status FROM prediction_cards').fetchone()[0], 'submitted')

    def test_rejects_invalid_score(self):
        score_file = self.root / 'actual_scores_2026_04.json'
        score_file.write_text(json.dumps({'season': 2026, 'week': 4, 'scores': {'p1': '12.3'}}))
        with self.assertRaises(ValueError):
            load_actual_scores(score_file)


if __name__ == '__main__':
    unittest.main()
