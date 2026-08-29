<?php

declare(strict_types=1);

header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: same-origin');
header("Content-Security-Policy: default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");

ini_set('session.use_strict_mode', '1');
ini_set('session.use_only_cookies', '1');
session_name('dynasty_hq_predictions');
session_set_cookie_params([
    'lifetime' => 0,
    'path' => '/predictions/',
    'secure' => !empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off',
    'httponly' => true,
    'samesite' => 'Lax',
]);
if (session_status() !== PHP_SESSION_ACTIVE) {
    session_start();
}

function predictions_data_directory(): string
{
    $configured = getenv('DASHBOARD_DATA_DIR');
    return rtrim($configured !== false && $configured !== '' ? $configured : dirname(__DIR__, 3), DIRECTORY_SEPARATOR);
}

function predictions_database_path(): string
{
    $configured = getenv('PREDICTIONS_DB_PATH');
    return $configured !== false && $configured !== '' ? $configured : predictions_data_directory() . '/predictions.sqlite';
}

function predictions_escape(mixed $value): string
{
    return htmlspecialchars((string) $value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

function predictions_redirect(string $location): never
{
    header('Location: ' . $location, true, 303);
    exit;
}

require_once __DIR__ . '/json.php';
require_once __DIR__ . '/csrf.php';
require_once __DIR__ . '/database.php';
require_once __DIR__ . '/predictions.php';
