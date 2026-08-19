<?php
/**
 * Trade Calculator Validation Test
 * Plain-assertion coverage for trade_calculator_lib.php's resolve_preselect()
 * — the function that decides whether a ?player=&format= query pair from
 * dashboard_agent.py's "Explore Trade" link (or anyone else's URL bar) is
 * safe to preselect.
 *
 * Deliberately NOT a phpunit test: this repo has no PHP test framework or
 * composer dependency today (mirrors scripts/scraper_smoke_test.py's own
 * "deliberately not test_*.py, run manually" convention for the Python
 * side) — adding one for a single small pure function would be
 * disproportionate infrastructure for what it buys.
 *
 * Run directly:  php scripts/test_trade_calculator_validation.php
 * Exit code is 0 only if every assertion passes.
 */

declare(strict_types=1);

require __DIR__ . '/../trade_calculator_lib.php';

$failures = 0;
$checked = 0;

function check(string $label, bool $condition): void {
    global $failures, $checked;
    $checked++;
    if ($condition) {
        echo "  ok   - $label\n";
    } else {
        echo "  FAIL - $label\n";
        $failures++;
    }
}

$playerPool = [
    'sf' => [
        '4046' => ['name' => 'Test Player', 'position' => 'RB', 'team' => 'CHI', 'value' => 5000, 'tier' => 'Mid 1st', 'source' => 'rosteraudit'],
    ],
    '1qb' => [
        '4046' => ['name' => 'Test Player', 'position' => 'RB', 'team' => 'CHI', 'value' => 4000, 'tier' => 'Late 1st', 'source' => 'rosteraudit'],
    ],
];

echo "resolve_preselect() validation\n";

// Valid id + valid format passes through untouched.
$r = resolve_preselect($playerPool, 'sf', '4046');
check('valid sf + known id resolves', $r['format'] === 'sf' && $r['playerId'] === '4046');

$r = resolve_preselect($playerPool, '1qb', '4046');
check('valid 1qb + known id resolves', $r['format'] === '1qb' && $r['playerId'] === '4046');

// Unknown id (not a key in the pool) is dropped, not trusted just because
// it's a plausible-looking numeric string.
$r = resolve_preselect($playerPool, 'sf', '9999999');
check('unknown numeric id is dropped', $r['playerId'] === null);

// Malformed / injection-shaped ids are dropped outright.
foreach ([
    '"); alert(1); //',
    '<script>alert(1)</script>',
    '../../etc/passwd',
    str_repeat('4046', 500), // oversized
    '',
] as $badId) {
    $r = resolve_preselect($playerPool, 'sf', $badId);
    check('malformed id dropped: ' . substr($badId, 0, 30), $r['playerId'] === null);
}

// Invalid/unexpected format strings fall back to 'sf', never passed through raw.
foreach (['SF', '2qb', 'drop table players;--', '', null] as $badFormat) {
    $r = resolve_preselect($playerPool, $badFormat, '4046');
    check('invalid format falls back to sf: ' . var_export($badFormat, true), $r['format'] === 'sf');
}

// No query params at all -> no preselect, default format, and no complaint:
// nobody asked for a player, so there's nothing to report as missing.
$r = resolve_preselect($playerPool, null, null);
check('no params: no preselect, default format', $r['format'] === 'sf' && $r['playerId'] === null);
check('no params: notFound stays false', $r['notFound'] === false);
$r = resolve_preselect($playerPool, 'sf', '');
check('empty player param: notFound stays false', $r['notFound'] === false);

// An id that was asked for but couldn't be resolved is reported, so the page
// can say so rather than loading a silently empty Side A.
$r = resolve_preselect($playerPool, 'sf', '9999999');
check('unknown id sets notFound', $r['notFound'] === true);
$r = resolve_preselect($playerPool, 'sf', '4046');
check('resolved id leaves notFound false', $r['notFound'] === false);

// A player only priced in one format isn't wrongly preselected in the other
// if it's genuinely a different id space (defensive: an id valid in sf
// should still resolve correctly for 1qb only if it's also a key there —
// this pool has it in both, so this checks the negative case explicitly).
$oneFormatPool = ['sf' => ['555' => ['name' => 'X']], '1qb' => []];
$r = resolve_preselect($oneFormatPool, '1qb', '555');
check('id present only in sf is not preselected under 1qb', $r['playerId'] === null);

echo "\n$checked checks, $failures failure(s)\n";
exit($failures === 0 ? 0 : 1);
