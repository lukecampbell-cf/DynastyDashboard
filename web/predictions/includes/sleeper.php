<?php

declare(strict_types=1);

const PREDICTIONS_SLEEPER_DEFAULT_BASE = 'https://api.sleeper.app/v1';

final class PredictionsSleeperException extends RuntimeException
{
}

function predictions_sleeper_base_url(): string
{
    $configured = getenv('PREDICTIONS_SLEEPER_API_BASE');
    return rtrim($configured !== false && $configured !== '' ? $configured : PREDICTIONS_SLEEPER_DEFAULT_BASE, '/');
}

function predictions_sleeper_request(string $path): array
{
    $url = predictions_sleeper_base_url() . '/' . ltrim($path, '/');
    $curl = curl_init($url);
    if ($curl === false) {
        throw new PredictionsSleeperException('Could not initialise the Sleeper request.');
    }

    curl_setopt_array($curl, [
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_CONNECTTIMEOUT => 4,
        CURLOPT_TIMEOUT => 10,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_HTTPHEADER => ['Accept: application/json', 'User-Agent: DynastyHQ-Predictions/1.0'],
    ]);
    $body = curl_exec($curl);
    $status = (int) curl_getinfo($curl, CURLINFO_RESPONSE_CODE);
    $error = curl_error($curl);
    curl_close($curl);

    if (!is_string($body) || $status < 200 || $status >= 300) {
        throw new PredictionsSleeperException($error !== '' ? 'Sleeper could not be reached.' : 'Sleeper returned an unexpected response.');
    }

    try {
        $decoded = json_decode($body, true, 512, JSON_THROW_ON_ERROR);
    } catch (JsonException) {
        throw new PredictionsSleeperException('Sleeper returned invalid data.');
    }

    if (!is_array($decoded)) {
        throw new PredictionsSleeperException('Sleeper returned invalid data.');
    }
    return $decoded;
}

function predictions_sleeper_user(string $username, ?callable $request = null): ?array
{
    $request ??= 'predictions_sleeper_request';
    $user = $request('/user/' . rawurlencode($username));
    return isset($user['user_id']) && is_scalar($user['user_id']) ? $user : null;
}

function predictions_sleeper_state(?callable $request = null): array
{
    $request ??= 'predictions_sleeper_request';
    $state = $request('/state/nfl');
    if (!isset($state['season']) || !is_scalar($state['season'])) {
        throw new PredictionsSleeperException('Sleeper did not return an active NFL season.');
    }
    return $state;
}

/** Return the prediction-market week represented by Sleeper's NFL state.
 *
 * Sleeper's `week` counts preseason weeks while `season_type` is `pre`.
 * Predictions markets use regular-season week numbering, so every preseason
 * state deliberately points at the upcoming regular-season Week 1 market.
 */
function predictions_market_week_from_state(array $state): int
{
    $seasonType = strtolower(trim((string) ($state['season_type'] ?? '')));
    if (in_array($seasonType, ['pre', 'preseason'], true)) {
        return 1;
    }

    $week = filter_var($state['week'] ?? null, FILTER_VALIDATE_INT);
    if ($week === false || $week < 1 || $week > 22) {
        throw new PredictionsSleeperException('Sleeper did not return a supported NFL week.');
    }
    return $week;
}

function predictions_sleeper_leagues(string $userId, string $season, ?callable $request = null): array
{
    $request ??= 'predictions_sleeper_request';
    return array_values($request('/user/' . rawurlencode($userId) . '/leagues/nfl/' . rawurlencode($season)));
}

function predictions_sleeper_rosters(string $leagueId, ?callable $request = null): array
{
    $request ??= 'predictions_sleeper_request';
    return array_values($request('/league/' . rawurlencode($leagueId) . '/rosters'));
}

function predictions_sleeper_players(?callable $request = null): array
{
    $request ??= 'predictions_sleeper_request';
    return $request('/players/nfl');
}

function predictions_find_roster(array $rosters, string $userId): ?array
{
    foreach ($rosters as $roster) {
        if (is_array($roster) && (string) ($roster['owner_id'] ?? '') === $userId) {
            return $roster;
        }
    }
    return null;
}

function predictions_candidate_players(array $roster, array $playerDirectory): array
{
    $eligiblePositions = ['QB' => true, 'RB' => true, 'WR' => true, 'TE' => true];
    $starters = array_fill_keys(array_map('strval', $roster['starters'] ?? []), true);
    $taxi = array_fill_keys(array_map('strval', $roster['taxi'] ?? []), true);
    $reserve = array_fill_keys(array_map('strval', $roster['reserve'] ?? []), true);
    $candidates = [];

    foreach (array_unique(array_map('strval', $roster['players'] ?? [])) as $playerId) {
        $player = $playerDirectory[$playerId] ?? null;
        if (!is_array($player)) {
            continue;
        }
        $position = strtoupper((string) ($player['position'] ?? ''));
        if (!isset($eligiblePositions[$position])) {
            continue;
        }
        $name = trim((string) ($player['full_name'] ?? ''));
        if ($name === '') {
            $name = trim((string) ($player['first_name'] ?? '') . ' ' . (string) ($player['last_name'] ?? ''));
        }
        $candidates[] = [
            'player_id' => $playerId,
            'full_name' => $name !== '' ? $name : 'Unknown player',
            'position' => $position,
            'team' => (string) ($player['team'] ?? 'FA'),
            'injury_status' => $player['injury_status'] ?? null,
            'is_starter' => isset($starters[$playerId]),
            'is_taxi' => isset($taxi[$playerId]),
            'is_ir' => isset($reserve[$playerId]),
        ];
    }

    usort($candidates, static function (array $a, array $b): int {
        return [$a['position'], !$a['is_starter'], $a['full_name']] <=> [$b['position'], !$b['is_starter'], $b['full_name']];
    });
    return $candidates;
}
