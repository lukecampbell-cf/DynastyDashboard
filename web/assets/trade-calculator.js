(() => {
'use strict';

const dataNode = document.getElementById('trade-calculator-data');
if (!dataNode) return;

const config = JSON.parse(dataNode.textContent);
const PLAYERS = config.players;
const PICK_TIERS = config.pickTiers;
const BTA_EMAIL = config.btaEmail;
const PRESELECT = config.preselect;
const VERDICT_YES_MIN = config.thresholds.yesMin;
const VERDICT_CLOSE_YES_MIN = config.thresholds.closeYesMin;
const VERDICT_CLOSE_NO_MIN = config.thresholds.closeNoMin;
const VERDICT_NO_MIN = config.thresholds.noMin;

// A load error renders no calculator controls; the server-side error card is complete.
if (!document.getElementById('search-A')) return;

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
document.querySelectorAll('[data-format]').forEach(button => {
  button.addEventListener('click', () => switchFormat(button.dataset.format));
});

if (PRESELECT.playerId) {
  addAsset('A', { type: 'player', id: PRESELECT.playerId });
}
})();
