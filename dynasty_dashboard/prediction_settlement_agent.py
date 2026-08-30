"""Settle submitted prediction cards from a private weekly Half-PPR score file."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
from pathlib import Path

from .paths import PROJECT_ROOT

SCORE_FILE_RE = re.compile(r"^actual_scores_(\d{4})_(\d{2})\.json$")


def load_actual_scores(path: Path) -> tuple[int, int, dict[str, float | None]]:
    match = SCORE_FILE_RE.fullmatch(path.name)
    if match is None:
        raise ValueError("Score filename must be actual_scores_YYYY_WW.json.")
    document = json.loads(path.read_text(encoding="utf-8"))
    season, week = int(match.group(1)), int(match.group(2))
    if not isinstance(document, dict) or document.get("season") != season or document.get("week") != week:
        raise ValueError("Score-file season/week must match its filename.")
    raw_scores = document.get("scores")
    if not isinstance(raw_scores, dict):
        raise ValueError("Score file must contain a scores object.")
    scores: dict[str, float | None] = {}
    for raw_player_id, raw_score in raw_scores.items():
        player_id = str(raw_player_id).strip()
        if not player_id:
            raise ValueError("Score player IDs must be non-empty strings.")
        if raw_score is None:
            scores[player_id] = None
            continue
        if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)) or not math.isfinite(float(raw_score)):
            raise ValueError(f"Invalid score for player {player_id}.")
        scores[player_id] = float(raw_score)
    return season, week, scores


def pick_result(selection: str, line: float, actual: float | None) -> str:
    if actual is None:
        return "VOID"
    if actual == line:
        return "PUSH"
    winning_side = "OVER" if actual > line else "UNDER"
    return "WIN" if selection == winning_side else "LOSS"


def settle(database_path: Path, score_path: Path) -> dict[str, int]:
    season, week, scores = load_actual_scores(score_path)
    counts = {"cards_completed": 0, "cards_pending": 0, "picks_settled": 0, "picks_voided": 0}
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("BEGIN IMMEDIATE")
        cards = connection.execute(
            "SELECT id FROM prediction_cards WHERE season = ? AND week = ? AND status = 'submitted'",
            (season, week),
        ).fetchall()
        for card in cards:
            picks = connection.execute(
                "SELECT id, player_id, selection, line_taken, result FROM predictions WHERE card_id = ?",
                (card["id"],),
            ).fetchall()
            for pick in picks:
                if pick["result"] != "PENDING" or pick["player_id"] not in scores:
                    continue
                actual = scores[pick["player_id"]]
                result = pick_result(pick["selection"], float(pick["line_taken"]), actual)
                connection.execute(
                    "UPDATE predictions SET result = ?, actual_points = ? WHERE id = ? AND result = 'PENDING'",
                    (result, actual, pick["id"]),
                )
                counts["picks_voided" if result == "VOID" else "picks_settled"] += 1
            totals = connection.execute(
                "SELECT COUNT(*) AS total, SUM(CASE WHEN result = 'PENDING' THEN 1 ELSE 0 END) AS pending, "
                "SUM(CASE WHEN result = 'WIN' THEN 1 ELSE 0 END) AS wins FROM predictions WHERE card_id = ?",
                (card["id"],),
            ).fetchone()
            points = int(totals["wins"] or 0) * 100
            if int(totals["total"] or 0) > 0 and int(totals["pending"] or 0) == 0:
                connection.execute(
                    "UPDATE prediction_cards SET status = 'settled', settled_at = datetime('now'), total_points = ? WHERE id = ?",
                    (points, card["id"]),
                )
                counts["cards_completed"] += 1
            else:
                connection.execute("UPDATE prediction_cards SET total_points = ? WHERE id = ?", (points, card["id"]))
                counts["cards_pending"] += 1
        connection.commit()
        return counts
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("score_file", type=Path, help="Private actual_scores_YYYY_WW.json file")
    parser.add_argument("--database", type=Path, default=None, help="Predictions SQLite path")
    args = parser.parse_args()
    data_dir = Path(os.environ.get("DASHBOARD_DATA_DIR", PROJECT_ROOT))
    database = args.database or Path(os.environ.get("PREDICTIONS_DB_PATH", data_dir / "predictions.sqlite"))
    try:
        counts = settle(database, args.score_file)
    except (OSError, ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        parser.exit(1, f"Settlement failed: {exc}\n")
    print("Settlement complete: " + ", ".join(f"{key.replace('_', ' ')}={value}" for key, value in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
