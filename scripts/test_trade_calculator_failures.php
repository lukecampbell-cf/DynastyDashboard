<?php

declare(strict_types=1);

require __DIR__ . '/../web/includes/trade_calculator_data.php';

$failures = 0;
function check_failure_path(string $label, bool $condition): void
{
    global $failures;
    echo ($condition ? '  ok   - ' : '  FAIL - ') . $label . "\n";
    $failures += $condition ? 0 : 1;
}

$fixtureDir = sys_get_temp_dir() . '/trade-calculator-failures-' . bin2hex(random_bytes(6));
mkdir($fixtureDir);
file_put_contents($fixtureDir . '/player_directory.json', '{}');

set_error_handler(static function (int $severity, string $message): never {
    throw new ErrorException($message, 0, $severity);
});
try {
    $view = trade_calculator_view_model($fixtureDir, []);
    check_failure_path('missing trade values has no fetched date', $view['fetchedAt'] === null);
    check_failure_path('missing trade values produces friendly error', str_contains($view['loadError'], 'trade_values.json'));
} finally {
    restore_error_handler();
}

$view['loadError'] = null;
$view['bootstrap']['players']['sf']['bad'] = ['name' => "\xB1\x31"];
$json = trade_calculator_encode_bootstrap($view);
check_failure_path('invalid UTF-8 uses valid fallback JSON', json_decode($json, true) !== null);
check_failure_path('invalid UTF-8 becomes a friendly load error', str_contains($view['loadError'], 'invalid text'));

unlink($fixtureDir . '/player_directory.json');
rmdir($fixtureDir);
echo "\n$failures failure(s)\n";
exit($failures === 0 ? 0 : 1);
