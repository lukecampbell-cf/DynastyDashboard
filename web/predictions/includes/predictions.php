<?php

declare(strict_types=1);

require_once __DIR__ . '/authorised_users.php';
require_once __DIR__ . '/sleeper.php';

function predictions_resolve_login(string $username, ?callable $request = null, ?string $dataDirectory = null): array
{
    $authorised = predictions_find_authorised_user($username, $dataDirectory);
    if ($authorised === null) {
        throw new DomainException('That Sleeper username is not authorised.');
    }

    // This is intentionally the first point at which a Sleeper request can occur.
    $sleeperUser = predictions_sleeper_user($authorised['sleeper_username'], $request);
    if ($sleeperUser === null) {
        throw new PredictionsSleeperException('Sleeper could not resolve that username.');
    }

    return [
        'sleeper_user_id' => (string) $sleeperUser['user_id'],
        'sleeper_username' => $authorised['sleeper_username'],
        'display_name' => $authorised['display_name'],
    ];
}

function predictions_find_league(array $leagues, string $leagueId): ?array
{
    foreach ($leagues as $league) {
        if (is_array($league) && (string) ($league['league_id'] ?? '') === $leagueId) {
            return $league;
        }
    }
    return null;
}
