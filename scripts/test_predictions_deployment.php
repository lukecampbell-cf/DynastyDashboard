<?php

declare(strict_types=1);

$repository = dirname(__DIR__);
$root = sys_get_temp_dir() . '/predictions-deployment-' . bin2hex(random_bytes(5));
mkdir($root . '/private/database', 0700, true);
mkdir($root . '/private/data', 0700, true);
putenv('DASHBOARD_DATA_DIR=' . $root . '/private/data');
putenv('PREDICTIONS_DB_PATH=' . $root . '/private/database/predictions.sqlite');
require $repository . '/web/predictions/includes/bootstrap.php';

function deployment_expect(bool $condition, string $message): void
{
    if (!$condition) {
        throw new RuntimeException($message);
    }
}

deployment_expect(predictions_data_directory() === $root . '/private/data', 'DASHBOARD_DATA_DIR was not honoured.');
deployment_expect(predictions_database_path() === $root . '/private/database/predictions.sqlite', 'PREDICTIONS_DB_PATH was not honoured.');

$pdo = predictions_database();
$pdo->exec("INSERT INTO prediction_cards
 (sleeper_user_id,sleeper_username,display_name,league_id,league_name,season,week,submitted_at)
 VALUES ('u1','user','User','L1','League',2026,1,'2026-09-01T00:00:00Z')");
$pdo = null;
$databasePath = $root . '/private/database/predictions.sqlite';
deployment_expect(is_file($databasePath), 'Predictions SQLite database was not created.');
deployment_expect((fileperms($databasePath) & 0777) === 0600, 'Predictions SQLite database is not mode 0600.');

$jsonPath = $root . '/private/data/private.json';
predictions_write_json_atomic($jsonPath, ['ok' => true]);
deployment_expect((fileperms($jsonPath) & 0777) === 0600, 'Private JSON is not mode 0600.');

$dashboardSource = file_get_contents($repository . '/dynasty_dashboard/dashboard_agent.py');
deployment_expect($dashboardSource !== false && str_contains($dashboardSource, 'href="predictions/"'), 'Dashboard navigation does not expose Predictions.');
deployment_expect(!str_contains($dashboardSource, "web/predictions/includes/bootstrap.php"), 'Dashboard rendering is coupled to Predictions bootstrap.');

foreach (['index.php', 'league.php', 'card.php', 'history.php', 'leaderboard.php', 'results.php'] as $page) {
    $source = file_get_contents($repository . '/web/predictions/' . $page);
    deployment_expect($source !== false && str_contains($source, 'href="../"'), $page . ' does not link back to the dashboard.');
}

$badPath = $root . '/missing/predictions.sqlite';
try {
    predictions_database($badPath);
    deployment_expect(false, 'Missing private database directory was silently accepted.');
} catch (RuntimeException $exception) {
    deployment_expect(str_contains($exception->getMessage(), 'directory is not writable'), 'Database path failure was not actionable.');
}

echo "Deployment integration tests passed.\n";
