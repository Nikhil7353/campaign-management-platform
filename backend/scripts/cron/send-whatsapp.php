<?php
declare(strict_types=1);
require dirname(__DIR__, 2) . '/bootstrap.php';

use App\Services\WhatsApp\DeviceLink;
use App\Services\WhatsApp\WhatsAppQueue;
use App\Support\Database;

if (PHP_SAPI !== 'cli') { http_response_code(404); exit; }
$lock = fopen(dirname(__DIR__, 2) . '/storage/app/private/whatsapp-worker.lock', 'c');
if (!$lock || !flock($lock, LOCK_EX | LOCK_NB)) exit("Worker already running.\n");
$db = Database::connect();
$result = (new WhatsAppQueue($db, new DeviceLink($db)))->run();
echo "WhatsApp: {$result['sent']} sent, {$result['failed']} failed, {$result['waiting']} waiting\n";
if ($result['error']) fwrite(STDERR, "Phone service: {$result['error']}\n");
flock($lock, LOCK_UN);
fclose($lock);
