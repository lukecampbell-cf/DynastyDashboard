<?php

declare(strict_types=1);

require __DIR__ . '/includes/bootstrap.php';

$identity = $_SESSION['predictions_identity'] ?? null;
$league = $_SESSION['predictions_league'] ?? null;
if (!is_array($identity) || !is_array($league)) {
    predictions_redirect('index.php');
}
$season = (string) ($_SESSION['predictions_season'] ?? '');
$week = (int) ($_SESSION['predictions_week'] ?? 0);
$error = null;
$marketsUnavailable = false;
$document = null;
$markets = [];
$quickPick = [];
$existingCard = null;
$submittedPicks = [];
try {
    $document = predictions_load_markets((string) $league['league_id'], $season, $week);
    $markets = predictions_open_market_map($document);
    $quickPick = array_values(array_filter(
        array_map(
            'predictions_normalise_market_id',
            is_array($document['quick_pick'] ?? null) ? $document['quick_pick'] : []
        ),
        static fn (string $id): bool => isset($markets[$id])
    ));
    $quickPick = array_slice($quickPick, 0, PREDICTIONS_MAX_CARD_PICKS);
    $database = predictions_database();
    $existingCard = predictions_find_card($database, (string) $identity['sleeper_user_id'], (string) $league['league_id'], (int) $season, $week);
    if ($existingCard !== null) {
        $submittedPicks = predictions_card_picks($database, (int) $existingCard['id']);
    }
} catch (DomainException $exception) {
    $error = $exception->getMessage();
    $marketsUnavailable = true;
} catch (PDOException $exception) {
    $error = $exception->getMessage();
}
$submitted = isset($_GET['submitted']) && $_GET['submitted'] === '1';
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dynasty HQ Fantasy Predictions</title>
  <link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&amp;family=Inter:wght@400;500;600&amp;display=swap" rel="stylesheet">
  <link rel="stylesheet" href="assets/predictions.css"><script src="assets/predictions.js" defer></script>
</head>
<body>
<header class="site-header"><div class="header-inner"><div class="header-brand"><div class="brand-title">Dynasty <span>HQ</span></div><div class="brand-sub">Fantasy Predictions</div></div><div class="header-meta"><span class="meta-pill"><?php echo predictions_escape($identity['display_name']); ?> · <?php echo predictions_escape($season); ?></span><a class="nav-link" href="league.php?league_id=<?php echo rawurlencode((string) $league['league_id']); ?>">Leagues</a><a class="nav-link" href="../dashboard/">Dashboard</a></div></div></header>
<main class="main">
  <section class="hero compact"><p class="eyebrow">Week <?php echo $week; ?> · Half-PPR · <?php echo predictions_escape($league['name']); ?></p><h1>Dynasty HQ Fantasy Predictions</h1><p class="subtitle">Are you better than the projections?</p></section>
  <?php if ($submitted): ?><div class="alert alert-success" role="status">Card submitted. Your picks and lines are now locked in.</div><?php endif; ?>
  <?php if ($error !== null): ?><div class="alert alert-error" role="alert"><?php echo predictions_escape($error); ?><?php if ($marketsUnavailable): ?> Markets have not yet been prepared for the selected league/week. The private operator must run the documented market-generation command.<?php endif; ?></div><?php endif; ?>
  <?php if ($existingCard !== null): ?>
    <section class="panel status-panel"><p class="eyebrow">Card locked</p><h2>Week <?php echo $week; ?> submitted</h2><p class="panel-copy">Submitted <?php echo predictions_escape($existingCard['submitted_at']); ?>. Submitted cards are immutable.</p></section>
    <section class="panel submitted-card-panel">
      <div class="section-heading"><div><p class="eyebrow">Your card</p><h2>Submitted picks</h2></div><span class="count-pill"><?php echo count($submittedPicks); ?> picks</span></div>
      <?php if ($submittedPicks === []): ?>
        <p class="empty-state">No submitted picks were found for this card.</p>
      <?php else: ?>
        <div class="market-grid submitted-market-grid">
          <?php foreach ($submittedPicks as $pick): $selection = strtoupper((string) $pick['selection']); ?>
            <article class="market-card submitted-market-card">
              <div class="player-heading"><span class="position-tag"><?php echo predictions_escape($pick['position']); ?></span><span class="team-tag"><?php echo predictions_escape($pick['nfl_team'] ?? 'FA'); ?></span><span class="submitted-choice <?php echo strtolower(predictions_escape($selection)); ?>"><?php echo predictions_escape($selection); ?></span></div>
              <h3><?php echo predictions_escape($pick['player_name']); ?></h3>
              <div class="market-line"><strong><?php echo predictions_escape(number_format((float) $pick['line_taken'], 1)); ?></strong><span>Fantasy points</span></div>
              <p class="submitted-detail">Line locked at submission · <?php echo predictions_escape($pick['model_version']); ?></p>
              <?php if (($pick['result'] ?? 'PENDING') !== 'PENDING'): ?><p class="submitted-result">Result: <strong><?php echo predictions_escape($pick['result']); ?></strong><?php if ($pick['actual_points'] !== null): ?> · <?php echo predictions_escape(number_format((float) $pick['actual_points'], 1)); ?> actual<?php endif; ?></p><?php endif; ?>
            </article>
          <?php endforeach; ?>
        </div>
      <?php endif; ?>
    </section>
  <?php elseif ($error === null && $markets !== []): ?>
  <form method="post" action="submit.php" data-card-form>
    <section class="panel card-toolbar"><div><p class="eyebrow">Quick Pick</p><h2>Make your calls</h2><p class="panel-copy">Choose OVER or UNDER for up to six players. Quick Pick highlights this week's most interesting markets.</p></div><div class="mode-toggle" role="group" aria-label="Card mode"><button type="button" class="mode-button active" data-mode="quick">Quick Pick</button><button type="button" class="mode-button" data-mode="build">Build My Card</button></div></section>
    <input type="hidden" name="csrf_token" value="<?php echo predictions_escape(predictions_csrf_token()); ?>">
    <div class="market-grid">
    <?php foreach ($markets as $marketId => $market): $isQuick = in_array($marketId, $quickPick, true); ?>
      <article class="market-card<?php echo $isQuick ? ' quick-market' : ' build-only'; ?>" data-market data-quick="<?php echo $isQuick ? '1' : '0'; ?>">
        <div class="player-heading"><span class="position-tag"><?php echo predictions_escape($market['position']); ?></span><span class="team-tag"><?php echo predictions_escape($market['team'] ?? 'FA'); ?></span><?php if ($isQuick): ?><span class="quick-tag">Quick Pick</span><?php endif; ?></div>
        <h3><?php echo predictions_escape($market['player_name'] ?? 'Unknown player'); ?></h3>
        <div class="market-line"><strong><?php echo predictions_escape(number_format((float) $market['line'], 1)); ?></strong><span>Fantasy points</span></div>
        <?php if (!empty($market['summary'])): ?><p class="market-summary"><?php echo predictions_escape($market['summary']); ?></p><?php endif; ?>
        <fieldset class="pick-controls"><legend class="sr-only">Prediction for <?php echo predictions_escape($market['player_name'] ?? 'player'); ?></legend>
          <label class="pick-button over"><input type="radio" name="pick[<?php echo predictions_escape($marketId); ?>]" value="OVER"> OVER</label>
          <label class="pick-button under"><input type="radio" name="pick[<?php echo predictions_escape($marketId); ?>]" value="UNDER"> UNDER</label>
        </fieldset>
      </article>
    <?php endforeach; ?>
    </div>
    <div class="submit-bar"><span><strong data-pick-count>0</strong> / 6 picks selected</span><button class="primary-button" type="submit">Submit card</button></div>
  </form>
  <?php elseif ($error === null): ?><section class="panel"><p class="empty-state">There are no open markets for this league and week.</p></section><?php endif; ?>
</main></body></html>
