-- ====================================================================
-- Communication Desk & Email Automation Platform
-- Production Database Schema (Structure Only - No Data)
-- Ready for managed MySQL-compatible deployment; FastAPI runtime is provider-neutral
-- Character Set: utf8mb4 / Collation: utf8mb4_unicode_ci / Engine: InnoDB
-- ====================================================================

SET FOREIGN_KEY_CHECKS = 0;
SET SQL_MODE = "NO_AUTO_VALUE_ON_ZERO";
START TRANSACTION;
SET time_zone = "+00:00";

-- --------------------------------------------------------------------
-- 1. Table structure for `emails` (Outgoing email queue & logs)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `emails`;
CREATE TABLE `emails` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `mail_account_id` INT DEFAULT NULL,
  `recipient` VARCHAR(254) NOT NULL,
  `cc` TEXT NOT NULL,
  `bcc` TEXT NOT NULL,
  `subject` VARCHAR(255) NOT NULL,
  `body` TEXT NOT NULL,
  `attachments` TEXT DEFAULT NULL,
  `status` VARCHAR(32) NOT NULL DEFAULT 'pending',
  `idempotency_key` VARCHAR(64) NOT NULL,
  `attempts` INT NOT NULL DEFAULT 0,
  `error` TEXT DEFAULT NULL,
  `message_id` VARCHAR(255) DEFAULT NULL,
  `inbox_email_id` INT DEFAULT NULL,
  `thread_id` VARCHAR(128) DEFAULT NULL,
  `sent_at` VARCHAR(32) DEFAULT NULL,
  `created_at` VARCHAR(32) NOT NULL,
  `updated_at` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_idempotency_key` (`idempotency_key`),
  KEY `idx_emails_status` (`status`),
  KEY `idx_emails_recipient` (`recipient`),
  KEY `idx_emails_thread_id` (`thread_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 2. Table structure for `inbox_emails` (optional IMAP incoming messages)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `inbox_emails`;
CREATE TABLE `inbox_emails` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `mail_account_id` INT DEFAULT NULL,
  `uid` INT NOT NULL,
  `message_id` VARCHAR(255) DEFAULT NULL,
  `from_name` VARCHAR(255) DEFAULT NULL,
  `from_email` VARCHAR(254) NOT NULL,
  `to_email` VARCHAR(254) NOT NULL,
  `subject` VARCHAR(255) NOT NULL,
  `body` LONGTEXT NOT NULL,
  `html_body` LONGTEXT DEFAULT NULL,
  `snippet` VARCHAR(255) DEFAULT NULL,
  `is_read` INT NOT NULL DEFAULT 0,
  `received_at` VARCHAR(64) NOT NULL,
  `created_at` VARCHAR(32) NOT NULL,
  `archived_at` VARCHAR(32) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_inbox_uid` (`uid`),
  KEY `idx_inbox_from_email` (`from_email`),
  KEY `idx_inbox_is_read` (`is_read`),
  KEY `idx_inbox_received_at` (`received_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 3. Table structure for `activity` (Audit events and delivery timeline)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `activity`;
CREATE TABLE `activity` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `email_id` INT DEFAULT NULL,
  `action` VARCHAR(80) NOT NULL,
  `detail` TEXT NOT NULL,
  `created_at` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_activity_email_id` (`email_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 4. Table structure for `google_connection` (OAuth tokens for Google Sheets)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `google_connection`;
CREATE TABLE `google_connection` (
  `id` INT NOT NULL,
  `token_data` TEXT NOT NULL,
  `updated_at` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 5. Table structure for `sheet_sources` (Connected Google Sheets & mappings)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `sheet_sources`;
CREATE TABLE `sheet_sources` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `spreadsheet_id` VARCHAR(160) NOT NULL,
  `sheet_id` INT NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `header_row` INT NOT NULL,
  `mapping` TEXT NOT NULL,
  `created_at` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uniq_sheet_source` (`spreadsheet_id`, `sheet_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 6. Table structure for `uploaded_sheets` (Direct Excel/CSV imports)
-- --------------------------------------------------------------------
DROP TABLE IF EXISTS `uploaded_sheets`;
CREATE TABLE `uploaded_sheets` (
  `id` VARCHAR(80) NOT NULL,
  `title` VARCHAR(255) NOT NULL,
  `workbook` MEDIUMTEXT NOT NULL,
  `created_at` VARCHAR(32) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- --------------------------------------------------------------------
-- 7. FastAPI migration support tables
-- --------------------------------------------------------------------
CREATE TABLE `mail_accounts` (
  `id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL,
  `email` VARCHAR(254) NOT NULL, `smtp_host` VARCHAR(255) NOT NULL,
  `smtp_port` INT NOT NULL DEFAULT 465, `smtp_user` VARCHAR(255) NOT NULL,
  `smtp_pass` TEXT NOT NULL, `imap_host` VARCHAR(255) NOT NULL,
  `imap_port` INT NOT NULL DEFAULT 993, `imap_user` VARCHAR(255) NOT NULL,
  `imap_pass` TEXT NOT NULL, `is_default` INT NOT NULL DEFAULT 1,
  `status` VARCHAR(32) NOT NULL DEFAULT 'active', `created_at` VARCHAR(64) NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uniq_mail_account_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `users` (
  `id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(255) NOT NULL,
  `email` VARCHAR(254) NOT NULL, `password_hash` VARCHAR(255) NOT NULL,
  `role` VARCHAR(32) NOT NULL DEFAULT 'member', `assigned_account_id` INT DEFAULT NULL,
  `created_at` VARCHAR(64) NOT NULL, `updated_at` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`id`), UNIQUE KEY `uniq_user_email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_account` (
  `id` INT NOT NULL, `name` VARCHAR(160) NOT NULL DEFAULT 'WhatsApp',
  `base_url` VARCHAR(255) NOT NULL DEFAULT '', `access_token` TEXT NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL, PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_settings` (
  `id` INT NOT NULL, `api_url` TEXT NOT NULL, `api_token` TEXT NOT NULL,
  `phone_number_id` VARCHAR(120) NOT NULL DEFAULT '', `api_version` VARCHAR(20) NOT NULL DEFAULT 'v23.0', `verify_token` TEXT NOT NULL,
  `updated_at` VARCHAR(64) NOT NULL, PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_devices` (
  `id` INT NOT NULL AUTO_INCREMENT, `name` VARCHAR(120) NOT NULL,
  `phone` VARCHAR(40) NOT NULL DEFAULT '', `status` VARCHAR(32) NOT NULL DEFAULT 'disconnected',
  `qr_code` LONGTEXT DEFAULT NULL, `updated_at` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_sends` (
  `id` INT NOT NULL AUTO_INCREMENT, `recipient` VARCHAR(40) NOT NULL,
  `message` TEXT NOT NULL, `status` VARCHAR(32) NOT NULL DEFAULT 'queued',
  `device_id` VARCHAR(120) DEFAULT NULL, `created_at` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`id`), KEY `idx_wa_send_status` (`status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_chats` (
  `id` INT NOT NULL AUTO_INCREMENT, `remote_jid` VARCHAR(120) NOT NULL,
  `name` VARCHAR(255) NOT NULL DEFAULT '', `phone` VARCHAR(40) NOT NULL DEFAULT '',
  `last_message` TEXT NOT NULL, `unread_count` INT NOT NULL DEFAULT 0,
  `last_at` VARCHAR(64) NOT NULL, PRIMARY KEY (`id`), UNIQUE KEY `uniq_wa_chat_jid` (`remote_jid`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `whatsapp_messages` (
  `id` INT NOT NULL AUTO_INCREMENT, `chat_id` INT NOT NULL,
  `direction` VARCHAR(12) NOT NULL, `body` TEXT NOT NULL,
  `status` VARCHAR(20) NOT NULL DEFAULT 'sent', `created_at` VARCHAR(64) NOT NULL,
  PRIMARY KEY (`id`), KEY `idx_wa_message_chat` (`chat_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

SET FOREIGN_KEY_CHECKS = 1;
COMMIT;

