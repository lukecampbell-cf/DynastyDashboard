<?php
declare(strict_types=1);
require __DIR__ . '/includes/bootstrap.php';
$identity = $_SESSION['predictions_identity'] ?? null;
if (!is_array($identity)) predictions_redirect('index.php');
$database = predictions_database();
$cardId = filter_input(INPUT_GET, 'card_id', FILTER_VALIDATE_INT);
if (!is_int($cardId) || $cardId < 1) {
    $league = $_SESSION['predictions_league'] ?? [];
    $card = predictions_find_card($database, (string) $identity['sleeper_user_id'], (string) ($league['league_id'] ?? ''), (int) ($_SESSION['predictions_season'] ?? 0), (int) ($_SESSION['predictions_week'] ?? 0));
    $cardId = (int) ($card['id'] ?? 0);
}
$card = predictions_card_with_picks($database, (int) $cardId, (string) $identity['sleeper_user_id']);
if ($card === null) { http_response_code(404); }
?>
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Results · Dynasty HQ Fantasy Predictions</title><link rel="stylesheet" href="assets/predictions.css"></head><body>
<header class="site-header"><div class="header-inner"><div class="header-brand"><div class="brand-title">Dynasty <span>HQ</span></div><div class="brand-sub">Fantasy Predictions</div></div><div class="header-meta"><a class="nav-link" href="history.php">History</a><a class="nav-link" href="leaderboard.php">Leaderboard</a><a class="nav-link" href="league.php">Leagues</a></div></div></header>
<main class="main"><section class="hero compact"><h1>Card results</h1><p class="subtitle">Canonical Half-PPR scoring</p></section>
<?php if ($card === null): ?><div class="alert alert-error" role="alert">That prediction card was not found.</div><?php else: ?>
<section class="panel"><div class="section-heading"><div><p class="eyebrow">Week <?php echo (int) $card['week']; ?></p><h2><?php echo predictions_escape($card['league_name']); ?></h2></div><span class="result-badge result-<?php echo predictions_escape(strtolower($card['status'])); ?>"><?php echo predictions_escape($card['status']); ?></span></div><p class="score-total"><?php echo (int) $card['total_points']; ?> points</p></section>
<div class="results-list"><?php foreach ($card['picks'] as $pick): ?><article class="panel result-row"><div><span class="position-tag"><?php echo predictions_escape($pick['position']); ?></span><h3><?php echo predictions_escape($pick['player_name']); ?></h3><p class="panel-copy"><?php echo predictions_escape($pick['selection']); ?> <?php echo number_format((float) $pick['line_taken'], 1); ?> · Actual <?php echo $pick['actual_points'] === null ? '—' : number_format((float) $pick['actual_points'], 1); ?></p></div><strong class="result-badge result-<?php echo predictions_escape(strtolower($pick['result'])); ?>"><?php echo predictions_escape($pick['result']); ?></strong></article><?php endforeach; ?></div>
<?php endif; ?></main></body></html>
