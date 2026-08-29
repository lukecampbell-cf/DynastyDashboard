<?php

declare(strict_types=1);

// Copy to local_config.php on the server. The real file is intentionally
// ignored by Git so deployment-specific paths are not committed.
define(
    'PREDICTIONS_DB_PATH_OVERRIDE',
    '/var/www/vhosts/example.test/private/predictions/predictions.sqlite'
);
