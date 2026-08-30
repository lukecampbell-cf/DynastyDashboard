# Predictions Phase 5: manual settlement

Predictions uses one canonical Half-PPR profile, independent of a Sleeper league's custom settings:

- Passing: 0.04 points per yard, 4 per touchdown, -2 per interception.
- Rushing: 0.1 points per yard, 6 per touchdown.
- Receiving: 0.5 per reception, 0.1 per yard, 6 per touchdown.
- Fumbles lost: -2 points.

## Weekly workflow

1. After games are final, use a trusted fantasy-statistics site that can display this exact profile, or collect official/reputable box-score statistics and calculate the formula above. Do not copy a Sleeper score unless its scoring settings exactly match.
2. Create a private file outside the web root named `actual_scores_YYYY_WW.json`. Use canonical Sleeper player IDs. Numeric values are final canonical scores; `null` explicitly voids that player's picks. Omit players whose score is not yet available so their picks remain pending.

   ```json
   {"season": 2026, "week": 4, "scores": {"1234": 19.7, "5678": null}}
   ```

3. Back up `predictions.sqlite`, then run:

   ```bash
   PREDICTIONS_DB_PATH=/private/path/predictions.sqlite \
     python3 -m dynasty_dashboard.prediction_settlement_agent /private/path/actual_scores_2026_04.json
   ```

The command validates the filename, period, IDs and finite numeric scores before starting a transaction. It assigns WIN, LOSS, PUSH or VOID, awards 100 points per WIN, and marks a card settled only after none of its picks remain pending. Re-running the same file is safe: terminal picks and already-settled cards are unchanged. Results, history and leaderboard pages read SQLite and make no provider or LLM calls.
