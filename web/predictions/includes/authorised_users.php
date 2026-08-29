<?php

declare(strict_types=1);

require_once __DIR__ . '/json.php';

function predictions_normalise_username(string $username): string
{
    return strtolower(trim($username));
}

function predictions_find_authorised_user(string $username, ?string $dataDirectory = null): ?array
{
    $normalised = predictions_normalise_username($username);
    if ($normalised === '') {
        return null;
    }

    $directory = $dataDirectory ?? predictions_data_directory();
    $config = predictions_load_json($directory . '/authorised_users.json');
    if ($config === null) {
        return null;
    }

    foreach (($config['authorised_users'] ?? []) as $user) {
        if (!is_array($user) || ($user['enabled'] ?? false) !== true) {
            continue;
        }
        if (hash_equals(predictions_normalise_username((string) ($user['sleeper_username'] ?? '')), $normalised)) {
            return [
                'sleeper_username' => (string) $user['sleeper_username'],
                'display_name' => trim((string) ($user['display_name'] ?? $user['sleeper_username'])),
            ];
        }
    }

    return null;
}
