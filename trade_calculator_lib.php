<?php
/**
 * Trade Calculator — shared validation logic
 *
 * Pulled out of trade_calculator.php so the pure, security-relevant bit
 * (resolving a possibly attacker-controlled ?player=&format= query pair
 * into something safe to preselect) can be exercised in isolation — by
 * scripts/test_trade_calculator_validation.php — without executing the
 * rest of trade_calculator.php, which mixes HTML output into the same
 * file top-to-bottom and has no other test seam.
 */

declare(strict_types=1);

/**
 * Resolve dashboard_agent.py's "Explore Trade" deep link
 * (trade_calculator.php?player=<id>&format=<sf|1qb>) into a trusted format
 * and, only if it's an actual key in that format's already-loaded player
 * pool, a player id to preselect.
 *
 * `format` is validated by exact match against the known set — anything
 * else (missing, malformed, unexpected value) falls back to 'sf', the
 * page's own default. `player` is trusted only if it's a real existing key
 * in $playerPool[format] — an unknown, malformed, or injection-shaped id
 * (quotes, script tags, path segments, oversized strings) is silently
 * dropped rather than ever being echoed back or trusted on the strength of
 * looking id-shaped.
 *
 * @param array<string, array<string, array>> $playerPool format => (id => player data)
 * @return array{format: string, playerId: ?string}
 */
function resolve_preselect(array $playerPool, ?string $rawFormat, ?string $rawPlayerId): array {
    $format = in_array($rawFormat, ['sf', '1qb'], true) ? $rawFormat : 'sf';

    $playerId = null;
    if ($rawPlayerId !== null && isset($playerPool[$format][$rawPlayerId])) {
        $playerId = $rawPlayerId;
    }

    return ['format' => $format, 'playerId' => $playerId];
}
