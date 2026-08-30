<?php

declare(strict_types=1);

require __DIR__ . '/includes/bootstrap.php';

$identity = $_SESSION['predictions_identity'] ?? null;
if (!is_array($identity) || !isset($identity['sleeper_user_id'])) {
    predictions_redirect('index.php');
}

$error = null;
$leagues = [];
$selectedLeague = null;
$candidates = [];
$projections = [];
$submittedLeagueIds = [];
try {
    // Refresh the period here so a session created during preseason cannot
    // retain Sleeper's preseason week as a regular-season market week.
    $state = predictions_sleeper_state();
    $_SESSION['predictions_season'] = (string) $state['season'];
    $_SESSION['predictions_week'] = predictions_market_week_from_state($state);
    $season = (string) $_SESSION['predictions_season'];
    $leagues = predictions_sleeper_leagues((string) $identity['sleeper_user_id'], $season);
    $submittedLeagueIds = predictions_submitted_league_ids(
        predictions_database(),
        (string) $identity['sleeper_user_id'],
        (int) $season,
        (int) $_SESSION['predictions_week']
    );
    $requestedLeagueId = (string) ($_GET['league_id'] ?? '');
    if ($requestedLeagueId !== '') {
        $selectedLeague = predictions_find_league($leagues, $requestedLeagueId);
        if ($selectedLeague === null) {
            throw new DomainException('That league is not available for this Sleeper user.');
        }
        $_SESSION['predictions_league'] = [
            'league_id' => (string) $selectedLeague['league_id'],
            'name' => (string) ($selectedLeague['name'] ?? 'Sleeper league'),
        ];
        if (($_GET['view'] ?? '') === 'card' && isset($submittedLeagueIds[$requestedLeagueId])) {
            predictions_redirect('card.php');
        }
        // Roster data is requested only after the league is validated against the user's league list.
        $rosters = predictions_sleeper_rosters($requestedLeagueId);
        $roster = predictions_find_roster($rosters, (string) $identity['sleeper_user_id']);
        if ($roster === null) {
            throw new DomainException('No roster owned by this Sleeper user was found in that league.');
        }
        $players = predictions_sleeper_players();
        $candidates = predictions_candidate_players($roster, $players);
        $directory = predictions_load_json(predictions_data_directory() . '/player_directory.json') ?? [];
        $details = predictions_load_json(predictions_data_directory() . '/player_cache.json') ?? [];
        $projections = predictions_project_roster(
            $candidates,
            $directory,
            predictions_trade_value_format($selectedLeague),
            $season,
            (int) ($_SESSION['predictions_week'] ?? 0),
            $details
        );
    }
} catch (DomainException | PredictionsSleeperException | PDOException $exception) {
    $error = $exception->getMessage();
}
$season = (string) ($_SESSION['predictions_season'] ?? '');
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Dynasty HQ Fantasy Predictions</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&amp;family=Inter:wght@400;500;600&amp;display=swap" rel="stylesheet">
  <link rel="stylesheet" href="assets/predictions.css">
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <div class="header-brand"><div class="brand-title">Dynasty <span>HQ</span></div><div class="brand-sub">Fantasy Predictions</div></div>
    <div class="header-meta"><span class="meta-pill"><?php echo predictions_escape($identity['display_name']); ?> · <?php echo predictions_escape($season); ?></span><a class="nav-link" href="index.php">Change user</a><a class="nav-link" href="../">Dashboard</a></div>
  </div>
</header>
<main class="main">
  <section class="hero compact"><h1>Dynasty HQ Fantasy Predictions</h1><p class="subtitle">Are you better than the projections?</p></section>
  <?php if ($error !== null): ?><div class="alert alert-error" role="alert"><?php echo predictions_escape($error); ?></div><?php endif; ?>
  <section class="panel">
    <div class="section-heading"><div><p class="eyebrow">Step 1</p><h2>Choose a league</h2></div><span class="count-pill"><?php echo count($leagues); ?> available</span></div>
    <?php if ($leagues === []): ?><p class="empty-state">No NFL leagues were found for the active season.</p><?php else: ?>
    <div class="league-grid">
      <?php foreach ($leagues as $league): $leagueId = (string) ($league['league_id'] ?? ''); ?>
      <a class="league-card<?php echo $selectedLeague !== null && (string) $selectedLeague['league_id'] === $leagueId ? ' selected' : ''; ?>" href="?league_id=<?php echo rawurlencode($leagueId); ?><?php echo isset($submittedLeagueIds[$leagueId]) ? '&amp;view=card' : ''; ?>">
        <span class="league-card-heading"><span class="league-name"><?php echo predictions_escape($league['name'] ?? 'Unnamed League'); ?></span><?php if (isset($submittedLeagueIds[$leagueId])): ?><span class="submitted-flag">Submitted</span><?php endif; ?></span>
        <span class="league-meta"><?php echo predictions_escape($league['total_rosters'] ?? '—'); ?> teams · <?php echo predictions_escape($season); ?></span>
      </a>
      <?php endforeach; ?>
    </div>
    <?php endif; ?>
  </section>
  <?php if ($selectedLeague !== null && $error === null): ?>
  <section class="panel roster-panel">
    <div class="section-heading"><div><p class="eyebrow">Step 2</p><h2><?php echo predictions_escape($selectedLeague['name'] ?? 'Selected league'); ?> roster</h2></div><span class="count-pill"><?php echo count($candidates); ?> eligible</span></div>
    <p class="panel-copy">QB, RB, WR and TE candidates with deterministic Projection Model V0 estimates.</p>
    <p class="roster-card-cta"><a class="primary-button button-link" href="card.php">Play this week's card</a></p>
    <?php if ($candidates === []): ?><p class="empty-state">No eligible roster players were found.</p><?php else: ?>
    <div class="player-grid">
      <?php foreach ($candidates as $player): ?>
      <article class="player-card">
        <div class="player-heading"><span class="position-tag pos-<?php echo strtolower(predictions_escape($player['position'])); ?>"><?php echo predictions_escape($player['position']); ?></span><span class="team-tag"><?php echo predictions_escape($player['team']); ?></span></div>
        <h3><?php echo predictions_escape($player['full_name']); ?></h3>
        <?php $projection = $projections[$player['player_id']] ?? null; ?>
        <?php if (is_array($projection)): ?><p class="projection-estimate"><strong><?php echo predictions_escape(number_format((float) $projection['heuristic_projection'], 2)); ?></strong> projected fantasy points</p><?php endif; ?>
        <div class="player-flags">
          <?php if ($player['is_starter']): ?><span class="roster-tag starter">Starter</span><?php endif; ?>
          <?php if ($player['is_taxi']): ?><span class="roster-tag">Taxi</span><?php endif; ?>
          <?php if ($player['is_ir']): ?><span class="roster-tag injury">IR</span><?php endif; ?>
          <?php if ($player['injury_status']): ?><span class="roster-tag injury"><?php echo predictions_escape($player['injury_status']); ?></span><?php endif; ?>
        </div>
      </article>
      <?php endforeach; ?>
    </div>
    <?php endif; ?>
  </section>
  <?php endif; ?>
</main>
</body>
</html>
