from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr, parsedate_to_datetime
import imaplib
import re
import smtplib
import ssl
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rows(db: Session, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    try:
        return [dict(row) for row in db.execute(text(query), params or {}).mappings().all()]
    except Exception:
        return []


def dashboard(db: Session) -> dict[str, Any]:
    sent = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status IN ('sent','delivered')")[0].get("value", 0)
    delivered = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status = 'delivered'")[0].get("value", 0)
    failed = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status IN ('failed','bounced')")[0].get("value", 0)
    pending = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status IN ('pending','processing')")[0].get("value", 0)
    unread = rows(db, "SELECT COUNT(*) AS value FROM inbox_emails WHERE is_read = 0 AND COALESCE(archived_at, '') = ''")[0].get("value", 0)
    contacts = rows(db, """
        SELECT from_email AS email, COALESCE(from_name, from_email) AS name,
               COUNT(*) AS replies, MAX(received_at) AS last_at
        FROM inbox_emails GROUP BY from_email, from_name ORDER BY last_at DESC LIMIT 6
    """)
    recent_activity = rows(db, """
        SELECT id, email_id, action, detail, created_at
        FROM activity ORDER BY created_at DESC LIMIT 6
    """)
    return {
        "emails_sent": int(sent or 0), "emails_delivered": int(delivered or 0),
        "delivery_confirmation_available": bool(delivered),
        "emails_failed": int(failed or 0),
        "emails_pending": int(pending or 0), "inbox_unread": int(unread or 0),
        "hot_contacts": contacts, "recent_activity": recent_activity, "health": {
            "smtp": {"status": "configured"}, "imap": {"status": "configured"},
            "sheets": {"status": "ready"}, "database": {"status": "connected"},
        },
    }


def inbox(db: Session, unread: str = "", search: str = "", limit: int = 25, offset: int = 0) -> dict[str, Any]:
    clauses = ["COALESCE(archived_at, '') = ''"]
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if unread == "unread": clauses.append("is_read = 0")
    if unread == "read": clauses.append("is_read = 1")
    if search:
        clauses.append("(from_name LIKE :search OR from_email LIKE :search OR subject LIKE :search OR body LIKE :search)")
        params["search"] = f"%{search}%"
    where = " AND ".join(clauses)
    items = rows(db, f"SELECT * FROM inbox_emails WHERE {where} ORDER BY received_at DESC LIMIT :limit OFFSET :offset", params)
    total = rows(db, f"SELECT COUNT(*) AS value FROM inbox_emails WHERE {where}", params)[0].get("value", 0)
    unread_total = rows(db, "SELECT COUNT(*) AS value FROM inbox_emails WHERE is_read = 0 AND COALESCE(archived_at, '') = ''")[0].get("value", 0)
    return {"emails": items, "total": int(total or 0), "unread_count": int(unread_total or 0)}


def sent(db: Session, status: str = "", search: str = "", limit: int = 25, offset: int = 0) -> dict[str, Any]:
    clauses: list[str] = []
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    normalized_status = (status or "").strip().lower()
    if normalized_status == "sent":
        clauses.append("status IN ('sent', 'delivered')")
    elif normalized_status == "pending":
        clauses.append("status IN ('pending', 'processing')")
    elif normalized_status == "failed":
        clauses.append("status IN ('failed', 'bounced')")
    elif normalized_status == "dry_run":
        clauses.append("status = 'dry_run'")
    if search:
        clauses.append("(recipient LIKE :search OR cc LIKE :search OR bcc LIKE :search OR subject LIKE :search OR body LIKE :search)")
        params["search"] = f"%{search}%"
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    items = rows(
        db,
        f"SELECT * FROM emails {where} ORDER BY COALESCE(sent_at, created_at) DESC, id DESC LIMIT :limit OFFSET :offset",
        params,
    )
    total = rows(db, f"SELECT COUNT(*) AS value FROM emails {where}", params)[0].get("value", 0)
    status_rows = rows(db, "SELECT status, COUNT(*) AS value FROM emails GROUP BY status")
    raw_counts = {str(r.get("status") or ""): int(r.get("value") or 0) for r in status_rows}
    counts = {
        "all": sum(raw_counts.values()),
        "sent": raw_counts.get("sent", 0) + raw_counts.get("delivered", 0),
        "pending": raw_counts.get("pending", 0) + raw_counts.get("processing", 0),
        "failed": raw_counts.get("failed", 0) + raw_counts.get("bounced", 0),
        "dry_run": raw_counts.get("dry_run", 0),
    }
    return {"emails": items, "total": int(total or 0), "counts": counts}


def whatsapp_overview(db: Session, days: int = 7) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    sent_count = rows(db, "SELECT COUNT(*) AS value FROM whatsapp_sends WHERE created_at >= :since", {"since": since})[0].get("value", 0)
    failed_count = rows(db, "SELECT COUNT(*) AS value FROM whatsapp_sends WHERE created_at >= :since AND status = 'failed'", {"since": since})[0].get("value", 0)
    return {"range": days, "sent": int(sent_count or 0), "failed": int(failed_count or 0), "pending": 0, "days": []}


def _mail_settings() -> dict[str, Any]:
    from .config import get_settings
    s = get_settings()
    return {"host": s.smtp_host, "port": s.smtp_port, "user": s.smtp_username,
            "password": s.smtp_password, "from": s.mail_from_address or s.smtp_username,
            "from_name": s.mail_from_name, "dry_run": s.mail_dry_run}


def send_email(db: Session, email_id: int) -> dict[str, Any]:
    item = rows(db, "SELECT * FROM emails WHERE id = :id", {"id": email_id})
    if not item:
        raise ValueError("Email not found")
    email = item[0]
    settings = _mail_settings()
    if settings["dry_run"]:
        status, message_id = "dry_run", None
    else:
        if not settings["user"] or not settings["password"] or not settings["from"]:
            raise ValueError("SMTP configuration is incomplete")
        message_id = make_msgid()
        from email.message import EmailMessage
        message = EmailMessage()
        message["From"] = f'{settings["from_name"]} <{settings["from"]}>'
        message["To"] = email["recipient"]
        if email.get("cc"): message["Cc"] = email["cc"]
        if email.get("bcc"): message["Bcc"] = email["bcc"]
        message["Subject"] = email["subject"]
        message["Message-ID"] = message_id
        message["Date"] = formatdate(localtime=False)
        message.set_content(email["body"])
        if settings["port"] == 465:
            with smtplib.SMTP_SSL(settings["host"], settings["port"], timeout=30, context=ssl.create_default_context()) as smtp:
                smtp.login(settings["user"], settings["password"]); smtp.send_message(message)
        else:
            with smtplib.SMTP(settings["host"], settings["port"], timeout=30) as smtp:
                smtp.starttls(context=ssl.create_default_context()); smtp.login(settings["user"], settings["password"]); smtp.send_message(message)
        status = "sent"
    timestamp = now_iso()
    db.execute(text("UPDATE emails SET status=:status, error=NULL, message_id=:message_id, sent_at=:sent_at, updated_at=:updated_at, attempts=attempts+1 WHERE id=:id"), {"status": status, "message_id": message_id, "sent_at": timestamp, "updated_at": timestamp, "id": email_id})
    db.execute(text("INSERT INTO activity (email_id, action, detail, created_at) VALUES (:id, 'email_sent', :detail, :created)"), {"id": email_id, "detail": status, "created": timestamp})
    db.commit()
    return {"id": email_id, "status": status, "message_id": message_id}


def process_email_queue(db: Session, limit: int = 25) -> dict[str, Any]:
    queued = rows(db, "SELECT id FROM emails WHERE status = 'pending' ORDER BY id LIMIT :limit", {"limit": limit})
    results = []
    for item in queued:
        try: results.append(send_email(db, int(item["id"])))
        except Exception as exc:
            db.execute(text("UPDATE emails SET status='failed', error=:error, attempts=attempts+1, updated_at=:updated WHERE id=:id"), {"error": str(exc), "updated": now_iso(), "id": item["id"]}); db.commit(); results.append({"id": item["id"], "status": "failed", "error": str(exc)})
    return {"processed": len(results), "results": results}


def _decode_header(value: str | None) -> str:
    if not value: return ""
    try: return str(make_header(decode_header(value)))
    except Exception: return value


MIN_SYNC_INTERVAL_SECONDS = 30
MAX_SYNC_INTERVAL_SECONDS = 86400
_SYNC_LOCK = threading.Lock()


def imap_configured() -> bool:
    from .config import get_settings
    s = get_settings()
    return bool(s.imap_host and s.imap_username and s.imap_password)


def _clamp_sync_interval(value: int) -> int:
    return max(MIN_SYNC_INTERVAL_SECONDS, min(MAX_SYNC_INTERVAL_SECONDS, int(value)))


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _read_sync_row(db: Session) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings()
    saved = rows(db, "SELECT * FROM inbox_sync_settings WHERE id = 1")
    if saved:
        row = saved[0]
        return {
            "enabled": bool(row.get("enabled", 0)),
            "interval_seconds": _clamp_sync_interval(int(row.get("interval_seconds") or s.inbox_auto_sync_interval_seconds)),
            "last_sync_at": row.get("last_sync_at") or None,
            "last_status": str(row.get("last_status") or "idle"),
            "last_error": str(row.get("last_error") or ""),
            "last_new_count": int(row.get("last_new_count") or 0),
            "last_total_inbox": int(row.get("last_total_inbox") or 0),
            "last_trigger": str(row.get("last_trigger") or ""),
            "updated_at": row.get("updated_at") or None,
        }
    return {
        "enabled": bool(s.inbox_auto_sync_enabled),
        "interval_seconds": _clamp_sync_interval(s.inbox_auto_sync_interval_seconds),
        "last_sync_at": None,
        "last_status": "idle",
        "last_error": "",
        "last_new_count": 0,
        "last_total_inbox": 0,
        "last_trigger": "",
        "updated_at": None,
    }


def get_sync_settings(db: Session) -> dict[str, Any]:
    state = _read_sync_row(db)
    is_running = _SYNC_LOCK.locked()
    next_sync_at: str | None = None
    if state["enabled"] and not is_running:
        now_dt = datetime.now(timezone.utc)
        last_dt = _parse_iso_datetime(state["last_sync_at"])
        if last_dt is None:
            next_sync_at = now_dt.isoformat()
        else:
            target = last_dt + timedelta(seconds=state["interval_seconds"])
            next_sync_at = max(now_dt, target).isoformat()
    return {
        **state,
        "imap_configured": imap_configured(),
        "is_running": is_running,
        "next_sync_at": next_sync_at,
        "min_interval_seconds": MIN_SYNC_INTERVAL_SECONDS,
        "max_interval_seconds": MAX_SYNC_INTERVAL_SECONDS,
    }


def _upsert_sync_row(db: Session, values: dict[str, Any]) -> None:
    if rows(db, "SELECT id FROM inbox_sync_settings WHERE id = 1"):
        db.execute(
            text("""UPDATE inbox_sync_settings
                SET enabled=:enabled, interval_seconds=:interval_seconds,
                    last_sync_at=:last_sync_at, last_status=:last_status,
                    last_error=:last_error, last_new_count=:last_new_count,
                    last_total_inbox=:last_total_inbox, last_trigger=:last_trigger,
                    updated_at=:updated_at
                WHERE id = 1"""),
            values,
        )
    else:
        db.execute(
            text("""INSERT INTO inbox_sync_settings
                (id, enabled, interval_seconds, last_sync_at, last_status, last_error,
                 last_new_count, last_total_inbox, last_trigger, updated_at)
                VALUES (1, :enabled, :interval_seconds, :last_sync_at, :last_status, :last_error,
                        :last_new_count, :last_total_inbox, :last_trigger, :updated_at)"""),
            values,
        )
    db.commit()


def save_sync_settings(
    db: Session,
    enabled: bool | None = None,
    interval_seconds: int | None = None,
) -> dict[str, Any]:
    current = _read_sync_row(db)
    next_enabled = current["enabled"] if enabled is None else bool(enabled)
    if interval_seconds is None:
        next_interval = current["interval_seconds"]
    else:
        if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int):
            raise ValueError("interval_seconds must be an integer")
        if interval_seconds < MIN_SYNC_INTERVAL_SECONDS or interval_seconds > MAX_SYNC_INTERVAL_SECONDS:
            raise ValueError(
                f"interval_seconds must be between {MIN_SYNC_INTERVAL_SECONDS} and {MAX_SYNC_INTERVAL_SECONDS}"
            )
        next_interval = interval_seconds
    _upsert_sync_row(
        db,
        {
            "enabled": 1 if next_enabled else 0,
            "interval_seconds": next_interval,
            "last_sync_at": current["last_sync_at"],
            "last_status": current["last_status"],
            "last_error": current["last_error"],
            "last_new_count": current["last_new_count"],
            "last_total_inbox": current["last_total_inbox"],
            "last_trigger": current["last_trigger"],
            "updated_at": now_iso(),
        },
    )
    return get_sync_settings(db)


def _record_sync_result(
    db: Session,
    *,
    status: str,
    trigger: str,
    error: str = "",
    new_count: int = 0,
    total_inbox: int = 0,
    synced_at: str | None = None,
) -> None:
    current = _read_sync_row(db)
    timestamp = synced_at or now_iso()
    _upsert_sync_row(
        db,
        {
            "enabled": 1 if current["enabled"] else 0,
            "interval_seconds": current["interval_seconds"],
            "last_sync_at": timestamp,
            "last_status": status,
            "last_error": error,
            "last_new_count": int(new_count),
            "last_total_inbox": int(total_inbox),
            "last_trigger": trigger,
            "updated_at": timestamp,
        },
    )
    detail = f"{trigger} sync {status}: {new_count} new" if not error else f"{trigger} sync {status}: {error[:180]}"
    try:
        db.execute(
            text("INSERT INTO activity (email_id, action, detail, created_at) VALUES (NULL, 'inbox_sync', :detail, :created)"),
            {"detail": detail, "created": timestamp},
        )
        db.commit()
    except Exception:
        db.rollback()


def _normalize_received_at(value: str | None) -> str:
    if not value:
        return now_iso()
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            parsed = parsed.astimezone(timezone.utc)
        return parsed.isoformat()
    except (TypeError, ValueError, IndexError):
        iso = _parse_iso_datetime(value)
        return iso.astimezone(timezone.utc).isoformat() if iso else now_iso()


def sync_inbox(db: Session, limit: int = 30) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings()
    if not s.imap_host or not s.imap_username or not s.imap_password:
        raise ValueError("IMAP credentials are not configured")
    client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=30)
    try:
        client.login(s.imap_username, s.imap_password)
        client.select("INBOX")
        _, data = client.uid("search", None, "ALL")
        raw_uids = data[0].split() if data and data[0] else []
        uids = [int(x) for x in raw_uids]
        existing = rows(db, "SELECT uid FROM inbox_emails")
        known = {int(x["uid"]) for x in existing}
        added = 0
        for uid in reversed(uids):
            if uid in known or added >= limit:
                continue
            _, fetched = client.uid("fetch", str(uid), "(RFC822)")
            raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
            if not raw:
                continue
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            sender_name, sender_email = parseaddr(str(msg.get("From", "")))
            body = msg.get_body(preferencelist=("plain", "html"))
            content = body.get_content() if body else ""
            is_html = bool(body and body.get_content_type() == "text/html")
            plain_content = re.sub(r"<[^>]+>", " ", content) if is_html else content
            received = _normalize_received_at(msg.get("Date"))
            db.execute(
                text("""INSERT INTO inbox_emails (uid,message_id,from_name,from_email,to_email,subject,body,html_body,snippet,is_read,received_at,created_at)
                    VALUES (:uid,:message_id,:from_name,:from_email,:to_email,:subject,:body,:html_body,:snippet,0,:received,:created)"""),
                {
                    "uid": uid,
                    "message_id": str(msg.get("Message-ID", "")),
                    "from_name": _decode_header(sender_name),
                    "from_email": sender_email,
                    "to_email": s.imap_username,
                    "subject": _decode_header(str(msg.get("Subject", "(No subject)"))),
                    "body": plain_content,
                    "html_body": content if is_html else "",
                    "snippet": plain_content[:255],
                    "received": received,
                    "created": now_iso(),
                },
            )
            added += 1
        db.commit()
    finally:
        try:
            client.logout()
        except Exception:
            pass
    return {"new_count": added, "total_inbox": len(uids), "synced_at": now_iso()}


def run_inbox_sync(db: Session, limit: int = 30, trigger: str = "manual") -> dict[str, Any]:
    if not _SYNC_LOCK.acquire(blocking=False):
        raise RuntimeError("Inbox sync is already in progress")
    try:
        if not imap_configured():
            message = "IMAP credentials are not configured"
            timestamp = now_iso()
            if trigger == "auto":
                _record_sync_result(db, status="skipped", trigger=trigger, error=message, synced_at=timestamp)
                result: dict[str, Any] = {
                    "new_count": 0,
                    "total_inbox": 0,
                    "synced_at": timestamp,
                    "status": "skipped",
                    "error": message,
                    "trigger": trigger,
                }
            else:
                _record_sync_result(db, status="error", trigger=trigger, error=message, synced_at=timestamp)
                raise ValueError(message)
        else:
            try:
                synced = sync_inbox(db, limit=limit)
            except Exception as exc:
                db.rollback()
                _record_sync_result(db, status="error", trigger=trigger, error=str(exc), synced_at=now_iso())
                raise
            _record_sync_result(
                db,
                status="success",
                trigger=trigger,
                new_count=int(synced["new_count"]),
                total_inbox=int(synced["total_inbox"]),
                synced_at=str(synced["synced_at"]),
            )
            result = {**synced, "status": "success", "error": "", "trigger": trigger}
    finally:
        _SYNC_LOCK.release()
    return {**result, "sync_settings": get_sync_settings(db)}


def whatsapp_request(method: str, path: str, body: dict[str, Any] | None = None, base_url: str | None = None, access_token: str | None = None) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings(); base = (base_url or s.whatsapp_base_url).rstrip("/"); token = access_token or s.whatsapp_access_token
    if not base or not token: raise ValueError("WhatsApp base URL and access token are not configured")
    raw = json.dumps(body).encode() if body is not None else None
    request = Request(base + path, data=raw, method=method, headers={"Accept": "application/json", "Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=30) as response: return json.loads(response.read().decode() or "{}")
    except (HTTPError, URLError) as exc: raise ValueError(f"WhatsApp gateway request failed: {exc}") from exc


def send_whatsapp_cloud(recipient: str, message: str, phone_number_id: str | None = None, access_token: str | None = None, api_version: str | None = None) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings()
    token = access_token or s.whatsapp_access_token; phone_id = phone_number_id or s.whatsapp_phone_number_id; version = api_version or s.whatsapp_api_version
    if not token or not phone_id:
        raise ValueError("Meta WhatsApp phone number ID and access token are not configured")
    return whatsapp_request("POST", f"/{version}/{phone_id}/messages", {
        "messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"preview_url": False, "body": message}
    }, "https://graph.facebook.com", token)


def google_authorization_url(state: str) -> str:
    from .config import get_settings
    s = get_settings()
    if not s.google_client_id or not s.google_client_secret: raise ValueError("Google OAuth credentials are not configured")
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({"client_id": s.google_client_id, "redirect_uri": s.google_redirect_uri, "response_type": "code", "access_type": "offline", "prompt": "consent", "scope": "https://www.googleapis.com/auth/spreadsheets", "state": state})


def google_exchange_code(db: Session, code: str) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings(); body = urlencode({"code": code, "client_id": s.google_client_id, "client_secret": s.google_client_secret, "redirect_uri": s.google_redirect_uri, "grant_type": "authorization_code"}).encode()
    request = Request("https://oauth2.googleapis.com/token", data=body, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=30) as response: token = json.loads(response.read().decode())
    except (HTTPError, URLError) as exc: raise ValueError(f"Google token exchange failed: {exc}") from exc
    values = {"token": json.dumps(token), "updated": now_iso()}
    if rows(db, "SELECT id FROM google_connection WHERE id = 1"):
        db.execute(text("UPDATE google_connection SET token_data=:token, updated_at=:updated WHERE id=1"), values)
    else:
        db.execute(text("INSERT INTO google_connection (id, token_data, updated_at) VALUES (1, :token, :updated)"), values)
    db.commit(); return token

