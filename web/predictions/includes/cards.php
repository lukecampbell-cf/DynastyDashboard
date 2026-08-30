<?php

declare(strict_types=1);

const PREDICTIONS_MAX_CARD_PICKS = 6;

function predictions_normalise_market_id(mixed $marketId): string
{
    return is_string($marketId) ? trim($marketId) : '';
}

function predictions_market_path(string $leagueId, string|int $season, int $week): string
{
    if (!preg_match('/^[A-Za-z0-9_-]+$/', $leagueId) || !preg_match('/^\d{4}$/', (string) $season) || $week < 1 || $week > 22) {
        throw new DomainException('Invalid prediction market period.');
    }
    return predictions_data_directory() . '/prediction_markets/' . $season . '/week_' . $week . '/' . $leagueId . '.json';
}

function predictions_load_markets(string $leagueId, string|int $season, int $week): array
{
    $document = predictions_load_json(predictions_market_path($leagueId, $season, $week));
    if ($document === null
        || (string) ($document['league_id'] ?? '') !== $leagueId
        || (string) ($document['season'] ?? '') !== (string) $season
        || (int) ($document['week'] ?? 0) !== $week
        || !is_array($document['markets'] ?? null)) {
        throw new DomainException(sprintf(
            'Prediction markets are not available for league %s in season %s, week %d.',
            $leagueId,
            (string) $season,
            $week
        ));
    }
    return $document;
}

function predictions_market_is_locked(array $market, array $document, ?DateTimeImmutable $now = null): bool
{
    $raw = $market['lock_at'] ?? $document['lock_at'] ?? null;
    if (!is_string($raw) || trim($raw) === '') {
        return false;
    }
    try {
        $lock = new DateTimeImmutable($raw);
    } catch (Exception) {
        // A malformed authoritative lock is closed, never silently open.
        return true;
    }
    return ($now ?? new DateTimeImmutable('now', new DateTimeZone('UTC'))) >= $lock;
}

function predictions_open_market_map(array $document, ?DateTimeImmutable $now = null): array
{
    $markets = [];
    foreach ($document['markets'] as $market) {
        if (!is_array($market) || predictions_market_is_locked($market, $document, $now)) {
            continue;
        }
        $id = predictions_normalise_market_id($market['market_id'] ?? null);
        $playerId = (string) ($market['player_id'] ?? '');
        $position = (string) ($market['position'] ?? '');
        $numeric = ['line', 'heuristic_projection', 'context_adjustment', 'final_projection'];
        $validNumbers = true;
        foreach ($numeric as $field) {
            $validNumbers = $validNumbers && isset($market[$field]) && is_numeric($market[$field]) && is_finite((float) $market[$field]);
        }
        if ($id === '' || isset($markets[$id]) || $playerId === '' || !in_array($position, ['QB', 'RB', 'WR', 'TE'], true) || !$validNumbers) {
            continue;
        }
        $markets[$id] = $market;
    }
    return $markets;
}

function predictions_normalise_picks(mixed $marketIds, mixed $selections): array
{
    if (!is_array($marketIds) || !is_array($selections) || count($marketIds) !== count($selections)) {
        throw new DomainException('Choose an OVER or UNDER prediction for each selected player.');
    }
    if ($marketIds === [] || count($marketIds) > PREDICTIONS_MAX_CARD_PICKS) {
        throw new DomainException('A prediction card must contain between one and six picks.');
    }
    $picks = [];
    foreach ($marketIds as $index => $marketId) {
        $marketId = predictions_normalise_market_id($marketId);
        $selection = is_string($selections[$index] ?? null) ? strtoupper(trim($selections[$index])) : '';
        if ($marketId === '' || isset($picks[$marketId]) || !in_array($selection, ['OVER', 'UNDER'], true)) {
            throw new DomainException('The submitted prediction choices are invalid.');
        }
        $picks[$marketId] = $selection;
    }
    return $picks;
}

function predictions_submit_card(PDO $pdo, array $identity, array $league, array $document, array $picks, ?DateTimeImmutable $now = null): int
{
    $leagueId = (string) ($league['league_id'] ?? '');
    $season = (int) ($document['season'] ?? 0);
    $week = (int) ($document['week'] ?? 0);
    if ($leagueId === '' || $leagueId !== (string) ($document['league_id'] ?? '')) {
        throw new DomainException('The selected league does not match these markets.');
    }
    $openMarkets = predictions_open_market_map($document, $now);
    foreach ($picks as $marketId => $_selection) {
        if (!isset($openMarkets[$marketId])) {
            throw new DomainException('A selected market is invalid or has locked. Refresh and try again.');
        }
    }

    $submittedAt = ($now ?? new DateTimeImmutable('now', new DateTimeZone('UTC')))->setTimezone(new DateTimeZone('UTC'))->format(DATE_ATOM);
    $transactionStarted = false;
    try {
        // PDO does not mark transactions started with raw BEGIN IMMEDIATE as
        // active in every PHP/SQLite build. Keep the immediate write lock, but
        // finish the same SQL-managed transaction with explicit SQL too.
        $pdo->exec('BEGIN IMMEDIATE');
        $transactionStarted = true;
        $card = $pdo->prepare('INSERT INTO prediction_cards
            (sleeper_user_id, sleeper_username, display_name, league_id, league_name, season, week, status, submitted_at)
            VALUES (:user_id, :username, :display_name, :league_id, :league_name, :season, :week, \'submitted\', :submitted_at)');
        $card->execute([
            ':user_id' => (string) $identity['sleeper_user_id'], ':username' => (string) $identity['sleeper_username'],
            ':display_name' => (string) $identity['display_name'], ':league_id' => $leagueId,
            ':league_name' => (string) ($league['name'] ?? $document['league_name'] ?? 'Sleeper league'),
            ':season' => $season, ':week' => $week, ':submitted_at' => $submittedAt,
        ]);
        $cardId = (int) $pdo->lastInsertId();
        $prediction = $pdo->prepare('INSERT INTO predictions
            (card_id, market_id, player_id, player_name, position, nfl_team, selection, line_taken,
             heuristic_projection, context_adjustment, final_projection, model_version)
            VALUES (:card_id, :market_id, :player_id, :player_name, :position, :team, :selection, :line,
                    :heuristic, :adjustment, :final, :model_version)');
        foreach ($picks as $marketId => $selection) {
            $market = $openMarkets[$marketId];
            $prediction->execute([
                ':card_id' => $cardId, ':market_id' => $marketId, ':player_id' => (string) $market['player_id'],
                ':player_name' => (string) ($market['player_name'] ?? 'Unknown player'), ':position' => (string) $market['position'],
                ':team' => isset($market['team']) ? (string) $market['team'] : null, ':selection' => $selection,
                ':line' => (float) $market['line'], ':heuristic' => (float) $market['heuristic_projection'],
                ':adjustment' => (float) $market['context_adjustment'], ':final' => (float) $market['final_projection'],
                ':model_version' => (string) ($market['model_version'] ?? 'v0-heuristic'),
            ]);
        }
        $pdo->exec('COMMIT');
        $transactionStarted = false;
        return $cardId;
    } catch (PDOException $exception) {
        if ($transactionStarted) {
            $pdo->exec('ROLLBACK');
            $transactionStarted = false;
        }
        if ((string) $exception->getCode() === '23000' || str_contains($exception->getMessage(), 'UNIQUE constraint failed')) {
            throw new DomainException('A card has already been submitted for this league and week.');
        }
        throw $exception;
    } catch (Throwable $exception) {
        if ($transactionStarted) {
            $pdo->exec('ROLLBACK');
        }
        throw $exception;
    }
}

function predictions_find_card(PDO $pdo, string $userId, string $leagueId, int $season, int $week): ?array
{
    $statement = $pdo->prepare('SELECT * FROM prediction_cards WHERE sleeper_user_id = ? AND league_id = ? AND season = ? AND week = ?');
    $statement->execute([$userId, $leagueId, $season, $week]);
    $card = $statement->fetch();
    return is_array($card) ? $card : null;
}

function predictions_card_picks(PDO $pdo, int $cardId): array
{
    if ($cardId < 1) {
        return [];
    }
    $statement = $pdo->prepare('SELECT market_id, player_id, player_name, position, nfl_team,
        selection, line_taken, heuristic_projection, context_adjustment, final_projection,
        model_version, result, actual_points
        FROM predictions WHERE card_id = ? ORDER BY id ASC');
    $statement->execute([$cardId]);
    return $statement->fetchAll();
}

function predictions_submitted_league_ids(PDO $pdo, string $userId, int $season, int $week): array
{
    $statement = $pdo->prepare('SELECT league_id FROM prediction_cards
        WHERE sleeper_user_id = ? AND season = ? AND week = ? AND status != \'void\'');
    $statement->execute([$userId, $season, $week]);
    $leagueIds = [];
    foreach ($statement->fetchAll(PDO::FETCH_COLUMN) as $leagueId) {
        $leagueIds[(string) $leagueId] = true;
    }
    return $leagueIds;
}
