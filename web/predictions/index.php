<?php

declare(strict_types=1);

require __DIR__ . '/includes/bootstrap.php';

$error = null;
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    try {
        if (!predictions_verify_csrf($_POST['csrf_token'] ?? null)) {
            throw new DomainException('Your session expired. Please try again.');
        }
        $identity = predictions_resolve_login((string) ($_POST['sleeper_username'] ?? ''));
        session_regenerate_id(true);
        $_SESSION['predictions_identity'] = $identity;
        unset($_SESSION['predictions_season'], $_SESSION['predictions_week'], $_SESSION['predictions_league']);
        predictions_redirect('league.php');
    } catch (DomainException | PredictionsSleeperException $exception) {
        $error = $exception->getMessage();
    }
}
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
    <div class="header-brand">
      <div class="brand-title">Dynasty <span>HQ</span></div>
      <div class="brand-sub">Fantasy Predictions</div>
    </div>
    <div class="header-meta"><a class="nav-link" href="../">Dashboard</a><a class="nav-link" href="../trade_calculator.php">Trade Calculator</a></div>
  </div>
</header>
<main class="main main-narrow">
  <section class="hero">
    <p class="eyebrow">Private · Free to play · Half-PPR</p>
    <h1>Dynasty HQ Fantasy Predictions</h1>
    <p class="subtitle">Are you better than the projections?</p>
  </section>
  <section class="panel login-panel">
    <h2>Start with Sleeper</h2>
    <p class="panel-copy">Enter an authorised Sleeper username to find your leagues and roster.</p>
    <?php if ($error !== null): ?><div class="alert alert-error" role="alert"><?php echo predictions_escape($error); ?></div><?php endif; ?>
    <form method="post" class="stack-form">
      <input type="hidden" name="csrf_token" value="<?php echo predictions_escape(predictions_csrf_token()); ?>">
      <label for="sleeper_username">Sleeper username</label>
      <input id="sleeper_username" name="sleeper_username" type="text" maxlength="50" autocomplete="username" required value="<?php echo predictions_escape($_POST['sleeper_username'] ?? ''); ?>">
      <button class="primary-button" type="submit">Find my leagues</button>
    </form>
    <p class="security-note">Username access is a private allowlist, not account authentication.</p>
  </section>
</main>
</body>
</html>
