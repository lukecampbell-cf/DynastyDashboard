<?php

declare(strict_types=1);

header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: same-origin');
header("Content-Security-Policy: default-src 'self'; style-src 'self' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");

ini_set('session.use_strict_mode', '1');
ini_set('session.use_only_cookies', '1');
session_name('dynasty_hq_predictions');

// Optional deployment-local settings. This file is deliberately untracked so
// hosts such as Plesk do not have to propagate environment variables through
// Apache/nginx into PHP-FPM.
$predictionsLocalConfig = __DIR__ . '/local_config.php';
if (is_file($predictionsLocalConfig)) {
    require $predictionsLocalConfig;
}

function predictions_session_cookie_path(?string $scriptName = null): string
{
    $directory = str_replace('\\', '/', dirname($scriptName ?? (string) ($_SERVER['SCRIPT_NAME'] ?? '/predictions/index.php')));
    return $directory === '/' || $directory === '.' ? '/' : rtrim($directory, '/') . '/';
}

session_set_cookie_params([
    'lifetime' => 0,
    // Scope the cookie to the tool's real mount point (for example
    // /dashboard/predictions/) rather than assuming it is mounted at root.
    'path' => predictions_session_cookie_path(),
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
    if (defined('PREDICTIONS_DB_PATH_OVERRIDE')) {
        $override = trim((string) constant('PREDICTIONS_DB_PATH_OVERRIDE'));
        if ($override !== '') {
            return $override;
        }
    }
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
require_once __DIR__ . '/projection.php';
require_once __DIR__ . '/predictions.php';
require_once __DIR__ . '/cards.php';
