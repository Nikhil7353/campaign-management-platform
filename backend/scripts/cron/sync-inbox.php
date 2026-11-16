<?php
declare(strict_types=1);
require dirname(__DIR__, 2) . '/bootstrap.php';

use App\Support\Database;
use App\Services\Email\ImapClient;
use App\Services\MailAccountService;

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
$lock = fopen(dirname(__DIR__, 2) . '/storage/app/private/imap-worker.lock', 'c');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) exit("IMAP sync already running.\n");
$db = Database::connect();
Database::migrate($db);

$totalNew = 0;
$errors = 0;
try {
    $accounts = MailAccountService::getAll($db, true);
    if (!empty($accounts)) {
        foreach ($accounts as $acct) {
            if (($acct['status'] ?? '') !== 'active') continue;
            try {
                $result = (new ImapClient($db, $acct))->sync(50);
                $totalNew += (int) ($result['new_count'] ?? 0);
            } catch (Throwable $e) {
                $errors++;
                fwrite(STDERR, "IMAP sync failed for account {$acct['id']}: {$e->getMessage()}\n");
            }
        }
    } else {
        $result = (new ImapClient($db))->sync(50);
        $totalNew = (int) ($result['new_count'] ?? 0);
    }
    echo "IMAP Sync completed: {$totalNew} new messages fetched" . ($errors ? " ({$errors} mailbox error(s))" : '') . ".\n";
} catch (Throwable $e) {
    fwrite(STDERR, "IMAP Sync failed: " . $e->getMessage() . "\n");
}
flock($lock, LOCK_UN);
fclose($lock);
