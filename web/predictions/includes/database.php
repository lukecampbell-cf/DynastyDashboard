<?php

declare(strict_types=1);

function predictions_database(?string $path = null): PDO
{
    $databasePath = $path ?? predictions_database_path();
    $pdo = new PDO('sqlite:' . $databasePath, null, null, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    $pdo->exec('PRAGMA foreign_keys = ON');
    $pdo->exec('PRAGMA journal_mode = WAL');
    predictions_initialise_database($pdo);
    @chmod($databasePath, 0600);
    return $pdo;
}

function predictions_initialise_database(PDO $pdo): void
{
    $pdo->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS prediction_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sleeper_user_id TEXT NOT NULL,
    sleeper_username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    league_id TEXT NOT NULL,
    league_name TEXT NOT NULL,
    season INTEGER NOT NULL,
    week INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'submitted' CHECK (status IN ('submitted', 'settled', 'void')),
    submitted_at TEXT NOT NULL,
    settled_at TEXT,
    total_points INTEGER NOT NULL DEFAULT 0,
    UNIQUE (sleeper_user_id, league_id, season, week)
)
SQL);
    $pdo->exec(<<<'SQL'
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL REFERENCES prediction_cards(id) ON DELETE RESTRICT,
    market_id TEXT NOT NULL,
    player_id TEXT NOT NULL,
    player_name TEXT NOT NULL,
    position TEXT NOT NULL,
    nfl_team TEXT,
    selection TEXT NOT NULL CHECK (selection IN ('OVER', 'UNDER')),
    line_taken REAL NOT NULL,
    heuristic_projection REAL NOT NULL,
    context_adjustment REAL NOT NULL,
    final_projection REAL NOT NULL,
    model_version TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT 'PENDING' CHECK (result IN ('PENDING', 'WIN', 'LOSS', 'PUSH', 'VOID')),
    actual_points REAL,
    UNIQUE (card_id, market_id)
)
SQL);
}
