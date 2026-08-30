<?php

declare(strict_types=1);

$fixtureDir = sys_get_temp_dir() . '/trade-calculator-' . bin2hex(random_bytes(6));
if (!mkdir($fixtureDir)) {
    throw new RuntimeException('Could not create fixture directory');
}

$directory = [
    '4046' => [
        'name' => 'Test Player',
        'position' => 'RB',
        'team' => 'CHI',
        'values' => [
            'sf' => ['value' => 5000, 'tier' => 'Starter', 'source' => 'rosteraudit'],
            '1qb' => ['value' => 4000, 'tier' => 'Starter', 'source' => 'rosteraudit'],
        ],
    ],
];
$tradeValues = [
    'fetched_at' => '2026-08-30T10:00:00Z',
    'formats' => [
        'sf' => ['tier_chart' => [['label' => '2027 Early 1st', 'min_value' => 6000]]],
        '1qb' => ['tier_chart' => []],
    ],
];
file_put_contents($fixtureDir . '/player_directory.json', json_encode($directory, JSON_THROW_ON_ERROR));
file_put_contents($fixtureDir . '/trade_values.json', json_encode($tradeValues, JSON_THROW_ON_ERROR));

putenv('DASHBOARD_DATA_DIR=' . $fixtureDir);
$_GET = ['format' => 'sf', 'player' => '4046'];
ob_start();
require __DIR__ . '/../web/trade_calculator.php';
$html = ob_get_clean();

$checks = [
    'external stylesheet' => str_contains($html, 'href="assets/trade-calculator.css"'),
    'external script' => str_contains($html, 'src="assets/trade-calculator.js"'),
    'JSON bootstrap' => str_contains($html, 'id="trade-calculator-data"'),
    'preselected player' => str_contains($html, 'Test Player'),
    'no inline style block' => !str_contains($html, '<style>'),
    'no inline event handler' => !str_contains($html, 'onclick='),
];

$failures = 0;
foreach ($checks as $label => $passed) {
    echo ($passed ? '  ok   - ' : '  FAIL - ') . $label . "\n";
    $failures += $passed ? 0 : 1;
}

unlink($fixtureDir . '/player_directory.json');
unlink($fixtureDir . '/trade_values.json');
rmdir($fixtureDir);
echo "\n" . count($checks) . " checks, $failures failure(s)\n";
exit($failures === 0 ? 0 : 1);
