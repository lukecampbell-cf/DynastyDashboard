<?php

declare(strict_types=1);

require_once __DIR__ . '/projection_config.php';

function predictions_numeric_value(mixed $value): ?float
{
    if (!is_int($value) && !is_float($value) && !(is_string($value) && is_numeric(trim($value)))) {
        return null;
    }
    $number = (float) $value;
    return is_finite($number) ? $number : null;
}

function predictions_positional_rank(mixed $rank, string $position): ?float
{
    if (is_string($rank)) {
        $normalised = strtoupper(trim($rank));
        if (preg_match('/^' . preg_quote(strtoupper($position), '/') . '\s*[-#]?\s*(\d+(?:\.\d+)?)$/', $normalised, $matches)) {
            return (float) $matches[1];
        }
    }
    return predictions_numeric_value($rank);
}

function predictions_position_trade_percentiles(array $directory, string $format = 'sf'): array
{
    $format = $format === '1qb' ? '1qb' : 'sf';
    $groups = [];
    foreach ($directory as $playerId => $player) {
        if (!is_array($player)) {
            continue;
        }
        $position = strtoupper((string) ($player['position'] ?? ''));
        $value = predictions_numeric_value($player['values'][$format]['value'] ?? null);
        if (!in_array($position, ['QB', 'RB', 'WR', 'TE'], true) || $value === null || $value <= 0.0) {
            continue;
        }
        $groups[$position][(string) $playerId] = $value;
    }

    $percentiles = [];
    foreach ($groups as $players) {
        if (count($players) < 2) {
            foreach ($players as $playerId => $_value) {
                $percentiles[$playerId] = null;
            }
            continue;
        }
        asort($players, SORT_NUMERIC);
        $sortedValues = array_values($players);
        $denominator = count($sortedValues) - 1;
        foreach ($players as $playerId => $value) {
            $less = 0;
            $equal = 0;
            foreach ($sortedValues as $comparison) {
                $less += $comparison < $value ? 1 : 0;
                $equal += $comparison === $value ? 1 : 0;
            }
            // Midrank makes ties deterministic and independent of JSON ordering.
            $percentiles[$playerId] = round(100.0 * ($less + (($equal - 1) / 2)) / $denominator, 4);
        }
    }
    return $percentiles;
}

function predictions_rank_adjustment(string $position, mixed $rank, ?array $config = null): float
{
    $numericRank = predictions_positional_rank($rank, $position);
    if ($numericRank === null || $numericRank <= 0) {
        return 0.0;
    }
    $config ??= predictions_projection_config();
    foreach ($config['rank_bands'][$position] ?? [] as [$maximum, $adjustment]) {
        if ($numericRank <= $maximum) {
            return (float) $adjustment;
        }
    }
    return 0.0;
}

function predictions_trade_value_adjustment(?float $percentile, ?array $config = null): float
{
    if ($percentile === null) {
        return 0.0;
    }
    $config ??= predictions_projection_config();
    foreach ($config['trade_value_bands'] as [$upperExclusive, $adjustment]) {
        if ($percentile < $upperExclusive) {
            return (float) $adjustment;
        }
    }
    return 0.0;
}

function predictions_roster_role(array $player): string
{
    $designation = strtolower(trim((string) ($player['roster_designation'] ?? '')));
    if (($player['is_taxi'] ?? false) || ($player['is_ir'] ?? false) || preg_match('/(taxi|reserve|bench|fringe|deep)/', $designation)) {
        return 'fringe';
    }
    if (($player['is_starter'] ?? false) || preg_match('/^(qb|rb|wr|te)1$/', $designation) || str_contains($designation, 'starter')) {
        return 'starter';
    }
    return 'middle';
}

function predictions_injury_adjustment(mixed $status, ?array $config = null): ?float
{
    $normalised = strtolower(trim((string) ($status ?? '')));
    $config ??= predictions_projection_config();
    if (in_array($normalised, $config['ineligible_injury_statuses'], true)) {
        return null;
    }
    return (float) ($config['injury_adjustments'][$normalised] ?? 0.0);
}

function predictions_heuristic_projection(array $player, ?float $tradePercentile = null): ?array
{
    $config = predictions_projection_config();
    $position = strtoupper((string) ($player['position'] ?? ''));
    $playerId = trim((string) ($player['player_id'] ?? $player['sleeper_player_id'] ?? ''));
    if ($playerId === '' || !isset($config['position_baselines'][$position])) {
        return null;
    }
    $injury = predictions_injury_adjustment($player['injury_status'] ?? null, $config);
    if ($injury === null) {
        return null;
    }
    $role = predictions_roster_role($player);
    $components = [
        'position_baseline' => (float) $config['position_baselines'][$position],
        'rank_adjustment' => predictions_rank_adjustment($position, $player['fp_pos_rank'] ?? null, $config),
        'trade_value_adjustment' => predictions_trade_value_adjustment($tradePercentile, $config),
        'roster_role_adjustment' => (float) $config['roster_role_adjustments'][$role],
        'injury_adjustment' => $injury,
    ];
    return [
        'model_version' => PREDICTIONS_PROJECTION_MODEL_VERSION,
        'player_id' => $playerId,
        'position' => $position,
        'trade_value_percentile' => $tradePercentile,
        'roster_role' => $role,
        'components' => $components,
        'heuristic_projection' => round(array_sum($components), 2),
    ];
}

function predictions_weekly_input_hash(array $player, ?float $tradePercentile, string $season, int $week): string
{
    $material = [
        'season' => $season,
        'week' => $week,
        'player_id' => (string) ($player['player_id'] ?? $player['sleeper_player_id'] ?? ''),
        'position' => strtoupper((string) ($player['position'] ?? '')),
        'fp_pos_rank' => predictions_positional_rank($player['fp_pos_rank'] ?? null, (string) ($player['position'] ?? '')),
        'trade_value' => predictions_numeric_value($player['trade_value'] ?? null),
        'trade_value_percentile' => $tradePercentile,
        'roster_designation' => (string) ($player['roster_designation'] ?? ''),
        'is_starter' => (bool) ($player['is_starter'] ?? false),
        'is_taxi' => (bool) ($player['is_taxi'] ?? false),
        'is_ir' => (bool) ($player['is_ir'] ?? false),
        'injury_status' => strtolower(trim((string) ($player['injury_status'] ?? ''))),
        'news_injury_status' => strtolower(trim((string) ($player['news_injury_status'] ?? ''))),
        'news_items' => is_array($player['news_items'] ?? null) ? $player['news_items'] : [],
        'model_version' => PREDICTIONS_PROJECTION_MODEL_VERSION,
    ];
    return hash('sha256', json_encode($material, JSON_THROW_ON_ERROR | JSON_PRESERVE_ZERO_FRACTION));
}

function predictions_trade_value_format(array $league): string
{
    $positions = array_map(static fn (mixed $position): string => strtoupper((string) $position), $league['roster_positions'] ?? []);
    return in_array('SUPER_FLEX', $positions, true) ? 'sf' : '1qb';
}

function predictions_project_roster(array $candidates, array $directory, string $format, string $season, int $week, array $details = []): array
{
    $percentiles = predictions_position_trade_percentiles($directory, $format);
    $projected = [];
    foreach ($candidates as $candidate) {
        $playerId = (string) ($candidate['player_id'] ?? '');
        $directoryPlayer = is_array($directory[$playerId] ?? null) ? $directory[$playerId] : [];
        $detail = is_array($details[$playerId] ?? null) ? $details[$playerId] : [];
        $tradeValue = predictions_numeric_value($directoryPlayer['values'][$format]['value'] ?? null);
        $enriched = array_merge($detail, $candidate, [
            'trade_value' => $tradeValue,
            // A caller-provided/live positional rank wins; never substitute overall rank.
            'fp_pos_rank' => $candidate['fp_pos_rank'] ?? $detail['fp_pos_rank'] ?? null,
        ]);
        $percentile = $percentiles[$playerId] ?? null;
        $projection = predictions_heuristic_projection($enriched, $percentile);
        if ($projection === null) {
            continue;
        }
        $projection['input_hash'] = predictions_weekly_input_hash($enriched, $percentile, $season, $week);
        $projected[$playerId] = $projection;
    }
    return $projected;
}
