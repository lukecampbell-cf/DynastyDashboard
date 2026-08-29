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
try {
    if (!isset($_SESSION['predictions_season'])) {
        $state = predictions_sleeper_state();
        $_SESSION['predictions_season'] = (string) $state['season'];
        $_SESSION['predictions_week'] = (int) ($state['week'] ?? 0);
    }
    $season = (string) $_SESSION['predictions_season'];
    $leagues = predictions_sleeper_leagues((string) $identity['sleeper_user_id'], $season);
    $requestedLeagueId = (string) ($_GET['league_id'] ?? '');
    if ($requestedLeagueId !== '') {
        $selectedLeague = predictions_find_league($leagues, $requestedLeagueId);
        if ($selectedLeague === null) {
            throw new DomainException('That league is not available for this Sleeper user.');
        }
        // Roster data is requested only after the league is validated against the user's league list.
        $rosters = predictions_sleeper_rosters($requestedLeagueId);
        $roster = predictions_find_roster($rosters, (string) $identity['sleeper_user_id']);
        if ($roster === null) {
            throw new DomainException('No roster owned by this Sleeper user was found in that league.');
        }
        $players = predictions_sleeper_players();
        $candidates = predictions_candidate_players($roster, $players);
    }
} catch (DomainException | PredictionsSleeperException $exception) {
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
    <div class="header-meta"><span class="meta-pill"><?php echo predictions_escape($identity['display_name']); ?> · <?php echo predictions_escape($season); ?></span><a class="nav-link" href="index.php">Change user</a><a class="nav-link" href="../../index.html">Dashboard</a></div>
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
      <a class="league-card<?php echo $selectedLeague !== null && (string) $selectedLeague['league_id'] === $leagueId ? ' selected' : ''; ?>" href="?league_id=<?php echo rawurlencode($leagueId); ?>">
        <span class="league-name"><?php echo predictions_escape($league['name'] ?? 'Unnamed League'); ?></span>
        <span class="league-meta"><?php echo predictions_escape($league['total_rosters'] ?? '—'); ?> teams · <?php echo predictions_escape($season); ?></span>
      </a>
      <?php endforeach; ?>
    </div>
    <?php endif; ?>
  </section>
  <?php if ($selectedLeague !== null && $error === null): ?>
  <section class="panel roster-panel">
    <div class="section-heading"><div><p class="eyebrow">Step 2</p><h2><?php echo predictions_escape($selectedLeague['name'] ?? 'Selected league'); ?> roster</h2></div><span class="count-pill"><?php echo count($candidates); ?> eligible</span></div>
    <p class="panel-copy">QB, RB, WR and TE candidates. Projection markets arrive in later phases.</p>
    <?php if ($candidates === []): ?><p class="empty-state">No eligible roster players were found.</p><?php else: ?>
    <div class="player-grid">
      <?php foreach ($candidates as $player): ?>
      <article class="player-card">
        <div class="player-heading"><span class="position-tag pos-<?php echo strtolower(predictions_escape($player['position'])); ?>"><?php echo predictions_escape($player['position']); ?></span><span class="team-tag"><?php echo predictions_escape($player['team']); ?></span></div>
        <h3><?php echo predictions_escape($player['full_name']); ?></h3>
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
