import csv
import hashlib
import io
import json
import uuid
import zipfile
import xml.etree.ElementTree as ET
import secrets
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .services import dashboard, google_authorization_url, google_exchange_code, inbox, process_email_queue, rows, send_email, send_whatsapp_cloud, sent, sync_inbox, whatsapp_overview, whatsapp_request

settings = get_settings()
app = FastAPI(title=settings.app_name, version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
init_db()


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def payload(request: dict[str, Any] | None) -> dict[str, Any]:
    return request or {}


def whatsapp_config(db: Session) -> tuple[str, str]:
    saved = rows(db, "SELECT base_url, access_token FROM whatsapp_account WHERE id=1")
    if saved:
        return str(saved[0].get("base_url") or settings.whatsapp_base_url), str(saved[0].get("access_token") or settings.whatsapp_access_token)
    saved = rows(db, "SELECT api_url, api_token FROM whatsapp_settings WHERE id=1")
    if saved:
        return str(saved[0].get("api_url") or settings.whatsapp_base_url), str(saved[0].get("api_token") or settings.whatsapp_access_token)
    return settings.whatsapp_base_url, settings.whatsapp_access_token


def whatsapp_cloud_config(db: Session) -> tuple[str, str, str, str]:
    saved = rows(db, "SELECT phone_number_id, api_token, api_version, verify_token FROM whatsapp_settings WHERE id=1")
    if saved:
        item = saved[0]
        return (str(item.get("phone_number_id") or settings.whatsapp_phone_number_id), str(item.get("api_token") or settings.whatsapp_access_token), str(item.get("api_version") or settings.whatsapp_api_version), str(item.get("verify_token") or settings.whatsapp_verify_token))
    return settings.whatsapp_phone_number_id, settings.whatsapp_access_token, settings.whatsapp_api_version, settings.whatsapp_verify_token


def parse_xlsx(content: bytes) -> list[dict[str, str]]:
    """Read the first worksheet from XLSX using the standard library."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            root = ET.fromstring(archive.read('xl/sharedStrings.xml'))
            shared = [''.join(node.itertext()) for node in root]
        sheet_name = 'xl/worksheets/sheet1.xml'
        root = ET.fromstring(archive.read(sheet_name))
        matrix: list[list[str]] = []
        for row in root.findall('.//{*}row'):
            values = []
            for cell in row.findall('{*}c'):
                value = cell.find('{*}v')
                text_value = '' if value is None else value.text or ''
                if cell.get('t') == 's' and text_value.isdigit():
                    text_value = shared[int(text_value)] if int(text_value) < len(shared) else text_value
                values.append(text_value)
            matrix.append(values)
    if not matrix:
        return []
    headers = [value or f'column_{index + 1}' for index, value in enumerate(matrix[0])]
    return [dict(zip(headers, row + [''] * (len(headers) - len(row)))) for row in matrix[1:]]


@app.get("/api/health")
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    phone_id, wa_token, _, _ = whatsapp_cloud_config(db)
    return {"status": "ok", "service": settings.app_name, "smtp": "configured" if settings.smtp_username and settings.smtp_password else "not_configured", "imap": "configured" if settings.imap_host and settings.imap_username and settings.imap_password else "not_configured", "whatsapp": "configured" if phone_id and wa_token else "not_configured"}


@app.get("/api/dashboard")
def get_dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    return dashboard(db)


@app.get("/api/inbox")
def get_inbox(db: Session = Depends(get_db), unread: str = "", search: str = "", limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    return inbox(db, unread, search, limit, offset)


@app.get("/api/inbox/{email_id}")
def get_inbox_email(email_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = rows(db, "SELECT * FROM inbox_emails WHERE id = :id", {"id": email_id})
    if not item: raise HTTPException(404, "Inbox message not found")
    return item[0]


@app.post("/api/inbox/{email_id}/read")
def mark_read(email_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    db.execute(text("UPDATE inbox_emails SET is_read = 1 WHERE id = :id"), {"id": email_id})
    db.commit()
    return {"ok": True}


@app.post("/api/inbox/{email_id}/toggle-read")
def toggle_read(email_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    db.execute(text("UPDATE inbox_emails SET is_read = CASE WHEN is_read = 1 THEN 0 ELSE 1 END WHERE id = :id"), {"id": email_id})
    db.commit()
    return {"ok": True}


@app.get("/api/emails")
def get_emails(db: Session = Depends(get_db), limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0)) -> dict[str, Any]:
    return sent(db, limit, offset)


@app.get("/api/emails/{email_id}")
def get_email(email_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    item = rows(db, "SELECT * FROM emails WHERE id = :id", {"id": email_id})
    if not item:
        raise HTTPException(404, "Email not found")
    return item[0]


@app.post("/api/emails/{email_id}/send")
def dispatch_email(email_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return send_email(db, email_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/workers/process-email")
def process_email_worker(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    return process_email_queue(db, limit)


@app.post("/api/workers/sync-inbox")
def sync_inbox_worker(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return sync_inbox(db, limit)
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/emails")
def queue_email(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload(request)
    recipient, subject, body = str(data.get("recipient", "")).strip(), str(data.get("subject", "")).strip(), str(data.get("body", ""))
    if not recipient or not subject or not body:
        raise HTTPException(422, "recipient, subject, and body are required")
    created = now()
    key = hashlib.sha256(f"{recipient}|{subject}|{body}".encode()).hexdigest()
    existing = rows(db, "SELECT id FROM emails WHERE idempotency_key = :key", {"key": key})
    if existing:
        return {"id": existing[0]["id"], "status": "already_queued"}
    db.execute(text("""INSERT INTO emails (recipient,cc,bcc,subject,body,status,idempotency_key,created_at,updated_at)
        VALUES (:recipient,:cc,:bcc,:subject,:body,'pending',:key,:created,:created)"""), {
        "recipient": recipient, "cc": str(data.get("cc", "")), "bcc": str(data.get("bcc", "")),
        "subject": subject, "body": body, "key": key, "created": created})
    db.commit()
    return {"id": db.execute(text("SELECT last_insert_rowid()")).scalar(), "status": "pending"}


@app.get("/api/whatsapp/overview")
def get_whatsapp_overview(db: Session = Depends(get_db), range: int = Query(7, ge=1, le=90)) -> dict[str, Any]:
    return whatsapp_overview(db, range)


@app.get("/api/whatsapp/sent")
def get_whatsapp_sent(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"messages": rows(db, "SELECT * FROM whatsapp_sends ORDER BY created_at DESC LIMIT 100")}


@app.post("/api/whatsapp/send")
def send_whatsapp(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload(request)
    recipient, message = str(data.get("recipient", "")).strip(), str(data.get("message", "")).strip()
    if not recipient or not message:
        raise HTTPException(422, "recipient and message are required")
    status = "queued"
    provider_error = None
    base_url, access_token = whatsapp_config(db)
    phone_id, cloud_token, cloud_version, _ = whatsapp_cloud_config(db)
    if phone_id and cloud_token:
        try:
            provider_result = send_whatsapp_cloud(recipient, message, phone_id, cloud_token, cloud_version)
            status = "sent"
        except ValueError as exc:
            status, provider_error = "failed", str(exc)
    elif base_url and access_token:
        try:
            provider_result = whatsapp_request("POST", "/unofficial/send-message", {"device_id": data.get("device_id", ""), "receiver_number": recipient, "message": message}, base_url, access_token)
            status = "sent"
        except ValueError as exc:
            status, provider_error = "failed", str(exc)
    db.execute(text("INSERT INTO whatsapp_sends (recipient,message,status,device_id,created_at) VALUES (:recipient,:message,:status,:device,:created)"), {
        "recipient": recipient, "message": message, "status": status, "device": data.get("device_id"), "created": now()})
    db.commit()
    return {"id": db.execute(text("SELECT last_insert_rowid()")).scalar(), "status": status, "error": provider_error, "provider": "meta_cloud" if phone_id else "legacy_gateway"}


@app.get("/api/whatsapp/chats")
def get_whatsapp_chats(db: Session = Depends(get_db)) -> dict[str, Any]:
    return {"chats": rows(db, "SELECT * FROM whatsapp_chats ORDER BY last_at DESC")}


@app.get("/api/whatsapp/chats/{chat_id}")
def get_whatsapp_thread(chat_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    chat = rows(db, "SELECT * FROM whatsapp_chats WHERE id = :id", {"id": chat_id})
    if not chat:
        raise HTTPException(404, "Chat not found")
    return {"chat": chat[0], "messages": rows(db, "SELECT * FROM whatsapp_messages WHERE chat_id = :id ORDER BY created_at", {"id": chat_id})}


@app.get("/api/whatsapp/device")
def get_whatsapp_devices(db: Session = Depends(get_db)) -> dict[str, list[Any]]:
    base_url, access_token = whatsapp_config(db)
    if base_url and access_token:
        try:
            return whatsapp_request("GET", "/api/devices", base_url=base_url, access_token=access_token)
        except ValueError:
            pass
    with SessionLocal() as db:
        return {"devices": rows(db, "SELECT * FROM whatsapp_devices ORDER BY id")}


@app.post("/api/whatsapp/device")
def add_whatsapp_device(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    name = str(request.get("name", "Device")).strip() or "Device"
    base_url, access_token = whatsapp_config(db)
    if base_url and access_token:
        try:
            return whatsapp_request("POST", "/api/devices", {"device_name": name}, base_url, access_token)
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc
    db.execute(text("INSERT INTO whatsapp_devices (name,phone,status,updated_at) VALUES (:name,:phone,'disconnected',:updated)"), {"name": name, "phone": str(request.get("phone", "")), "updated": now()})
    db.commit()
    return {"id": db.execute(text("SELECT last_insert_rowid()")).scalar(), "status": "disconnected"}


@app.post("/api/whatsapp/device/{device_id}/disconnect")
def disconnect_device(device_id: int, db: Session = Depends(get_db)) -> dict[str, bool]:
    base_url, access_token = whatsapp_config(db)
    if base_url and access_token:
        try:
            whatsapp_request("DELETE", f"/api/devices/{device_id}", base_url=base_url, access_token=access_token)
            return {"ok": True}
        except ValueError as exc:
            raise HTTPException(502, str(exc)) from exc
    db.execute(text("UPDATE whatsapp_devices SET status = 'disconnected', updated_at = :updated WHERE id = :id"), {"id": device_id, "updated": now()})
    db.commit()
    return {"ok": True}


@app.get("/api/whatsapp/settings")
def get_whatsapp_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    item = rows(db, "SELECT api_url, updated_at, phone_number_id, api_version, verify_token FROM whatsapp_settings WHERE id = 1")
    if item:
        return {"api_url": item[0].get("api_url") or "https://graph.facebook.com", "phone_number_id": item[0].get("phone_number_id", ""), "api_version": item[0].get("api_version", settings.whatsapp_api_version), "verify_token": item[0].get("verify_token", ""), "updated_at": item[0].get("updated_at"), "configured": bool(item[0].get("phone_number_id") and item[0].get("api_token"))}
    return {"api_url": "https://graph.facebook.com", "phone_number_id": settings.whatsapp_phone_number_id, "api_version": settings.whatsapp_api_version, "verify_token": "", "updated_at": None, "configured": bool(settings.whatsapp_phone_number_id and settings.whatsapp_access_token)}


@app.post("/api/whatsapp/settings")
def save_whatsapp_settings(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, bool]:
    existing = rows(db, "SELECT api_token, phone_number_id, api_version, verify_token FROM whatsapp_settings WHERE id = 1")
    current = existing[0] if existing else {}
    values = {"url": str(request.get("api_url", "https://graph.facebook.com")), "token": str(request.get("api_token", "")) or str(current.get("api_token") or settings.whatsapp_access_token), "phone_id": str(request.get("phone_number_id", "")) or str(current.get("phone_number_id") or settings.whatsapp_phone_number_id), "version": str(request.get("api_version", "")) or str(current.get("api_version") or settings.whatsapp_api_version), "verify": str(request.get("verify_token", "")) or str(current.get("verify_token") or settings.whatsapp_verify_token), "updated": now()}
    if rows(db, "SELECT id FROM whatsapp_settings WHERE id=1"):
        db.execute(text("UPDATE whatsapp_settings SET api_url=:url, api_token=:token, phone_number_id=:phone_id, api_version=:version, verify_token=:verify, updated_at=:updated WHERE id=1"), values)
    else:
        db.execute(text("INSERT INTO whatsapp_settings (id,api_url,api_token,phone_number_id,api_version,verify_token,updated_at) VALUES (1,:url,:token,:phone_id,:version,:verify,:updated)"), values)
    account = rows(db, "SELECT id FROM whatsapp_account WHERE id=1")
    if account:
        db.execute(text("UPDATE whatsapp_account SET base_url=:url, access_token=:token, updated_at=:updated WHERE id=1"), values)
    else:
        db.execute(text("INSERT INTO whatsapp_account (id,name,base_url,access_token,updated_at) VALUES (1,'WhatsApp',:url,:token,:updated)"), values)
    db.commit()
    return {"ok": True}


@app.get("/api/sheets")
def get_sheets(db: Session = Depends(get_db)) -> dict[str, Any]:
    sources = rows(db, "SELECT * FROM sheet_sources ORDER BY created_at DESC")
    uploads = rows(db, "SELECT id, title, created_at FROM uploaded_sheets ORDER BY created_at DESC")
    return {"sources": sources, "uploads": uploads}


@app.get("/api/google/status")
def google_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    connected = bool(rows(db, "SELECT id FROM google_connection WHERE id = 1"))
    return {"configured": bool(settings.google_client_id and settings.google_client_secret), "connected": connected, "upload_limit": 10 * 1024 * 1024}


@app.post("/api/google/connect")
def google_connect() -> dict[str, str]:
    try:
        return {"authorization_url": google_authorization_url(secrets.token_urlsafe(24))}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/api/google/callback")
def google_callback(code: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        token = google_exchange_code(db, code)
        return {"ok": True, "expires_in": token.get("expires_in")}
    except ValueError as exc:
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/google/disconnect")
def google_disconnect(db: Session = Depends(get_db)) -> dict[str, bool]:
    db.execute(text("DELETE FROM google_connection WHERE id = 1")); db.commit(); return {"ok": True}


@app.post("/api/sheets/upload")
async def upload_sheet(file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename or not file.filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(422, "Upload a CSV or XLSX file")
    content = await file.read()
    if file.filename.lower().endswith('.csv'):
        decoded = content.decode('utf-8-sig')
        parsed = list(csv.DictReader(io.StringIO(decoded)))
    else:
        try:
            parsed = parse_xlsx(content)
        except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, f"Could not read XLSX file: {exc}") from exc
    source_id = str(uuid.uuid4())
    with SessionLocal() as db:
        db.execute(text("INSERT INTO uploaded_sheets (id,title,workbook,created_at) VALUES (:id,:title,:workbook,:created)"), {"id": source_id, "title": file.filename, "workbook": json.dumps(parsed), "created": now()})
        db.commit()
    return {"status": "accepted", "filename": file.filename, "id": source_id, "rows": str(len(parsed))}

