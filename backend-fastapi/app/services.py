from datetime import datetime, timedelta, timezone
from email import policy
from email.header import decode_header, make_header
from email.parser import BytesParser
from email.utils import formatdate, make_msgid, parseaddr
import imaplib
import smtplib
import ssl
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
    failed = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status IN ('failed','bounced')")[0].get("value", 0)
    pending = rows(db, "SELECT COUNT(*) AS value FROM emails WHERE status IN ('pending','processing')")[0].get("value", 0)
    unread = rows(db, "SELECT COUNT(*) AS value FROM inbox_emails WHERE is_read = 0 AND COALESCE(archived_at, '') = ''")[0].get("value", 0)
    contacts = rows(db, """
        SELECT from_email AS email, COALESCE(from_name, from_email) AS name,
               COUNT(*) AS replies, MAX(received_at) AS last_at
        FROM inbox_emails GROUP BY from_email, from_name ORDER BY last_at DESC LIMIT 6
    """)
    return {
        "emails_sent": int(sent or 0), "emails_failed": int(failed or 0),
        "emails_pending": int(pending or 0), "inbox_unread": int(unread or 0),
        "hot_contacts": contacts, "health": {
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
        clauses.append("(from_email LIKE :search OR subject LIKE :search OR body LIKE :search)")
        params["search"] = f"%{search}%"
    where = " AND ".join(clauses)
    items = rows(db, f"SELECT * FROM inbox_emails WHERE {where} ORDER BY received_at DESC LIMIT :limit OFFSET :offset", params)
    total = rows(db, f"SELECT COUNT(*) AS value FROM inbox_emails WHERE {where}", params)[0].get("value", 0)
    return {"emails": items, "total": int(total or 0), "unread_count": len(rows(db, "SELECT id FROM inbox_emails WHERE is_read = 0 AND COALESCE(archived_at, '') = ''"))}


def sent(db: Session, limit: int = 25, offset: int = 0) -> dict[str, Any]:
    items = rows(db, "SELECT * FROM emails ORDER BY created_at DESC LIMIT :limit OFFSET :offset", {"limit": limit, "offset": offset})
    total = rows(db, "SELECT COUNT(*) AS value FROM emails")[0].get("value", 0)
    return {"emails": items, "total": int(total or 0)}


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
    db.execute(text("UPDATE emails SET status=:status, message_id=:message_id, sent_at=:sent_at, updated_at=:updated_at, attempts=attempts+1 WHERE id=:id"), {"status": status, "message_id": message_id, "sent_at": timestamp, "updated_at": timestamp, "id": email_id})
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


def sync_inbox(db: Session, limit: int = 30) -> dict[str, Any]:
    from .config import get_settings
    s = get_settings()
    if not s.imap_username or not s.imap_password:
        raise ValueError("IMAP credentials are not configured")
    client = imaplib.IMAP4_SSL(s.imap_host, s.imap_port); client.login(s.imap_username, s.imap_password); client.select("INBOX")
    _, data = client.uid("search", None, "ALL"); uids = [int(x) for x in data[0].split()]
    existing = rows(db, "SELECT uid FROM inbox_emails ORDER BY uid DESC LIMIT :limit", {"limit": limit * 3}); known = {int(x["uid"]) for x in existing}; added = 0
    for uid in reversed(uids):
        if uid in known or added >= limit: continue
        _, fetched = client.uid("fetch", str(uid), "(RFC822)")
        raw = next((part[1] for part in fetched if isinstance(part, tuple)), None)
        if not raw: continue
        msg = BytesParser(policy=policy.default).parsebytes(raw)
        sender_name, sender_email = parseaddr(str(msg.get("From", "")))
        body = msg.get_body(preferencelist=("plain", "html"))
        content = body.get_content() if body else ""
        if body and body.get_content_type() == "text/html": content = __import__('re').sub(r"<[^>]+>", " ", content)
        received = msg.get("Date") or now_iso()
        db.execute(text("""INSERT INTO inbox_emails (uid,message_id,from_name,from_email,to_email,subject,body,html_body,snippet,is_read,received_at,created_at)
            VALUES (:uid,:message_id,:from_name,:from_email,:to_email,:subject,:body,:html_body,:snippet,0,:received,:created)"""), {"uid": uid, "message_id": str(msg.get("Message-ID", "")), "from_name": _decode_header(sender_name), "from_email": sender_email, "to_email": s.imap_username, "subject": _decode_header(str(msg.get("Subject", "(No subject)"))), "body": content, "html_body": content if body and body.get_content_type() == "text/html" else "", "snippet": content[:255], "received": received, "created": now_iso()}); added += 1
    db.commit(); client.logout(); return {"new_count": added, "total_inbox": len(uids), "synced_at": now_iso()}


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

