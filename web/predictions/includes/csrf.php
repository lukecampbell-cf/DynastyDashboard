<?php

declare(strict_types=1);

function predictions_csrf_token(): string
{
    if (!isset($_SESSION['predictions_csrf']) || !is_string($_SESSION['predictions_csrf'])) {
        $_SESSION['predictions_csrf'] = bin2hex(random_bytes(32));
    }
    return $_SESSION['predictions_csrf'];
}

function predictions_verify_csrf(?string $token): bool
{
    return is_string($token)
        && isset($_SESSION['predictions_csrf'])
        && is_string($_SESSION['predictions_csrf'])
        && hash_equals($_SESSION['predictions_csrf'], $token);
}
