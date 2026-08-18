<?php
/**
 * Trade Calculator
 * Reads player_directory.json (every fantasy-relevant NFL player + trade
 * value, both sf and 1qb — see player_directory_agent.py, refreshed weekly)
 * and trade_values.json (for its pick tier chart — see trade_value_agent.py)
 * to let you build a two-sided trade and get a fairness verdict.
 *
 * No write access to either file — this is a read-only consumer of caches
 * the Python pipeline maintains. All trade math runs client-side in JS
 * after the data is embedded on page load; there's no per-request
 * computation or external API call.
 *
 * Deployment: this file expects to sit next to player_directory.json and
 * trade_values.json (the project root the Python pipeline writes to). If
 * you deploy it somewhere else — e.g. inside the web root alongside
 * index.html — point DASHBOARD_DATA_DIR at the project root via an
 * environment variable (nginx fastcgi_param / apache SetEnv), or edit the
 * default below. See SETUP.md.
 */

declare(strict_types=1);

require __DIR__ . '/trade_calculator_lib.php';

$dataDir = getenv('DASHBOARD_DATA_DIR') ?: __DIR__;

/**
 * Verdict thresholds — ratio is min(totalA, totalB) / max(totalA, totalB),
 * so 1.0 is a perfectly even trade. Tune these to taste; they're the only
 * knob controlling how forgiving the calculator is.
 */
const VERDICT_YES_MIN = 0.90;
const VERDICT_CLOSE_YES_MIN = 0.75;
const VERDICT_CLOSE_NO_MIN = 0.55;
const VERDICT_NO_MIN = 0.30; // below this: outrageously unbalanced

const BTA_EMAIL = 'trades@bullshit-trade-association.co.uk';

function load_json(string $path): ?array {
    if (!is_file($path)) {
        return null;
    }
    $raw = file_get_contents($path);
    if ($raw === false) {
        return null;
    }
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : null;
}

$playerDirectory = load_json($dataDir . '/player_directory.json');
$tradeValues = load_json($dataDir . '/trade_values.json');

$loadError = null;
if ($playerDirectory === null) {
    $loadError = 'player_directory.json not found or unreadable at ' . htmlspecialchars($dataDir)
        . ' — run the full pipeline (orchestrator.py) at least once first so player_directory_agent.py can build it.';
} elseif ($tradeValues === null) {
    $loadError = 'trade_values.json not found or unreadable at ' . htmlspecialchars($dataDir)
        . ' — run trade_value_agent.py (or the full pipeline) at least once first.';
}

/**
 * Reshape player_directory.json (id -> {name, position, team, values: {sf:
 * {value,tier}, 1qb: {value,tier}}}) into the per-format id -> {name,
 * position, team, value, tier} map the front end works with.
 */
function build_player_pool(array $playerDirectory): array {
    $pool = ['sf' => [], '1qb' => []];
    foreach ($playerDirectory as $id => $p) {
        foreach (['sf', '1qb'] as $format) {
            $v = $p['values'][$format] ?? ['value' => 0, 'tier' => 'Deep Stash', 'source' => 'unranked'];
            $pool[$format][$id] = [
                'name' => $p['name'] ?? 'Unknown',
                'position' => $p['position'] ?? 'UNK',
                'team' => $p['team'] ?? 'FA',
                'value' => (float) ($v['value'] ?? 0),
                'tier' => $v['tier'] ?? null,
                'source' => $v['source'] ?? 'rosteraudit',
            ];
        }
    }
    return $pool;
}

function build_pick_tiers(array $tradeValues): array {
    $tiers = [];
    foreach (['sf', '1qb'] as $format) {
        $tiers[$format] = $tradeValues['formats'][$format]['tier_chart'] ?? [];
    }
    return $tiers;
}

$playerPool = $playerDirectory !== null ? build_player_pool($playerDirectory) : ['sf' => [], '1qb' => []];
$pickTiers = $tradeValues !== null ? build_pick_tiers($tradeValues) : ['sf' => [], '1qb' => []];
$fetchedAt = $tradeValues['fetched_at'] ?? null;

// Deep-link support for dashboard_agent.py's "Explore Trade" card link
// (?player=<id>&format=<sf|1qb>) — see trade_calculator_lib.php for the
// validation this goes through before anything is trusted.
$preselect = resolve_preselect($playerPool, $_GET['format'] ?? null, $_GET['player'] ?? null);

$jsonFlags = JSON_HEX_TAG | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_HEX_AMP;
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trade Calculator — Dynasty HQ</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@400;600;700;800&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --navy:    #0a0e1a;
      --navy-2:  #111827;
      --navy-3:  #1c2333;
      --navy-4:  #252f42;
      --bears-orange: #e05c00;
      --bears-dark:   #c04a00;
      --up:      #22c55e;
      --up-dim:  #166534;
      --down:    #ef4444;
      --down-dim:#7f1d1d;
      --watch:   #f59e0b;
      --watch-dim:#78350f;
      --text:    #e2e8f0;
      --muted:   #94a3b8;
      --border:  #2d3748;
      --card-bg: #161d2e;
    }

    * { box-sizing: border-box; margin: 0; padding: 0; }

    body {
      background: var(--navy);
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 14px;
      line-height: 1.5;
      min-height: 100vh;
    }

    /* ── HEADER (shared with dashboard) ── */
    .site-header {
      background: var(--navy-2);
      border-bottom: 3px solid var(--bears-orange);
      padding: 0 24px;
    }

    .header-inner {
      max-width: 1400px;
      margin: 0 auto;
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 16px 0;
      gap: 16px;
      flex-wrap: wrap;
    }

    .brand-title {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 28px;
      font-weight: 800;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #fff;
    }

    .brand-title span { color: var(--bears-orange); }

    .brand-sub {
      font-size: 12px;
      color: var(--muted);
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }

    .header-meta { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }

    .back-link {
      font-size: 12px;
      color: var(--muted);
      text-decoration: none;
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 6px 14px;
      transition: border-color 0.15s, color 0.15s;
    }
    .back-link:hover { border-color: var(--bears-orange); color: #fff; }

    .main { max-width: 1200px; margin: 0 auto; padding: 32px 24px; }

    .page-title {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 26px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.04em;
      color: #fff;
      margin-bottom: 4px;
    }

    .page-sub { font-size: 13px; color: var(--muted); margin-bottom: 24px; }

    .error-card {
      background: var(--card-bg);
      border: 1px solid var(--down-dim);
      border-left: 3px solid var(--down);
      border-radius: 10px;
      padding: 20px;
      color: #fca5a5;
    }

    /* ── FORMAT TOGGLE ── */
    .format-toggle {
      display: inline-flex;
      background: var(--navy-3);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 3px;
      margin-bottom: 24px;
    }

    .format-toggle button {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      background: transparent;
      color: var(--muted);
      border: none;
      border-radius: 6px;
      padding: 8px 18px;
      cursor: pointer;
      transition: background 0.15s, color 0.15s;
    }

    .format-toggle button.active {
      background: var(--bears-orange);
      color: #fff;
    }

    /* ── TRADE SIDES ── */
    .trade-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 20px;
      margin-bottom: 24px;
    }

    @media (max-width: 800px) {
      .trade-grid { grid-template-columns: 1fr; }
    }

    .side-card {
      background: var(--navy-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 18px;
    }

    .side-header {
      display: flex;
      align-items: baseline;
      justify-content: space-between;
      margin-bottom: 14px;
    }

    .side-title {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 18px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      color: #fff;
    }

    .side-total {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 18px;
      font-weight: 700;
      color: var(--bears-orange);
    }

    .search-wrap { position: relative; margin-bottom: 10px; }

    .search-input {
      width: 100%;
      background: var(--navy-3);
      border: 1px solid var(--border);
      border-radius: 6px;
      color: var(--text);
      font-family: 'Inter', sans-serif;
      font-size: 13px;
      padding: 9px 12px;
    }

    .search-input:focus { outline: none; border-color: var(--bears-orange); }

    .search-results {
      position: absolute;
      z-index: 10;
      top: calc(100% + 4px);
      left: 0;
      right: 0;
      background: var(--navy-3);
      border: 1px solid var(--border);
      border-radius: 8px;
      max-height: 260px;
      overflow-y: auto;
      display: none;
    }

    .search-results.open { display: block; }

    .search-result-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 8px 12px;
      cursor: pointer;
      font-size: 12px;
      border-bottom: 1px solid var(--border);
    }
    .search-result-row:last-child { border-bottom: none; }
    .search-result-row:hover { background: var(--navy-4); }

    .search-result-name { color: var(--text); font-weight: 600; }
    .search-result-meta { color: var(--muted); font-size: 11px; }
    .search-result-value { color: var(--bears-orange); font-weight: 700; font-size: 11px; }

    .pick-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 14px; }

    .pick-chip {
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
      background: var(--navy-3);
      border: 1px solid var(--border);
      color: var(--muted);
      border-radius: 14px;
      padding: 5px 11px;
      cursor: pointer;
      transition: border-color 0.15s, color 0.15s;
    }
    .pick-chip:hover { border-color: var(--bears-orange); color: #fff; }

    .asset-list { list-style: none; }

    .asset-row {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 8px 12px;
      margin-bottom: 6px;
      font-size: 12px;
    }

    .asset-name { color: var(--text); font-weight: 600; }
    .asset-meta { color: var(--muted); font-size: 11px; margin-left: 6px; }
    .asset-value { color: var(--bears-orange); font-weight: 700; }

    .asset-remove {
      background: none;
      border: none;
      color: var(--muted);
      cursor: pointer;
      font-size: 14px;
      line-height: 1;
      padding: 2px 6px;
    }
    .asset-remove:hover { color: var(--down); }

    .empty-side { color: var(--muted); font-size: 12px; font-style: italic; padding: 8px 0; }

    /* ── VERDICT ── */
    .verdict-card {
      background: var(--navy-2);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 20px;
    }

    .balance-bar {
      display: flex;
      height: 10px;
      border-radius: 5px;
      overflow: hidden;
      background: var(--navy-4);
      margin-bottom: 16px;
    }

    .balance-bar-a { background: var(--bears-orange); }
    .balance-bar-b { background: #0891b2; }

    .verdict-badge {
      display: inline-block;
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 20px;
      font-weight: 800;
      letter-spacing: 0.03em;
      border-radius: 8px;
      padding: 8px 16px;
      margin-bottom: 10px;
    }

    .verdict-yes        { background: rgba(34,197,94,0.15); color: var(--up); }
    .verdict-close-yes  { background: rgba(245,158,11,0.15); color: var(--watch); }
    .verdict-close-no   { background: rgba(245,158,11,0.15); color: var(--watch); }
    .verdict-no          { background: rgba(239,68,68,0.15); color: var(--down); }
    .verdict-outrageous { background: rgba(239,68,68,0.25); color: #fca5a5; }
    .verdict-empty       { background: var(--navy-3); color: var(--muted); }

    .verdict-detail { font-size: 13px; color: var(--muted); margin-bottom: 4px; }

    .bta-box {
      margin-top: 16px;
      padding: 14px;
      background: rgba(239,68,68,0.08);
      border: 1px solid var(--down-dim);
      border-radius: 8px;
    }

    .bta-box p { font-size: 12px; color: #fca5a5; margin-bottom: 10px; }

    .bta-button {
      display: inline-block;
      font-family: 'Barlow Condensed', sans-serif;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      text-decoration: none;
      background: var(--down);
      color: #fff;
      border-radius: 6px;
      padding: 9px 16px;
    }
    .bta-button:hover { background: #dc2626; }

    /* ── FOOTER ── */
    .site-footer {
      text-align: center;
      padding: 32px 24px;
      color: var(--muted);
      font-size: 12px;
      border-top: 1px solid var(--border);
      margin-top: 24px;
    }
    .footer-orange { color: var(--bears-orange); font-weight: 600; }
  </style>
</head>
<body>

<header class="site-header">
  <div class="header-inner">
    <div>
      <div class="brand-title">Trade <span>Calculator</span></div>
      <div class="brand-sub">Dynasty HQ<?php echo $fetchedAt ? ' · values as of ' . htmlspecialchars(substr($fetchedAt, 0, 10)) : ''; ?></div>
    </div>
    <div class="header-meta">
      <a class="back-link" href="index.html">&larr; Back to dashboard</a>
    </div>
  </div>
</header>

<main class="main">
<?php if ($loadError !== null): ?>
  <div class="error-card"><?php echo $loadError; ?></div>
<?php else: ?>

  <div class="page-title">Is this trade fair?</div>
  <div class="page-sub">Add players and picks to each side. Values come from RosterAudit's dynasty market
  rankings; players marked <em>est.</em> aren't priced by RosterAudit and instead use a FantasyPros-rank-based
  estimate — treat those as directional, not authoritative.</div>

  <div class="format-toggle" role="group" aria-label="Value format">
    <button type="button" id="fmt-sf" class="<?php echo $preselect['format'] === 'sf' ? 'active' : ''; ?>" onclick="switchFormat('sf')">Superflex</button>
    <button type="button" id="fmt-1qb" class="<?php echo $preselect['format'] === '1qb' ? 'active' : ''; ?>" onclick="switchFormat('1qb')">1QB</button>
  </div>

  <div class="trade-grid">
    <div class="side-card" data-side="A">
      <div class="side-header">
        <div class="side-title">Side A gives</div>
        <div class="side-total" id="total-A">0</div>
      </div>
      <div class="search-wrap">
        <input type="text" class="search-input" id="search-A" placeholder="Search a player to add…" autocomplete="off">
        <div class="search-results" id="results-A"></div>
      </div>
      <div class="pick-chips" id="picks-A"></div>
      <ul class="asset-list" id="list-A"></ul>
    </div>

    <div class="side-card" data-side="B">
      <div class="side-header">
        <div class="side-title">Side B gives</div>
        <div class="side-total" id="total-B">0</div>
      </div>
      <div class="search-wrap">
        <input type="text" class="search-input" id="search-B" placeholder="Search a player to add…" autocomplete="off">
        <div class="search-results" id="results-B"></div>
      </div>
      <div class="pick-chips" id="picks-B"></div>
      <ul class="asset-list" id="list-B"></ul>
    </div>
  </div>

  <div class="verdict-card">
    <div class="balance-bar">
      <div class="balance-bar-a" id="bar-A" style="width:50%"></div>
      <div class="balance-bar-b" id="bar-B" style="width:50%"></div>
    </div>
    <div class="verdict-badge verdict-empty" id="verdict-badge">Add assets to both sides</div>
    <div class="verdict-detail" id="verdict-detail"></div>
    <div class="bta-box" id="bta-box" style="display:none;">
      <p>This trade is outrageously unbalanced. Report it to the authorities.</p>
      <a class="bta-button" id="bta-link" href="#" target="_blank" rel="noopener">Email the Bullshit Trade Association</a>
    </div>
  </div>

  <script>
    const PLAYERS = <?php echo json_encode($playerPool, $jsonFlags); ?>;
    const PICK_TIERS = <?php echo json_encode($pickTiers, $jsonFlags); ?>;
    const BTA_EMAIL = <?php echo json_encode(BTA_EMAIL, $jsonFlags); ?>;
    const VERDICT_YES_MIN = <?php echo json_encode(VERDICT_YES_MIN); ?>;
    const VERDICT_CLOSE_YES_MIN = <?php echo json_encode(VERDICT_CLOSE_YES_MIN); ?>;
    const VERDICT_CLOSE_NO_MIN = <?php echo json_encode(VERDICT_CLOSE_NO_MIN); ?>;
    const VERDICT_NO_MIN = <?php echo json_encode(VERDICT_NO_MIN); ?>;
    // Already validated server-side against the loaded player pool (see
    // trade_calculator_lib.php's resolve_preselect()) — playerId is either
    // null or a real key in PLAYERS[format], never unvalidated user input.
    const PRESELECT = <?php echo json_encode($preselect, $jsonFlags); ?>;

    let activeFormat = PRESELECT.format;
    const sides = { A: [], B: [] }; // {type: 'player'|'pick', id or label}

    function currentPlayers() { return PLAYERS[activeFormat] || {}; }
    function currentPickTiers() { return PICK_TIERS[activeFormat] || []; }

    function assetValue(entry) {
      if (entry.type === 'player') {
        const p = currentPlayers()[entry.id];
        return p ? p.value : 0;
      }
      const tier = currentPickTiers().find(t => t.label === entry.label);
      return tier ? tier.min_value : 0;
    }

    function assetLabel(entry) {
      if (entry.type === 'player') {
        const p = currentPlayers()[entry.id];
        if (!p) return { name: 'Unknown player', meta: 'not valued in this format' };
        const estimated = p.source === 'fantasypros_estimate' ? ' · est.' : '';
        return { name: p.name, meta: `${p.position} · ${p.team}${estimated}` };
      }
      return { name: entry.label, meta: 'Draft pick' };
    }

    function renderPickChips() {
      ['A', 'B'].forEach(side => {
        const wrap = document.getElementById('picks-' + side);
        wrap.innerHTML = '';
        currentPickTiers().forEach(tier => {
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = 'pick-chip';
          chip.textContent = tier.label;
          chip.title = 'Add ' + tier.label + ' pick (~' + Math.round(tier.min_value) + ')';
          chip.onclick = () => addAsset(side, { type: 'pick', label: tier.label });
          wrap.appendChild(chip);
        });
      });
    }

    function addAsset(side, entry) {
      sides[side].push(entry);
      renderSide(side);
      updateVerdict();
    }

    function removeAsset(side, index) {
      sides[side].splice(index, 1);
      renderSide(side);
      updateVerdict();
    }

    function renderSide(side) {
      const list = document.getElementById('list-' + side);
      list.innerHTML = '';
      if (sides[side].length === 0) {
        const li = document.createElement('li');
        li.className = 'empty-side';
        li.textContent = 'Nothing added yet.';
        list.appendChild(li);
      }
      sides[side].forEach((entry, i) => {
        const label = assetLabel(entry);
        const value = assetValue(entry);
        const li = document.createElement('li');
        li.className = 'asset-row';
        li.innerHTML = `<span><span class="asset-name">${escapeHtml(label.name)}</span>` +
          `<span class="asset-meta">${escapeHtml(label.meta)}</span></span>` +
          `<span><span class="asset-value">${Math.round(value)}</span>` +
          `<button class="asset-remove" title="Remove" data-i="${i}">&times;</button></span>`;
        li.querySelector('.asset-remove').addEventListener('click', () => removeAsset(side, i));
        list.appendChild(li);
      });
      document.getElementById('total-' + side).textContent = Math.round(sideTotal(side));
    }

    function sideTotal(side) {
      return sides[side].reduce((sum, e) => sum + assetValue(e), 0);
    }

    function escapeHtml(str) {
      const div = document.createElement('div');
      div.textContent = str;
      return div.innerHTML;
    }

    function updateVerdict() {
      const totalA = sideTotal('A');
      const totalB = sideTotal('B');
      const badge = document.getElementById('verdict-badge');
      const detail = document.getElementById('verdict-detail');
      const btaBox = document.getElementById('bta-box');
      const combined = totalA + totalB;

      document.getElementById('bar-A').style.width = combined > 0 ? (totalA / combined * 100) + '%' : '50%';
      document.getElementById('bar-B').style.width = combined > 0 ? (totalB / combined * 100) + '%' : '50%';

      if (sides.A.length === 0 && sides.B.length === 0) {
        badge.className = 'verdict-badge verdict-empty';
        badge.textContent = 'Add assets to both sides';
        detail.textContent = '';
        btaBox.style.display = 'none';
        return;
      }

      const higher = Math.max(totalA, totalB);
      const lower = Math.min(totalA, totalB);
      const ratio = higher > 0 ? lower / higher : 1;
      const pct = Math.round(ratio * 100);

      let cls, text;
      if (ratio >= VERDICT_YES_MIN) {
        cls = 'verdict-yes'; text = '✅ Yes — Fair Trade';
      } else if (ratio >= VERDICT_CLOSE_YES_MIN) {
        cls = 'verdict-close-yes'; text = '🟡 Close Yes';
      } else if (ratio >= VERDICT_CLOSE_NO_MIN) {
        cls = 'verdict-close-no'; text = '🟠 Close No';
      } else if (ratio >= VERDICT_NO_MIN) {
        cls = 'verdict-no'; text = '❌ No — Lopsided';
      } else {
        cls = 'verdict-outrageous'; text = '🚨 Outrageously Unbalanced';
      }

      badge.className = 'verdict-badge ' + cls;
      badge.textContent = text;
      detail.textContent = `Side A: ${Math.round(totalA)} · Side B: ${Math.round(totalB)} · ` +
        `smaller side is ${pct}% of the larger.`;

      if (ratio < VERDICT_NO_MIN) {
        btaBox.style.display = 'block';
        document.getElementById('bta-link').href = buildMailto(totalA, totalB);
      } else {
        btaBox.style.display = 'none';
      }
    }

    function buildMailto(totalA, totalB) {
      const describeSide = side => sides[side].map(e => {
        const label = assetLabel(e);
        return `- ${label.name} (${Math.round(assetValue(e))})`;
      }).join('\n') || '- (nothing)';

      const subject = 'Trade Review Request — Outrageously Unbalanced';
      const body = `I'd like to report the following trade for review:\n\n` +
        `SIDE A gives (total ${Math.round(totalA)}):\n${describeSide('A')}\n\n` +
        `SIDE B gives (total ${Math.round(totalB)}):\n${describeSide('B')}\n\n` +
        `Format: ${activeFormat === 'sf' ? 'Superflex' : '1QB'}\n` +
        `Please investigate.`;

      return `mailto:${BTA_EMAIL}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
    }

    function wireSearch(side) {
      const input = document.getElementById('search-' + side);
      const results = document.getElementById('results-' + side);

      input.addEventListener('input', () => {
        const q = input.value.trim().toLowerCase();
        results.innerHTML = '';
        if (q.length < 2) {
          results.classList.remove('open');
          return;
        }
        const matches = Object.entries(currentPlayers())
          .filter(([, p]) => p.name.toLowerCase().includes(q))
          .sort((a, b) => b[1].value - a[1].value)
          .slice(0, 8);

        matches.forEach(([id, p]) => {
          const row = document.createElement('div');
          row.className = 'search-result-row';
          const estimated = p.source === 'fantasypros_estimate' ? ' · est.' : '';
          row.innerHTML = `<span><span class="search-result-name">${escapeHtml(p.name)}</span>` +
            `<span class="search-result-meta"> ${escapeHtml(p.position)} · ${escapeHtml(p.team)}${estimated}</span></span>` +
            `<span class="search-result-value">${Math.round(p.value)}</span>`;
          row.addEventListener('click', () => {
            addAsset(side, { type: 'player', id });
            input.value = '';
            results.classList.remove('open');
          });
          results.appendChild(row);
        });
        results.classList.toggle('open', matches.length > 0);
      });

      document.addEventListener('click', (e) => {
        if (!results.contains(e.target) && e.target !== input) {
          results.classList.remove('open');
        }
      });
    }

    function switchFormat(fmt) {
      activeFormat = fmt;
      document.getElementById('fmt-sf').classList.toggle('active', fmt === 'sf');
      document.getElementById('fmt-1qb').classList.toggle('active', fmt === '1qb');
      renderPickChips();
      renderSide('A');
      renderSide('B');
      updateVerdict();
    }

    renderPickChips();
    renderSide('A');
    renderSide('B');
    wireSearch('A');
    wireSearch('B');

    if (PRESELECT.playerId) {
      addAsset('A', { type: 'player', id: PRESELECT.playerId });
    }
  </script>

<?php endif; ?>
</main>

<footer class="site-footer">
  <span class="footer-orange">Dynasty HQ</span> · Trade values via RosterAudit
</footer>

</body>
</html>
