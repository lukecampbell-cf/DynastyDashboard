<?php
declare(strict_types=1);
$root = sys_get_temp_dir() . '/predictions-phase5-' . bin2hex(random_bytes(5));
mkdir($root, 0700, true);
putenv('DASHBOARD_DATA_DIR=' . $root);
putenv('PREDICTIONS_DB_PATH=' . $root . '/predictions.sqlite');
require dirname(__DIR__) . '/web/predictions/includes/bootstrap.php';
function phase5_expect(bool $condition, string $message): void { if (!$condition) throw new RuntimeException($message); }
$pdo = predictions_database();
$pdo->exec("INSERT INTO prediction_cards
 (id,sleeper_user_id,sleeper_username,display_name,league_id,league_name,season,week,status,submitted_at,total_points)
 VALUES (1,'u1','user','Luke','L1','League One',2026,4,'settled','2026-09-01T00:00:00Z',100)");
$insert = $pdo->prepare("INSERT INTO predictions
 (card_id,market_id,player_id,player_name,position,selection,line_taken,heuristic_projection,context_adjustment,final_projection,model_version,result,actual_points)
 VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)");
$insert->execute(['m1','p1','Winner','WR','OVER',10.5,10,0.5,10.5,'v0','WIN',11]);
$insert->execute(['m2','p2','Loser','RB','UNDER',8.5,8,0.5,8.5,'v0','LOSS',9]);
$card = predictions_card_with_picks($pdo, 1, 'u1');
phase5_expect($card !== null && count($card['picks']) === 2, 'Results did not load card picks.');
phase5_expect(predictions_card_with_picks($pdo, 1, 'other') === null, 'User could read another user card.');
$history = predictions_user_history($pdo, 'u1');
phase5_expect(count($history) === 1 && (int) $history[0]['wins'] === 1, 'History totals are incorrect.');
$leaders = predictions_leaderboard($pdo);
phase5_expect(count($leaders) === 1 && (int) $leaders[0]['total_points'] === 100, 'Leaderboard duplicated card points.');
phase5_expect((int) $leaders[0]['wins'] === 1 && (int) $leaders[0]['settled_picks'] === 2, 'Leaderboard pick totals are incorrect.');
echo "Phase 5 predictions tests passed.\n";
