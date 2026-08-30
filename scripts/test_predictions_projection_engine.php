<?php

declare(strict_types=1);

require dirname(__DIR__) . '/web/predictions/includes/projection.php';

function projection_expect(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

$directory = [
    'wr-low' => ['position' => 'WR', 'values' => ['sf' => ['value' => 10]]],
    'wr-mid-a' => ['position' => 'WR', 'values' => ['sf' => ['value' => 20]]],
    'wr-mid-b' => ['position' => 'WR', 'values' => ['sf' => ['value' => 20]]],
    'wr-high' => ['position' => 'WR', 'values' => ['sf' => ['value' => 40]]],
    'wr-zero' => ['position' => 'WR', 'values' => ['sf' => ['value' => 0]]],
    'qb-only' => ['position' => 'QB', 'values' => ['sf' => ['value' => 50]]],
    'rb-bad' => ['position' => 'RB', 'values' => ['sf' => ['value' => 'unknown']]],
];
$percentiles = predictions_position_trade_percentiles($directory, 'sf');
projection_expect($percentiles['wr-low'] === 0.0, 'Lowest same-position trade value should be the zeroth percentile.');
projection_expect($percentiles['wr-mid-a'] === 50.0 && $percentiles['wr-mid-b'] === 50.0, 'Tied values should receive a deterministic midrank.');
projection_expect($percentiles['wr-high'] === 100.0, 'Highest same-position trade value should be the 100th percentile.');
projection_expect(!array_key_exists('wr-zero', $percentiles), 'Zero trade values must not be treated as meaningful data.');
projection_expect(array_key_exists('qb-only', $percentiles) && $percentiles['qb-only'] === null, 'A one-player population is insufficient for a percentile.');

projection_expect(predictions_rank_adjustment('WR', 5) === 8.0, 'WR top-five rank band is incorrect.');
projection_expect(predictions_rank_adjustment('WR', 6) === 5.0, 'WR rank boundary is incorrect.');
projection_expect(predictions_rank_adjustment('WR', 'WR6') === 5.0, 'Prefixed FantasyPros positional rank was not parsed.');
projection_expect(predictions_rank_adjustment('WR', null) === 0.0, 'Missing positional rank should have zero adjustment.');
projection_expect(predictions_trade_value_adjustment(90.0) === 1.5, 'Top trade-value percentile adjustment is incorrect.');
projection_expect(predictions_trade_value_adjustment(null) === 0.0, 'Missing percentile should have zero adjustment.');

$player = [
    'player_id' => 'wr-high', 'position' => 'WR', 'fp_pos_rank' => 6, 'fp_rank' => 1,
    'is_starter' => true, 'is_taxi' => false, 'is_ir' => false, 'injury_status' => 'Questionable',
    'trade_value' => 40,
];
$projection = predictions_heuristic_projection($player, 100.0);
projection_expect($projection !== null, 'Eligible player did not receive a projection.');
projection_expect($projection['heuristic_projection'] === 15.5, 'Projection components were not summed deterministically.');
projection_expect($projection['components']['rank_adjustment'] === 5.0, 'The engine did not use fp_pos_rank.');
projection_expect(predictions_heuristic_projection(array_merge($player, ['injury_status' => 'Out']), 100.0) === null, 'OUT players should be ineligible.');

$hash = predictions_weekly_input_hash($player, 100.0, '2026', 1);
projection_expect($hash === predictions_weekly_input_hash(array_merge($player, ['full_name' => 'Cosmetic change']), 100.0, '2026', 1), 'Non-material fields changed the input hash.');
projection_expect($hash !== predictions_weekly_input_hash(array_merge($player, ['injury_status' => 'Doubtful']), 100.0, '2026', 1), 'Material injury change did not change the input hash.');
projection_expect($hash !== predictions_weekly_input_hash(array_merge($player, ['news_items' => [['headline' => 'Role changed']]]), 100.0, '2026', 1), 'Material news change did not change the input hash.');
projection_expect($hash !== predictions_weekly_input_hash($player, 100.0, '2026', 2), 'Week change did not change the input hash.');

$roster = predictions_project_roster([$player], $directory, 'sf', '2026', 1);
projection_expect(isset($roster['wr-high']['input_hash']), 'Roster projection did not include a weekly material input hash.');
projection_expect(predictions_trade_value_format(['roster_positions' => ['QB', 'SUPER_FLEX']]) === 'sf', 'Superflex league format was not detected.');
projection_expect(predictions_trade_value_format(['roster_positions' => ['QB', 'FLEX']]) === '1qb', '1QB league format was not detected.');

echo "Projection engine tests passed.\n";
