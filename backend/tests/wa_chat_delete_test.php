<?php
declare(strict_types=1);

require __DIR__ . '/../bootstrap.php';

use App\Services\WhatsApp\WhatsAppChatService;

function check(bool $condition, string $message): void
{
    if (!$condition) throw new RuntimeException('FAILED: ' . $message);
}

$db = new PDO('sqlite::memory:');
$db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
App\Support\Database::migrate($db);
App\Services\WhatsApp\WhatsAppTables::ensure($db);

$chats = new WhatsAppChatService($db);
$now = time();
$at = static fn (int $secondsAgo): string => gmdate('c', $now - $secondsAgo);

// 1. Delete a whole chat: old history goes, and a re-import does not bring it back.
$phoneA = '+919876500001';
$thread = [
    'chat' => ['phone' => $phoneA, 'name' => 'Asha'],
    'messages' => [
        ['direction' => 'in', 'body' => 'Hi', 'created_at' => $at(3 * 3600), 'id' => 'a1', 'status' => ''],
        ['direction' => 'out', 'body' => 'Hello', 'created_at' => $at(3 * 3600 - 60), 'id' => 'a2', 'status' => 'read'],
        ['direction' => 'in', 'body' => 'Reply', 'created_at' => $at(7200), 'id' => 'a3', 'status' => ''],
    ],
];
$chats->remember($thread);
$list = $chats->listing([]);
check(count($list['chats']) === 1, 'Chat A should exist before delete');
$chatA = (int) $list['chats'][0]['id'];
check(count($chats->thread($chatA)['messages']) === 3, 'Chat A should have 3 messages before delete');

$result = $chats->deleteChat($phoneA);
check($result['chats'] === 1 && $result['messages'] === 3, 'deleteChat should remove 1 chat and 3 messages');
check(count($chats->listing([])['chats']) === 0, 'No chats should remain after deleteChat');

$chats->remember($thread);
check(count($chats->listing([])['chats']) === 0, 'Re-import of old history must not bring the chat back');

$visible = $chats->visibleMessages($phoneA, $thread['messages']);
check($visible === [], 'Old messages of a deleted chat must be hidden in thread view');

// A reply that arrives after the deletion is shown again.
$newReply = ['direction' => 'in', 'body' => 'Back again', 'created_at' => gmdate('c', $now + 600), 'id' => 'a4', 'status' => ''];
$chats->remember(['chat' => ['phone' => $phoneA, 'name' => 'Asha'], 'messages' => array_merge($thread['messages'], [$newReply])]);
$list = $chats->listing([]);
check(count($list['chats']) === 1, 'A new reply should bring the chat back');
$msgs = $chats->thread((int) $list['chats'][0]['id'])['messages'];
check(count($msgs) === 1 && $msgs[0]['body'] === 'Back again', 'Only the new reply should show after a chat delete');

// 2. Delete one message: it stays gone on re-import and on outgoing sync.
$phoneB = '+919876500002';
$threadB = [
    'chat' => ['phone' => $phoneB, 'name' => 'Ravi'],
    'messages' => [
        ['direction' => 'in', 'body' => 'First', 'created_at' => $at(7200), 'id' => 'b1', 'status' => ''],
        ['direction' => 'out', 'body' => 'Middle', 'created_at' => $at(3600), 'id' => 'b2', 'status' => 'delivered'],
        ['direction' => 'in', 'body' => 'Last', 'created_at' => $at(1800), 'id' => 'b3', 'status' => ''],
    ],
];
$chats->remember($threadB);
$listB = $chats->listing([]);
$chatB = (int) current(array_filter($listB['chats'], static fn (array $c): bool => str_ends_with($c['phone'], '500002')))['id'];

$one = $chats->deleteMessage($phoneB, 'out', 'Middle', $at(3600));
check($one['messages'] === 1, 'deleteMessage should remove exactly one message');
$afterDelete = $chats->thread($chatB)['messages'];
check(count($afterDelete) === 2, 'Two messages should remain after deleting the middle one');
check(array_column($afterDelete, 'body') === ['First', 'Last'], 'Remaining messages should be First and Last');

$chats->remember($threadB);
check(count($chats->thread($chatB)['messages']) === 2, 'Re-import must not restore the deleted message');

$sync = $chats->noteOutgoing($phoneB, 'Middle', $at(3600));
check($sync === 0, 'Outgoing sync must not restore the deleted message');

$sameTextLater = $chats->noteOutgoing($phoneB, 'Middle', $at(60));
check($sameTextLater > 0, 'The same text sent at a different time must still be saved');
check(count($chats->thread($chatB)['messages']) === 3, 'The later message should be the only new one');

// 3. Deleting the last message removes the chat from the list.
$phoneC = '+919876500003';
$chats->remember(['chat' => ['phone' => $phoneC, 'name' => 'Meera'], 'messages' => [
    ['direction' => 'in', 'body' => 'Solo', 'created_at' => $at(900), 'id' => 'c1', 'status' => ''],
]]);
$listC = $chats->listing([]);
$chatC = (int) current(array_filter($listC['chats'], static fn (array $c): bool => str_ends_with($c['phone'], '500003')))['id'];
$chats->deleteMessage($phoneC, 'in', 'Solo', $at(900));
$remainingC = array_filter($chats->listing([])['chats'], static fn (array $c): bool => str_ends_with($c['phone'], '500003'));
check($remainingC === [], 'A chat left with no messages should be removed');

// 4. Input validation.
try {
    $chats->deleteChat('');
    check(false, 'deleteChat with empty phone should throw');
} catch (InvalidArgumentException $e) {
    check(true, 'empty phone rejected');
}
try {
    $chats->deleteMessage($phoneB, 'in', '', $at(10));
    check(false, 'deleteMessage with empty body should throw');
} catch (InvalidArgumentException $e) {
    check(true, 'empty body rejected');
}

echo "All WhatsApp chat delete tests passed.\n";
