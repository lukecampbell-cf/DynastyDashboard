<?php

declare(strict_types=1);

$root = sys_get_temp_dir() . '/predictions-card-' . bin2hex(random_bytes(5));
mkdir($root . '/prediction_markets/2026/week_4', 0700, true);
putenv('DASHBOARD_DATA_DIR=' . $root);
putenv('PREDICTIONS_DB_PATH=' . $root . '/predictions.sqlite');
require dirname(__DIR__) . '/web/predictions/includes/bootstrap.php';

function card_expect(bool $condition, string $message): void { if (!$condition) throw new RuntimeException($message); }

card_expect(predictions_session_cookie_path('/dashboard/predictions/index.php') === '/dashboard/predictions/', 'Nested Predictions cookie path is incorrect.');
card_expect(predictions_session_cookie_path('/predictions/index.php') === '/predictions/', 'Root Predictions cookie path is incorrect.');

$market = [
    'market_id' => 'mkt_1', 'player_id' => 'p1', 'player_name' => 'Test Player', 'position' => 'WR', 'team' => 'GB',
    'line' => 12.5, 'heuristic_projection' => 12.0, 'context_adjustment' => 0.4, 'final_projection' => 12.4,
    'model_version' => 'v0-heuristic', 'lock_at' => '2026-09-10T18:00:00+00:00',
];
$document = ['league_id' => 'L1', 'league_name' => 'League', 'season' => '2026', 'week' => 4, 'markets' => [$market], 'quick_pick' => ['mkt_1']];
file_put_contents($root . '/prediction_markets/2026/week_4/L1.json', json_encode($document, JSON_THROW_ON_ERROR));

$loaded = predictions_load_markets('L1', '2026', 4);
card_expect(count(predictions_open_market_map($loaded, new DateTimeImmutable('2026-09-10T17:59:59Z'))) === 1, 'Open market was rejected.');
card_expect(count(predictions_open_market_map($loaded, new DateTimeImmutable('2026-09-10T18:00:00Z'))) === 0, 'Lock time was not enforced.');
$whitespaceDocument = $loaded;
$whitespaceDocument['markets'][0]['market_id'] = '  mkt_1  ';
card_expect(isset(predictions_open_market_map($whitespaceDocument, new DateTimeImmutable('2026-09-10T17:59:59Z'))['mkt_1']), 'Authoritative market IDs were not normalised.');
$picks = predictions_normalise_picks(['mkt_1'], ['over']);
try { predictions_normalise_picks(array_fill(0, 7, 'market'), array_fill(0, 7, 'OVER')); card_expect(false, 'Oversized card was accepted.'); } catch (DomainException) {}
try { predictions_normalise_picks(['mkt_1'], ['SIDEWAYS']); card_expect(false, 'Invalid selection was accepted.'); } catch (DomainException) {}
$pdo = predictions_database();
$identity = ['sleeper_user_id' => 'u1', 'sleeper_username' => 'user', 'display_name' => 'User'];
$league = ['league_id' => 'L1', 'name' => 'League'];
$cardId = predictions_submit_card($pdo, $identity, $league, $loaded, $picks, new DateTimeImmutable('2026-09-10T17:00:00Z'));
$saved = $pdo->query('SELECT * FROM predictions WHERE card_id = ' . $cardId)->fetch();
card_expect($saved['selection'] === 'OVER' && (float) $saved['line_taken'] === 12.5, 'Authoritative immutable snapshot was not saved.');
$submittedPicks = predictions_card_picks($pdo, $cardId);
card_expect(count($submittedPicks) === 1, 'Submitted card picks were not loaded.');
card_expect($submittedPicks[0]['player_name'] === 'Test Player'
    && $submittedPicks[0]['selection'] === 'OVER'
    && (float) $submittedPicks[0]['line_taken'] === 12.5,
    'Submitted card readout did not retain the immutable player, selection and line.');
$submittedLeagues = predictions_submitted_league_ids($pdo, 'u1', 2026, 4);
card_expect(isset($submittedLeagues['L1']), 'Submitted league flag lookup did not find the card.');
card_expect(predictions_submitted_league_ids($pdo, 'u1', 2026, 5) === [],
    'Submitted league flag lookup crossed prediction weeks.');
try { predictions_submit_card($pdo, $identity, $league, $loaded, $picks, new DateTimeImmutable('2026-09-10T17:01:00Z')); card_expect(false, 'Duplicate card was accepted.'); } catch (DomainException) {}
try { predictions_submit_card($pdo, ['sleeper_user_id'=>'u2','sleeper_username'=>'u2','display_name'=>'U2'], $league, $loaded, ['forged'=>'OVER'], new DateTimeImmutable('2026-09-10T17:00:00Z')); card_expect(false, 'Forged market was accepted.'); } catch (DomainException) {}
card_expect((int) $pdo->query('SELECT COUNT(*) FROM prediction_cards')->fetchColumn() === 1, 'Failed transaction left a partial card.');
card_expect((int) $pdo->query('SELECT COUNT(*) FROM predictions')->fetchColumn() === 1, 'Failed transaction left partial predictions.');

echo "Card submission tests passed.\n";
