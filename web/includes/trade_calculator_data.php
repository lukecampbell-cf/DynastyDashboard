<?php

declare(strict_types=1);

require_once dirname(__DIR__) . '/trade_calculator_lib.php';

const TRADE_VERDICT_YES_MIN = 0.90;
const TRADE_VERDICT_CLOSE_YES_MIN = 0.75;
const TRADE_VERDICT_CLOSE_NO_MIN = 0.55;
const TRADE_VERDICT_NO_MIN = 0.30;
const TRADE_BTA_EMAIL = 'trades@bullshit-trade-association.co.uk';

function trade_calculator_load_json(string $path): ?array
{
    if (!is_file($path)) return null;
    $raw = file_get_contents($path);
    if ($raw === false) return null;
    $decoded = json_decode($raw, true);
    return is_array($decoded) ? $decoded : null;
}

function trade_calculator_build_player_pool(array $directory): array
{
    $pool = ['sf' => [], '1qb' => []];
    foreach ($directory as $id => $player) {
        foreach (['sf', '1qb'] as $format) {
            $value = $player['values'][$format] ?? [
                'value' => 0,
                'tier' => 'Deep Stash',
                'source' => 'unranked',
            ];
            $pool[$format][$id] = [
                'name' => $player['name'] ?? 'Unknown',
                'position' => $player['position'] ?? 'UNK',
                'team' => $player['team'] ?? 'FA',
                'value' => (float) ($value['value'] ?? 0),
                'tier' => $value['tier'] ?? null,
                'source' => $value['source'] ?? 'rosteraudit',
            ];
        }
    }
    return $pool;
}

function trade_calculator_build_pick_tiers(array $tradeValues): array
{
    $tiers = [];
    foreach (['sf', '1qb'] as $format) {
        $tiers[$format] = $tradeValues['formats'][$format]['tier_chart'] ?? [];
    }
    return $tiers;
}

function trade_calculator_view_model(string $dataDir, array $query): array
{
    $directory = trade_calculator_load_json($dataDir . '/player_directory.json');
    $tradeValues = trade_calculator_load_json($dataDir . '/trade_values.json');
    $loadError = null;
    if ($directory === null) {
        $loadError = 'player_directory.json not found or unreadable at ' . $dataDir . ' — run the full pipeline at least once first.';
    } elseif ($tradeValues === null) {
        $loadError = 'trade_values.json not found or unreadable at ' . $dataDir . ' — run the trade-value step or full pipeline at least once first.';
    }
    $players = $directory === null ? ['sf' => [], '1qb' => []] : trade_calculator_build_player_pool($directory);
    $pickTiers = $tradeValues === null ? ['sf' => [], '1qb' => []] : trade_calculator_build_pick_tiers($tradeValues);
    $preselect = resolve_preselect($players, $query['format'] ?? null, $query['player'] ?? null);
    return [
        'players' => $players,
        'pickTiers' => $pickTiers,
        'fetchedAt' => $tradeValues['fetched_at'] ?? null,
        'loadError' => $loadError,
        'preselect' => $preselect,
        'bootstrap' => [
            'players' => $players,
            'pickTiers' => $pickTiers,
            'btaEmail' => TRADE_BTA_EMAIL,
            'thresholds' => ['yesMin' => TRADE_VERDICT_YES_MIN, 'closeYesMin' => TRADE_VERDICT_CLOSE_YES_MIN, 'closeNoMin' => TRADE_VERDICT_CLOSE_NO_MIN, 'noMin' => TRADE_VERDICT_NO_MIN],
            'preselect' => $preselect,
        ],
    ];
}
