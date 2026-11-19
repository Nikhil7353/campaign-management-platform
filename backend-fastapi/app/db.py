from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings


def _engine() -> Engine:
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        Path("storage").mkdir(exist_ok=True)
        return create_engine(settings.database_url, connect_args={"check_same_thread": False})
    return create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=3600)


engine = _engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def init_db() -> None:
    """Create the portable local schema used by the migration.

    Production MySQL deployments can still run the project's database.sql; these
    IF NOT EXISTS statements make local FastAPI development work out of the box.
    """
    if not get_settings().database_url.startswith("sqlite"):
        return
    statements = [
        """CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recipient VARCHAR(254) NOT NULL,
            cc TEXT DEFAULT '', bcc TEXT DEFAULT '', subject VARCHAR(255) NOT NULL,
            body TEXT NOT NULL, attachments TEXT, status VARCHAR(32) NOT NULL DEFAULT 'pending',
            idempotency_key VARCHAR(64) UNIQUE NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
            error TEXT, message_id VARCHAR(255), inbox_email_id INTEGER, thread_id VARCHAR(128),
            sent_at VARCHAR(64), created_at VARCHAR(64) NOT NULL, updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS inbox_emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT, uid INTEGER NOT NULL DEFAULT 0,
            message_id VARCHAR(255), from_name VARCHAR(255), from_email VARCHAR(254) NOT NULL,
            to_email VARCHAR(254) NOT NULL DEFAULT '', subject VARCHAR(255) NOT NULL,
            body TEXT NOT NULL DEFAULT '', html_body TEXT, snippet VARCHAR(255),
            is_read INTEGER NOT NULL DEFAULT 0, received_at VARCHAR(64) NOT NULL,
            created_at VARCHAR(64) NOT NULL, archived_at VARCHAR(64))""",
        """CREATE TABLE IF NOT EXISTS activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT, email_id INTEGER, action VARCHAR(80) NOT NULL,
            detail TEXT NOT NULL DEFAULT '', created_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS sheet_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT, spreadsheet_id VARCHAR(160) NOT NULL,
            sheet_id INTEGER NOT NULL DEFAULT 0, title VARCHAR(255) NOT NULL,
            header_row INTEGER NOT NULL DEFAULT 1, mapping TEXT NOT NULL DEFAULT '{}',
            rows_json TEXT NOT NULL DEFAULT '[]', created_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS uploaded_sheets (
            id VARCHAR(80) PRIMARY KEY, title VARCHAR(255) NOT NULL,
            workbook TEXT NOT NULL, created_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS mail_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(255) NOT NULL,
            email VARCHAR(254) NOT NULL UNIQUE, smtp_host VARCHAR(255) NOT NULL,
            smtp_port INTEGER NOT NULL DEFAULT 465, smtp_user VARCHAR(255) NOT NULL,
            smtp_pass TEXT NOT NULL DEFAULT '', imap_host VARCHAR(255) NOT NULL,
            imap_port INTEGER NOT NULL DEFAULT 993, imap_user VARCHAR(255) NOT NULL,
            imap_pass TEXT NOT NULL DEFAULT '', is_default INTEGER NOT NULL DEFAULT 1,
            status VARCHAR(32) NOT NULL DEFAULT 'active', created_at VARCHAR(64) NOT NULL,
            updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS google_connection (
            id INTEGER PRIMARY KEY, token_data TEXT NOT NULL, updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_account (
            id INTEGER PRIMARY KEY, name VARCHAR(160) NOT NULL DEFAULT 'WhatsApp',
            base_url VARCHAR(255) NOT NULL DEFAULT '', access_token TEXT NOT NULL DEFAULT '',
            updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_settings (
            id INTEGER PRIMARY KEY, api_url TEXT NOT NULL DEFAULT '', api_token TEXT NOT NULL DEFAULT '',
            phone_number_id TEXT NOT NULL DEFAULT '', api_version VARCHAR(20) NOT NULL DEFAULT 'v23.0', verify_token TEXT NOT NULL DEFAULT '',
            updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_devices (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name VARCHAR(120) NOT NULL,
            phone VARCHAR(40) NOT NULL DEFAULT '', status VARCHAR(32) NOT NULL DEFAULT 'disconnected',
            qr_code TEXT, updated_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_sends (
            id INTEGER PRIMARY KEY AUTOINCREMENT, recipient VARCHAR(40) NOT NULL,
            message TEXT NOT NULL, status VARCHAR(32) NOT NULL DEFAULT 'queued',
            device_id INTEGER, created_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_chats (
            id INTEGER PRIMARY KEY AUTOINCREMENT, remote_jid VARCHAR(120) NOT NULL UNIQUE,
            name VARCHAR(255) NOT NULL DEFAULT '', phone VARCHAR(40) NOT NULL DEFAULT '',
            last_message TEXT NOT NULL DEFAULT '', unread_count INTEGER NOT NULL DEFAULT 0,
            last_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS whatsapp_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER NOT NULL, direction VARCHAR(12) NOT NULL,
            body TEXT NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'sent', created_at VARCHAR(64) NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS inbox_sync_settings (
            id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            interval_seconds INTEGER NOT NULL DEFAULT 300,
            last_sync_at VARCHAR(64),
            last_status VARCHAR(32) NOT NULL DEFAULT 'idle',
            last_error TEXT NOT NULL DEFAULT '',
            last_new_count INTEGER NOT NULL DEFAULT 0,
            last_total_inbox INTEGER NOT NULL DEFAULT 0,
            last_trigger VARCHAR(32) NOT NULL DEFAULT '',
            updated_at VARCHAR(64) NOT NULL)""",
    ]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))
        for statement in [
            "ALTER TABLE whatsapp_settings ADD COLUMN phone_number_id TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE whatsapp_settings ADD COLUMN api_version VARCHAR(20) NOT NULL DEFAULT 'v23.0'",
            "ALTER TABLE whatsapp_settings ADD COLUMN verify_token TEXT NOT NULL DEFAULT ''",
        ]:
            try:
                conn.execute(text(statement))
            except Exception:
                pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

