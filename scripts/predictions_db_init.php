<?php

declare(strict_types=1);

require dirname(__DIR__) . '/web/predictions/includes/bootstrap.php';

$pdo = predictions_database();
echo 'Predictions database ready at ' . predictions_database_path() . PHP_EOL;
