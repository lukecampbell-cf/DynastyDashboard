<?php

declare(strict_types=1);

require __DIR__ . '/includes/bootstrap.php';

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    predictions_redirect('card.php');
}
$identity = $_SESSION['predictions_identity'] ?? null;
$league = $_SESSION['predictions_league'] ?? null;
if (!is_array($identity) || !is_array($league)) {
    predictions_redirect('index.php');
}
try {
    if (!predictions_verify_csrf($_POST['csrf_token'] ?? null)) {
        throw new DomainException('Your session expired. Please return to the card and try again.');
    }
    $rawPicks = $_POST['pick'] ?? null;
    $marketIds = is_array($rawPicks) ? array_keys($rawPicks) : null;
    $selections = is_array($rawPicks) ? array_values($rawPicks) : null;
    $picks = predictions_normalise_picks($marketIds, $selections);
    $season = (string) ($_SESSION['predictions_season'] ?? '');
    $week = (int) ($_SESSION['predictions_week'] ?? 0);
    // Reload the authoritative file here: no browser-supplied line, player, projection, version or lock is accepted.
    $document = predictions_load_markets((string) $league['league_id'], $season, $week);
    predictions_submit_card(predictions_database(), $identity, $league, $document, $picks);
    predictions_redirect('card.php?submitted=1');
} catch (DomainException | PDOException $exception) {
    http_response_code(422);
    $message = $exception->getMessage();
}
?>
<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Dynasty HQ Fantasy Predictions</title><link rel="stylesheet" href="assets/predictions.css"></head><body><main class="main main-narrow"><section class="hero"><h1>Dynasty HQ Fantasy Predictions</h1><p class="subtitle">Are you better than the projections?</p></section><div class="alert alert-error" role="alert"><?php echo predictions_escape($message ?? 'The card could not be submitted.'); ?></div><a class="primary-button button-link" href="card.php">Return to card</a></main></body></html>
