<?php

declare(strict_types=1);

// Private CLI adapter around the canonical Phase 2 PHP implementation. Keeping
// projection calculation here prevents the operator bridge from growing a
// second, subtly different Python implementation.
if (PHP_SAPI !== 'cli') {
    http_response_code(404);
    exit;
}

require dirname(__DIR__) . '/web/predictions/includes/projection.php';
require dirname(__DIR__) . '/web/predictions/includes/sleeper.php';
require dirname(__DIR__) . '/web/predictions/includes/json.php';

try {
    $request = json_decode((string) stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
    if (!is_array($request)) {
        throw new RuntimeException('Snapshot request must be a JSON object.');
    }
    $dataDirectory = rtrim((string) ($request['data_directory'] ?? ''), DIRECTORY_SEPARATOR);
    $league = $request['league'] ?? null;
    $roster = $request['roster'] ?? null;
    $sleeperPlayers = $request['sleeper_players'] ?? null;
    $season = (string) ($request['season'] ?? '');
    $week = (int) ($request['week'] ?? 0);
    if ($dataDirectory === '' || !is_array($league) || !is_array($roster) || !is_array($sleeperPlayers)
        || !preg_match('/^\d{4}$/', $season) || $week < 1 || $week > 22) {
        throw new RuntimeException('Snapshot request is incomplete or invalid.');
    }
    $directory = predictions_load_json($dataDirectory . '/player_directory.json');
    $details = predictions_load_json($dataDirectory . '/player_cache.json');
    if ($directory === null || $details === null) {
        throw new RuntimeException('player_directory.json and player_cache.json must both be readable.');
    }
    $candidates = predictions_candidate_players($roster, $sleeperPlayers);
    $format = predictions_trade_value_format($league);
    $projected = predictions_project_roster($candidates, $directory, $format, $season, $week, $details);
    $players = [];
    foreach ($candidates as $candidate) {
        $playerId = (string) $candidate['player_id'];
        if (!isset($projected[$playerId])) {
            continue;
        }
        $detail = is_array($details[$playerId] ?? null) ? $details[$playerId] : [];
        $directoryPlayer = is_array($directory[$playerId] ?? null) ? $directory[$playerId] : [];
        $projection = $projected[$playerId];
        $players[] = array_merge($candidate, [
            'age' => $detail['age'] ?? $directoryPlayer['age'] ?? null,
            'years_exp' => $detail['years_exp'] ?? $directoryPlayer['years_exp'] ?? null,
            'fp_pos_rank' => $detail['fp_pos_rank'] ?? null,
            'trade_value' => predictions_numeric_value($directoryPlayer['values'][$format]['value'] ?? null),
            'trade_value_percentile' => $projection['trade_value_percentile'],
            'roster_designation' => $projection['roster_role'],
            'news_injury_status' => $detail['news_injury_status'] ?? null,
            'news_items' => is_array($detail['news_items'] ?? null) ? $detail['news_items'] : [],
            'heuristic_projection' => $projection['heuristic_projection'],
            'components' => $projection['components'],
            'model_version' => $projection['model_version'],
            'input_hash' => $projection['input_hash'],
        ]);
    }
    echo json_encode([
        'season' => $season,
        'week' => $week,
        'league_id' => (string) ($league['league_id'] ?? ''),
        'league_name' => (string) ($league['name'] ?? 'Sleeper league'),
        'players' => $players,
    ], JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR), "\n";
} catch (Throwable $exception) {
    fwrite(STDERR, 'Snapshot build failed: ' . $exception->getMessage() . "\n");
    exit(1);
}
