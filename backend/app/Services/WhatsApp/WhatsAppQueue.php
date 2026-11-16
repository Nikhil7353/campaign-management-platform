<?php
declare(strict_types=1);

namespace App\Services\WhatsApp;

use App\Services\Sheets\WhatsAppSheetService;
use DateTimeImmutable;
use DateTimeZone;
use InvalidArgumentException;
use PDO;
use RuntimeException;

/** Shares a WhatsApp sheet's ready rows across its phones and sends each row when it is due. */
final class WhatsAppQueue
{
    public const DAILY = 20;
    private const DEFAULT_TIME = '10:00';

    private DateTimeZone $zone;

    public function __construct(private PDO $db, private DeviceLink $link, private ?WhatsAppSheetService $sheets = null)
    {
        new WhatsAppSentService($db);
        WhatsAppTables::ensure($db);
        $this->zone = new DateTimeZone('Asia/Kolkata');
    }

    public function plan(int $sourceId): array
    {
        return $this->summary($this->build($sourceId));
    }

    public function queue(int $sourceId): array
    {
        $built = $this->build($sourceId);
        $insert = $this->db->prepare(
            'INSERT INTO whatsapp_sends (phone, name, message, status, error, account_name, device_id, due_at, source_id, record_id, created_at) VALUES (?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?)'
        );
        $this->db->beginTransaction();
        try {
            foreach ($built['slots'] as $slot) {
                $row = $slot['row'];
                $due = gmdate('c', $slot['at']);
                $insert->execute([
                    (string) $row['values']['phone'],
                    mb_substr(trim((string) ($row['values']['name'] ?? '')), 0, 160),
                    (string) $row['values']['message'],
                    'waiting',
                    $slot['phone']['name'],
                    $slot['phone']['id'],
                    $due,
                    $sourceId,
                    mb_substr((string) $row['record_id'], 0, 160),
                    $due,
                ]);
            }
            $this->db->commit();
        } catch (\Throwable $e) {
            $this->db->rollBack();
            throw $e;
        }
        return $this->summary($built) + ['queued' => count($built['slots'])];
    }

    /** Send at most one due row per phone, keeping the gap and the daily cap. */
    public function run(): array
    {
        $now = time();
        $rows = $this->db->query(
            "SELECT s.id, s.phone, s.message, s.device_id, s.account_name, s.due_at, src.delay_minutes FROM whatsapp_sends s LEFT JOIN whatsapp_sheet_sources src ON src.id = s.source_id WHERE s.status = 'waiting' AND s.due_at IS NOT NULL AND s.device_id IS NOT NULL ORDER BY s.due_at ASC, s.id ASC"
        )->fetchAll();
        $result = ['sent' => 0, 'failed' => 0, 'not_ready' => [], 'error' => null];
        if (!$rows) return $result + $this->waitingTotals();
        $due = array_filter($rows, static fn ($row) => is_array($row) && (strtotime((string) $row['due_at']) ?: PHP_INT_MAX) <= $now);
        if (!$due) return $result + $this->waitingTotals();
        try {
            $ready = [];
            foreach ($this->link->roster()['devices'] as $device) {
                if (($device['status'] ?? '') === 'ready') $ready[(string) $device['id']] = true;
            }
        } catch (RuntimeException $e) {
            $result['error'] = $e->getMessage();
            return $result + $this->waitingTotals();
        }
        $chats = new WhatsAppChatService($this->db);
        $seen = [];
        foreach ($due as $row) {
            $deviceId = (string) $row['device_id'];
            if (isset($seen[$deviceId])) continue;
            $seen[$deviceId] = true;
            if (!isset($ready[$deviceId])) {
                $result['not_ready'][] = (string) $row['account_name'];
                continue;
            }
            $gap = max(1, (int) ($row['delay_minutes'] ?? 1)) * 60;
            [$last, $today] = $this->sentStats($deviceId, $now);
            if ($last !== 0 && $last + $gap > $now) continue;
            if ($today >= self::DAILY) continue;
            $claim = $this->db->prepare("UPDATE whatsapp_sends SET status = 'sending' WHERE id = ? AND status = 'waiting'");
            $claim->execute([(int) $row['id']]);
            if ($claim->rowCount() !== 1) continue;
            $digits = preg_replace('/\D+/', '', (string) $row['phone']) ?? '';
            try {
                $from = $this->link->sendText($digits, (string) $row['message'], $deviceId);
                $at = gmdate('c');
                $this->db->prepare("UPDATE whatsapp_sends SET status = 'sent', error = NULL, created_at = ?, account_name = ? WHERE id = ?")
                    ->execute([$at, $from['name'] !== '' ? $from['name'] : (string) $row['account_name'], (int) $row['id']]);
                $chats->noteOutgoing((string) $row['phone'], (string) $row['message'], $at, $deviceId);
                $result['sent']++;
            } catch (RuntimeException $e) {
                $this->db->prepare("UPDATE whatsapp_sends SET status = 'failed', error = ?, created_at = ? WHERE id = ?")
                    ->execute([mb_substr($e->getMessage(), 0, 500), gmdate('c'), (int) $row['id']]);
                $result['failed']++;
            }
        }
        $result['not_ready'] = array_values(array_unique($result['not_ready']));
        return $result + $this->waitingTotals();
    }

    public function progress(int $sourceId): array
    {
        $rows = $this->latestRows($sourceId);
        $totals = ['total' => 0, 'waiting' => 0, 'sent' => 0, 'failed' => 0];
        $phones = [];
        $log = [];
        foreach ($rows as $row) {
            $state = $this->state((string) $row['status']);
            $totals['total']++;
            $totals[$state]++;
            $key = (string) ($row['device_id'] ?? '');
            $phones[$key] ??= ['id' => $key, 'name' => (string) $row['account_name'], 'waiting' => 0, 'sent' => 0, 'failed' => 0, 'next_at' => null];
            $phones[$key][$state]++;
            if ($state === 'waiting') {
                $at = (string) ($row['due_at'] ?? '');
                if ($at !== '' && ($phones[$key]['next_at'] === null || strtotime($at) < strtotime($phones[$key]['next_at']))) {
                    $phones[$key]['next_at'] = $at;
                }
            } else {
                $log[] = [
                    'name' => (string) $row['name'],
                    'phone' => (string) $row['phone'],
                    'from' => (string) $row['account_name'],
                    'status' => $state,
                    'error' => $state === 'failed' ? (string) ($row['error'] ?? '') : '',
                    'at' => (string) $row['created_at'],
                ];
            }
        }
        usort($log, static fn ($a, $b) => (strtotime($b['at']) ?: 0) <=> (strtotime($a['at']) ?: 0));
        return $totals + ['phones' => array_values($phones), 'log' => array_slice($log, 0, 12)];
    }

    /** @return array<string, string> record id => pending, sent, or failed */
    public function statuses(int $sourceId): array
    {
        $map = [];
        foreach ($this->latestRows($sourceId) as $row) {
            $state = $this->state((string) $row['status']);
            $map[(string) $row['record_id']] = $state === 'waiting' ? 'pending' : $state;
        }
        return $map;
    }

    private function build(int $sourceId): array
    {
        if ($this->sheets === null) throw new RuntimeException('Open the sheet before sending.');
        $preview = $this->sheets->preview($sourceId);
        $source = $preview['source'];
        $taken = $this->statuses($sourceId);
        $rows = [];
        $skipped = 0;
        foreach ($preview['records'] as $row) {
            if (!empty($row['issues'])) continue;
            $state = $taken[(string) $row['record_id']] ?? '';
            if ($state === 'pending' || $state === 'sent') {
                $skipped++;
                continue;
            }
            $rows[] = $row;
        }
        $gap = (int) ($source['delay_minutes'] ?? 0);
        if ($gap < 1 || $gap > 30) throw new InvalidArgumentException('Choose the gap between messages first.');
        $phones = $this->phones($source);
        if (!$phones) throw new InvalidArgumentException('None of the chosen phones is ready. Pair one on Linked device.');
        if (!$rows) throw new InvalidArgumentException('Every ready row is already waiting or sent.');
        $time = $this->startTime($source);
        $start = $this->startAt($source, $time);
        return [
            'slots' => $this->assign($rows, $phones, $start, $gap, $time),
            'phones' => $phones,
            'start' => $start,
            'gap' => $gap,
            'skipped' => $skipped,
        ];
    }

    /** @return list<array{id: string, name: string}> */
    private function phones(array $source): array
    {
        $picked = $source['sender_mode'] === 'pick' ? $source['device_ids'] : null;
        $phones = [];
        foreach ($this->link->roster()['devices'] as $device) {
            if (($device['status'] ?? '') !== 'ready') continue;
            $id = (string) ($device['id'] ?? '');
            if ($id === '' || ($picked !== null && !in_array($id, $picked, true))) continue;
            $phones[] = ['id' => $id, 'name' => (string) ($device['name'] ?? 'Phone')];
        }
        return $phones;
    }

    private function startTime(array $source): string
    {
        $time = trim((string) ($source['start_time'] ?? ''));
        return preg_match('/^([01]\d|2[0-3]):[0-5]\d$/', $time) ? $time : self::DEFAULT_TIME;
    }

    private function startAt(array $source, string $time): int
    {
        $today = (new DateTimeImmutable('now', $this->zone))->format('Y-m-d');
        $date = trim((string) ($source['start_date'] ?? '')) ?: $today;
        if ($date < $today) throw new InvalidArgumentException('The start day has passed. Choose today or a later day.');
        $at = (new DateTimeImmutable($date . ' ' . $time, $this->zone))->getTimestamp();
        $now = (int) (ceil(time() / 60) * 60);
        return max($at, $now);
    }

    private function assign(array $rows, array $phones, int $start, int $gap, string $time): array
    {
        $usage = $this->usage(array_column($phones, 'id'));
        $cursor = [];
        foreach ($phones as $phone) {
            $at = $start;
            $last = $usage[$phone['id']]['last'] ?? 0;
            if ($last !== 0 && $last + $gap * 60 > $at) $at = $last + $gap * 60;
            $cursor[$phone['id']] = $this->fit($at, $usage[$phone['id']]['days'], $time);
        }
        $slots = [];
        foreach ($rows as $row) {
            $pick = null;
            foreach ($phones as $phone) {
                if ($pick === null || $cursor[$phone['id']] < $cursor[$pick['id']]) $pick = $phone;
            }
            $at = $cursor[$pick['id']];
            $day = $this->day($at);
            $usage[$pick['id']]['days'][$day] = ($usage[$pick['id']]['days'][$day] ?? 0) + 1;
            $slots[] = ['row' => $row, 'phone' => $pick, 'at' => $at];
            $next = $at + $gap * 60;
            if ($this->day($next) !== $day) $next = $this->dayStart($day, 1, $time);
            $cursor[$pick['id']] = $this->fit($next, $usage[$pick['id']]['days'], $time);
        }
        return $slots;
    }

    private function fit(int $at, array $days, string $time): int
    {
        for ($guard = 0; $guard < 400; $guard++) {
            $day = $this->day($at);
            if (($days[$day] ?? 0) < self::DAILY) return $at;
            $at = $this->dayStart($day, 1, $time);
        }
        return $at;
    }

    /** @param list<string> $ids */
    private function usage(array $ids): array
    {
        $usage = [];
        foreach ($ids as $id) $usage[$id] = ['days' => [], 'last' => 0];
        if (!$ids) return $usage;
        $marks = implode(',', array_fill(0, count($ids), '?'));
        $query = $this->db->prepare("SELECT device_id, status, due_at, created_at FROM whatsapp_sends WHERE device_id IN ($marks) AND status IN ('waiting', 'sending', 'sent')");
        $query->execute($ids);
        foreach ($query->fetchAll() as $row) {
            $id = (string) $row['device_id'];
            $at = strtotime((string) ($row['status'] === 'sent' ? $row['created_at'] : ($row['due_at'] ?: $row['created_at'])));
            if (!$at) continue;
            $day = $this->day($at);
            $usage[$id]['days'][$day] = ($usage[$id]['days'][$day] ?? 0) + 1;
            if ($at > $usage[$id]['last']) $usage[$id]['last'] = $at;
        }
        return $usage;
    }

    /** @return array{0: int, 1: int} last sent time, sent today */
    private function sentStats(string $deviceId, int $now): array
    {
        $query = $this->db->prepare("SELECT created_at FROM whatsapp_sends WHERE device_id = ? AND status = 'sent'");
        $query->execute([$deviceId]);
        $last = 0;
        $today = 0;
        $day = $this->day($now);
        foreach ($query->fetchAll(PDO::FETCH_COLUMN) as $value) {
            $at = strtotime((string) $value);
            if (!$at) continue;
            if ($at > $last) $last = $at;
            if ($this->day($at) === $day) $today++;
        }
        return [$last, $today];
    }

    private function latestRows(int $sourceId): array
    {
        $query = $this->db->prepare('SELECT id, record_id, phone, name, status, error, account_name, device_id, due_at, created_at FROM whatsapp_sends WHERE source_id = ? ORDER BY id ASC');
        $query->execute([$sourceId]);
        $latest = [];
        foreach ($query->fetchAll() as $row) {
            $latest[(string) $row['record_id']] = $row;
        }
        return array_values($latest);
    }

    private function waitingTotals(): array
    {
        $rows = $this->db->query("SELECT due_at FROM whatsapp_sends WHERE status IN ('waiting', 'sending')")->fetchAll(PDO::FETCH_COLUMN);
        $next = null;
        foreach ($rows as $value) {
            $at = strtotime((string) $value);
            if ($at && ($next === null || $at < $next)) $next = $at;
        }
        return ['waiting' => count($rows), 'next_at' => $next !== null ? gmdate('c', $next) : null];
    }

    private function summary(array $built): array
    {
        $phones = [];
        foreach ($built['phones'] as $phone) {
            $phones[$phone['id']] = ['id' => $phone['id'], 'name' => $phone['name'], 'count' => 0, 'first_at' => null, 'last_at' => null];
        }
        $days = [];
        foreach ($built['slots'] as $slot) {
            $id = $slot['phone']['id'];
            $at = gmdate('c', $slot['at']);
            $phones[$id]['count']++;
            $phones[$id]['first_at'] ??= $at;
            $phones[$id]['last_at'] = $at;
            $day = $this->day($slot['at']);
            $days[$day] = ($days[$day] ?? 0) + 1;
        }
        ksort($days);
        $dayList = [];
        foreach ($days as $date => $count) $dayList[] = ['date' => $date, 'count' => $count];
        return [
            'total' => count($built['slots']),
            'start_at' => gmdate('c', $built['start']),
            'gap' => $built['gap'],
            'daily' => self::DAILY,
            'skipped' => $built['skipped'],
            'phones' => array_values($phones),
            'days' => $dayList,
        ];
    }

    private function state(string $status): string
    {
        return match ($status) {
            'sent' => 'sent',
            'failed', 'blocked' => 'failed',
            default => 'waiting',
        };
    }

    private function day(int $at): string
    {
        return (new DateTimeImmutable('@' . $at))->setTimezone($this->zone)->format('Y-m-d');
    }

    private function dayStart(string $day, int $offset, string $time): int
    {
        return (new DateTimeImmutable($day . ' ' . $time, $this->zone))->modify('+' . $offset . ' day')->getTimestamp();
    }
}
