<?php

declare(strict_types=1);

const PREDICTIONS_PROJECTION_MODEL_VERSION = 'v0-heuristic';

function predictions_projection_config(): array
{
    return [
        'position_baselines' => ['QB' => 17.0, 'RB' => 10.0, 'WR' => 10.0, 'TE' => 7.0],
        'rank_bands' => [
            'QB' => [[5, 6.0], [12, 3.0], [20, 1.0], [30, 0.0], [PHP_INT_MAX, -2.0]],
            'RB' => [[5, 7.0], [12, 4.5], [24, 2.0], [36, 0.0], [48, -2.0], [PHP_INT_MAX, -4.0]],
            'WR' => [[5, 8.0], [12, 5.0], [24, 2.0], [36, 0.0], [48, -2.0], [PHP_INT_MAX, -4.0]],
            'TE' => [[5, 6.0], [12, 3.0], [20, 1.0], [30, 0.0], [PHP_INT_MAX, -2.0]],
        ],
        'trade_value_bands' => [[10.0, -1.5], [25.0, -0.75], [75.0, 0.0], [90.0, 0.75], [101.0, 1.5]],
        'roster_role_adjustments' => ['starter' => 0.5, 'middle' => 0.0, 'fringe' => -0.5],
        'injury_adjustments' => [
            '' => 0.0, 'healthy' => 0.0, 'active' => 0.0,
            'probable' => -0.5, 'minor' => -0.5,
            'questionable' => -1.5, 'doubtful' => -4.0,
        ],
        'ineligible_injury_statuses' => ['out', 'ir', 'pup'],
    ];
}
