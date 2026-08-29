<?php

declare(strict_types=1);

function predictions_load_json(string $path): ?array
{
    if (!is_file($path) || !is_readable($path)) {
        return null;
    }

    $raw = file_get_contents($path);
    if ($raw === false) {
        return null;
    }

    try {
        $decoded = json_decode($raw, true, 512, JSON_THROW_ON_ERROR);
    } catch (JsonException) {
        return null;
    }

    return is_array($decoded) ? $decoded : null;
}

function predictions_write_json_atomic(string $path, array $data): void
{
    $directory = dirname($path);
    if (!is_dir($directory) || !is_writable($directory)) {
        throw new RuntimeException('Predictions data directory is not writable.');
    }

    $temporary = tempnam($directory, '.predictions-');
    if ($temporary === false) {
        throw new RuntimeException('Could not create a temporary cache file.');
    }

    try {
        $json = json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);
        if (file_put_contents($temporary, $json . "\n", LOCK_EX) === false || !rename($temporary, $path)) {
            throw new RuntimeException('Could not save the Sleeper player cache.');
        }
        chmod($path, 0600);
    } finally {
        if (is_file($temporary)) {
            unlink($temporary);
        }
    }
}
