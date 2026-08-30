<?php
declare(strict_types=1);

require __DIR__ . '/includes/trade_calculator_data.php';

$view = trade_calculator_view_model(getenv('DASHBOARD_DATA_DIR') ?: __DIR__, $_GET);
$bootstrapJson = trade_calculator_encode_bootstrap($view);

function trade_calculator_escape(?string $value): string
{
    return htmlspecialchars($value ?? '', ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trade Calculator — Dynasty HQ</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="assets/trade-calculator.css">
  <script id="trade-calculator-data" type="application/json"><?php echo $bootstrapJson; ?></script>
  <script src="assets/trade-calculator.js" defer></script>
</head>
<body>
<header class="site-header">
  <div class="header-inner">
    <div>
      <div class="brand-title">Trade <span>Calculator</span></div>
      <div class="brand-sub">Dynasty HQ<?php echo $view['fetchedAt'] ? ' · values as of ' . trade_calculator_escape(substr($view['fetchedAt'], 0, 10)) : ''; ?></div>
    </div>
    <div class="header-meta"><a class="back-link" href="index.html">&larr; Back to dashboard</a></div>
  </div>
</header>
<main class="main">
<?php if ($view['loadError'] !== null): ?>
  <div class="error-card"><?php echo trade_calculator_escape($view['loadError']); ?></div>
<?php else: ?>
  <div class="page-title">Is this trade fair?</div>
  <div class="page-sub">Add players and picks to each side. Values come from RosterAudit's dynasty market rankings; players marked <em>est.</em> use a FantasyPros-rank-based estimate.</div>
<?php if ($view['preselect']['notFound']): ?>
  <div class="preselect-note">That link asked for a player who isn't in the current directory, so Side A started empty. Search for the player by name to add them.</div>
<?php endif; ?>
  <div class="format-toggle" role="group" aria-label="Value format">
    <button type="button" id="fmt-sf" data-format="sf" class="<?php echo $view['preselect']['format'] === 'sf' ? 'active' : ''; ?>">Superflex</button>
    <button type="button" id="fmt-1qb" data-format="1qb" class="<?php echo $view['preselect']['format'] === '1qb' ? 'active' : ''; ?>">1QB</button>
  </div>
  <div class="trade-grid">
<?php foreach (['A', 'B'] as $side): ?>
    <div class="side-card" data-side="<?php echo $side; ?>">
      <div class="side-header"><div class="side-title">Side <?php echo $side; ?> gives</div><div class="side-total" id="total-<?php echo $side; ?>">0</div></div>
      <div class="search-wrap">
        <input type="text" class="search-input" id="search-<?php echo $side; ?>" placeholder="Search a player to add…" autocomplete="off">
        <div class="search-results" id="results-<?php echo $side; ?>"></div>
      </div>
      <div class="pick-chips" id="picks-<?php echo $side; ?>"></div>
      <ul class="asset-list" id="list-<?php echo $side; ?>"></ul>
    </div>
<?php endforeach; ?>
  </div>
  <div class="verdict-card">
    <div class="balance-bar"><div class="balance-bar-a" id="bar-A" style="width:50%"></div><div class="balance-bar-b" id="bar-B" style="width:50%"></div></div>
    <div class="verdict-badge verdict-empty" id="verdict-badge">Add assets to both sides</div>
    <div class="verdict-detail" id="verdict-detail"></div>
    <div class="bta-box" id="bta-box" style="display:none;">
      <p>This trade is outrageously unbalanced. Report it to the authorities.</p>
      <a class="bta-button" id="bta-link" href="#" target="_blank" rel="noopener">Email the Bullshit Trade Association</a>
    </div>
  </div>
<?php endif; ?>
</main>
<footer class="site-footer"><span class="footer-orange">Dynasty HQ</span> · Trade values via RosterAudit</footer>
</body>
</html>
