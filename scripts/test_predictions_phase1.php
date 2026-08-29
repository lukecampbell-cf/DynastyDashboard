<?php

declare(strict_types=1);

$testData = sys_get_temp_dir() . '/dynasty-predictions-test-' . bin2hex(random_bytes(6));
mkdir($testData, 0700);
putenv('DASHBOARD_DATA_DIR=' . $testData);
putenv('PREDICTIONS_DB_PATH=' . $testData . '/predictions.sqlite');
require dirname(__DIR__) . '/web/predictions/includes/bootstrap.php';

function expect(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

file_put_contents($testData . '/authorised_users.json', json_encode([
    'authorised_users' => [
        ['sleeper_username' => 'AllowedUser', 'display_name' => 'Allowed', 'enabled' => true],
        ['sleeper_username' => 'DisabledUser', 'display_name' => 'Disabled', 'enabled' => false],
    ],
]));

$calls = [];
$request = static function (string $path) use (&$calls): array {
    $calls[] = $path;
    return ['user_id' => 'stable-123', 'username' => 'AllowedUser'];
};

try {
    predictions_resolve_login('not-allowed', $request, $testData);
    throw new RuntimeException('Unauthorised login should fail.');
} catch (DomainException) {
    expect($calls === [], 'Unauthorised login made a Sleeper request.');
}

$identity = predictions_resolve_login(' alloweduser ', $request, $testData);
expect($identity['sleeper_user_id'] === 'stable-123', 'Stable Sleeper user ID was not retained.');
expect(count($calls) === 1, 'Authorised login should make exactly one resolution call.');

$leagues = [['league_id' => '10', 'name' => 'A'], ['league_id' => '20', 'name' => 'B']];
expect(predictions_find_league($leagues, '20')['name'] === 'B', 'League lookup failed.');
expect(predictions_find_league($leagues, '30') === null, 'Foreign league was accepted.');

$roster = ['owner_id' => 'stable-123', 'players' => ['1', '2', '3', '4'], 'starters' => ['1'], 'taxi' => ['2'], 'reserve' => ['3']];
$players = [
    '1' => ['full_name' => 'Quarter Back', 'position' => 'QB', 'team' => 'CHI'],
    '2' => ['first_name' => 'Running', 'last_name' => 'Back', 'position' => 'RB', 'team' => 'DET'],
    '3' => ['full_name' => 'Wide Receiver', 'position' => 'WR', 'team' => 'GB', 'injury_status' => 'Questionable'],
    '4' => ['full_name' => 'A Kicker', 'position' => 'K', 'team' => 'MIN'],
];
$candidates = predictions_candidate_players($roster, $players);
expect(count($candidates) === 3, 'Candidate filter did not retain only QB/RB/WR/TE.');
expect(predictions_find_roster([$roster], 'stable-123') !== null, 'Owned roster was not resolved.');
expect(predictions_find_roster([$roster], 'someone-else') === null, 'Incorrect roster owner was accepted.');

expect(predictions_market_week_from_state(['season_type' => 'pre', 'week' => 3]) === 1,
    'Sleeper preseason week must map to regular-season prediction week 1.');
expect(predictions_market_week_from_state(['season_type' => 'regular', 'week' => 3]) === 3,
    'Sleeper regular-season week must be retained.');
try {
    predictions_market_week_from_state(['season_type' => 'regular', 'week' => 0]);
    throw new RuntimeException('Unsupported Sleeper week should fail.');
} catch (PredictionsSleeperException) {
    // Expected.
}

$pdo = predictions_database();
$tables = $pdo->query("SELECT name FROM sqlite_master WHERE type = 'table'")->fetchAll(PDO::FETCH_COLUMN);
expect(in_array('prediction_cards', $tables, true), 'prediction_cards table was not created.');
expect(in_array('predictions', $tables, true), 'predictions table was not created.');

unset($pdo);
foreach (glob($testData . '/*') ?: [] as $path) {
    unlink($path);
}
rmdir($testData);
echo "Phase 1 predictions tests passed.\n";
