<?php

declare(strict_types=1);

function predictions_card_with_picks(PDO $pdo, int $cardId, ?string $userId = null): ?array
{
    $sql = 'SELECT * FROM prediction_cards WHERE id = ?';
    $params = [$cardId];
    if ($userId !== null) {
        $sql .= ' AND sleeper_user_id = ?';
        $params[] = $userId;
    }
    $statement = $pdo->prepare($sql);
    $statement->execute($params);
    $card = $statement->fetch();
    if (!is_array($card)) {
        return null;
    }
    $card['picks'] = predictions_card_picks($pdo, (int) $card['id']);
    return $card;
}

function predictions_user_history(PDO $pdo, string $userId): array
{
    $statement = $pdo->prepare('SELECT c.*,
        COUNT(p.id) AS pick_count,
        SUM(CASE WHEN p.result = \'WIN\' THEN 1 ELSE 0 END) AS wins,
        SUM(CASE WHEN p.result IN (\'WIN\', \'LOSS\', \'PUSH\') THEN 1 ELSE 0 END) AS settled_picks
        FROM prediction_cards c LEFT JOIN predictions p ON p.card_id = c.id
        WHERE c.sleeper_user_id = ? GROUP BY c.id
        ORDER BY c.season DESC, c.week DESC, c.submitted_at DESC');
    $statement->execute([$userId]);
    return $statement->fetchAll();
}

function predictions_leaderboard(PDO $pdo): array
{
    return $pdo->query('SELECT display_name, SUM(card_points) AS total_points,
        SUM(wins) AS wins, SUM(settled_picks) AS settled_picks
        FROM (SELECT c.id, c.sleeper_user_id, c.display_name, c.total_points AS card_points,
            SUM(CASE WHEN p.result = \'WIN\' THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN p.result IN (\'WIN\', \'LOSS\', \'PUSH\') THEN 1 ELSE 0 END) AS settled_picks
            FROM prediction_cards c JOIN predictions p ON p.card_id = c.id GROUP BY c.id)
        GROUP BY sleeper_user_id, display_name HAVING SUM(settled_picks) > 0
        ORDER BY total_points DESC, wins DESC, settled_picks ASC, display_name ASC')->fetchAll();
}
