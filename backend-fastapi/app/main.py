from contextlib import asynccontextmanager
import csv
import hashlib
import io
import json
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.orm import Session

from .config import get_settings
from .db import SessionLocal, get_db, init_db
from .scheduler import InboxSyncScheduler
from .services import (
    dashboard,
    get_sync_settings,
    google_authorization_url,
    google_exchange_code,
    inbox,
    process_email_queue,
    rows,
    run_inbox_sync,
    save_sync_settings,
    send_email,
    send_whatsapp_cloud,
    sent,
    whatsapp_overview,
    whatsapp_request,
)

settings = get_settings()
sync_scheduler = InboxSyncScheduler()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    sync_scheduler.start()
    try:
        yield
    finally:
        sync_scheduler.stop()


app = FastAPI(title=settings.app_name, version="1.0.0", lifespan=lifespan)
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


MAX_SHEET_UPLOAD_BYTES = 10 * 1024 * 1024
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TEMPLATE_TOKEN_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def _excel_col_index(cell_ref: str | None) -> int | None:
    if not cell_ref:
        return None
    letters = "".join(ch for ch in cell_ref if ch.isalpha()).upper()
    if not letters:
        return None
    index = 0
    for ch in letters:
        index = index * 26 + (ord(ch) - ord("A") + 1)
    return index - 1


def parse_xlsx(content: bytes) -> list[dict[str, str]]:
    """Read the first worksheet from XLSX using the standard library."""
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            shared = ["".join(node.itertext()) for node in root]
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in archive.namelist():
            candidates = sorted(n for n in archive.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
            if not candidates:
                return []
            sheet_name = candidates[0]
        root = ET.fromstring(archive.read(sheet_name))
        matrix: list[list[str]] = []
        for row in root.findall(".//{*}row"):
            values: list[str] = []
            for cell in row.findall("{*}c"):
                col_idx = _excel_col_index(cell.get("r"))
                cell_type = cell.get("t")
                if cell_type == "inlineStr":
                    inline = cell.find("{*}is")
                    text_value = "".join(inline.itertext()) if inline is not None else ""
                else:
                    value = cell.find("{*}v")
                    text_value = "" if value is None else value.text or ""
                    if cell_type == "s" and text_value.isdigit():
                        idx = int(text_value)
                        text_value = shared[idx] if idx < len(shared) else text_value
                if col_idx is not None:
                    while len(values) < col_idx:
                        values.append("")
                    if col_idx < len(values):
                        values[col_idx] = text_value
                    else:
                        values.append(text_value)
                else:
                    values.append(text_value)
            if any(str(v).strip() for v in values):
                matrix.append(values)
    if not matrix:
        return []
    width = max(len(r) for r in matrix)
    raw_headers = matrix[0] + [""] * (width - len(matrix[0]))
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(raw_headers):
        base = str(raw).strip() or f"column_{index + 1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        headers.append(base if count == 0 else f"{base}_{count + 1}")
    parsed: list[dict[str, str]] = []
    for row in matrix[1:]:
        padded = row + [""] * (len(headers) - len(row))
        record = {headers[i]: str(padded[i]).strip() for i in range(len(headers))}
        if any(record.values()):
            parsed.append(record)
    return parsed


def parse_csv(content: bytes) -> list[dict[str, str]]:
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(decoded))
    if not reader.fieldnames:
        return []
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(reader.fieldnames):
        base = (raw or "").strip() or f"column_{index + 1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        headers.append(base if count == 0 else f"{base}_{count + 1}")
    parsed: list[dict[str, str]] = []
    for raw_row in reader:
        record: dict[str, str] = {}
        for orig_key, clean_key in zip(reader.fieldnames, headers):
            val = raw_row.get(orig_key)
            record[clean_key] = "" if val is None else str(val).strip()
        if any(record.values()):
            parsed.append(record)
    return parsed


def _sheet_columns(records: list[dict[str, Any]]) -> list[str]:
    columns: list[str] = []
    for record in records[:20]:
        if isinstance(record, dict):
            for key in record.keys():
                key_str = str(key)
                if key_str not in columns:
                    columns.append(key_str)
    return columns


def _render_sheet_template(template: str, row: dict[str, Any]) -> str:
    lower_map = {str(k).strip().lower(): "" if v is None else str(v).strip() for k, v in row.items()}

    def replace(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if token in row and row[token] is not None:
            return str(row[token]).strip()
        return lower_map.get(token.lower(), "")

    return _TEMPLATE_TOKEN_RE.sub(replace, template).strip()


_GOOGLE_SHEET_URL_RE = re.compile(r"/spreadsheets/(?:u/\d+/)?d/([A-Za-z0-9-_]{20,})")
_GOOGLE_SHEET_ID_RE = re.compile(r"^[A-Za-z0-9-_]{20,}$")


def extract_google_sheet_ref(url_or_id: str) -> tuple[str, str]:
    raw = (url_or_id or "").strip()
    if not raw:
        raise ValueError("Paste a Google Sheets URL or spreadsheet ID")
    if _GOOGLE_SHEET_ID_RE.match(raw):
        return raw, "0"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or parsed.netloc.lower() not in ("docs.google.com", "www.docs.google.com"):
        raise ValueError("Enter a valid Google Sheets URL (https://docs.google.com/spreadsheets/d/...)")
    match = _GOOGLE_SHEET_URL_RE.search(parsed.path or "")
    if not match:
        raise ValueError("Could not find a Google Spreadsheet ID in that URL")
    sheet_id = match.group(1)
    gid = "0"
    for source in (parsed.query, parsed.fragment):
        params = parse_qs(source or "")
        if "gid" in params and params["gid"] and params["gid"][0].isdigit():
            gid = params["gid"][0]
            break
    return sheet_id, gid


def fetch_google_sheet_csv(spreadsheet_id: str, gid: str = "0", access_token: str | None = None) -> tuple[bytes, str]:
    export_url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={gid}"
    headers = {"Accept": "text/csv,text/plain;q=0.9,*/*;q=0.8", "User-Agent": "CommunicationDesk/1.0"}
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    req = Request(export_url, headers=headers, method="GET")
    try:
        with urlopen(req, timeout=20) as resp:
            final_url = resp.geturl()
            content_type = str(resp.headers.get("Content-Type", "")).lower()
            if "accounts.google.com" in final_url or "text/html" in content_type:
                raise ValueError(
                    "This Google Sheet is private. Share it as 'Anyone with the link (Viewer)' or connect Google Sheets OAuth."
                )
            content = resp.read(MAX_SHEET_UPLOAD_BYTES + 1)
            if len(content) > MAX_SHEET_UPLOAD_BYTES:
                raise ValueError("Google Sheet exceeds the 10 MB import limit")
            disposition = str(resp.headers.get("Content-Disposition", ""))
            filename = ""
            utf8_match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
            if utf8_match:
                filename = unquote(utf8_match.group(1)).strip()
            else:
                plain_match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
                if plain_match:
                    filename = plain_match.group(1).strip()
            return content, filename or f"Google Sheet ({spreadsheet_id[:8]}).csv"
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ValueError(
                "This Google Sheet is private. Share it as 'Anyone with the link (Viewer)' or connect Google Sheets OAuth."
            ) from exc
        if exc.code == 404:
            raise ValueError("Google Sheet not found. Check the link and tab (gid).") from exc
        raise ValueError(f"Google Sheets returned HTTP {exc.code}") from exc
    except URLError as exc:
        raise ValueError(f"Could not reach Google Sheets: {exc.reason}") from exc


def parse_pasted_table(raw_text: str) -> list[dict[str, str]]:
    cleaned = (raw_text or "").strip("\r\n")
    if not cleaned.strip():
        return []
    first_line = next((line for line in cleaned.splitlines() if line.strip()), "")
    delimiter = "\t" if "\t" in first_line else ","
    reader = csv.DictReader(io.StringIO(cleaned), delimiter=delimiter)
    if not reader.fieldnames:
        return []
    headers: list[str] = []
    seen: dict[str, int] = {}
    for index, raw in enumerate(reader.fieldnames):
        base = (raw or "").strip() or f"column_{index + 1}"
        count = seen.get(base, 0)
        seen[base] = count + 1
        headers.append(base if count == 0 else f"{base}_{count + 1}")
    parsed: list[dict[str, str]] = []
    for raw_row in reader:
        record: dict[str, str] = {}
        for orig_key, clean_key in zip(reader.fieldnames, headers):
            val = raw_row.get(orig_key)
            record[clean_key] = "" if val is None else str(val).strip()
        if any(record.values()):
            parsed.append(record)
    return parsed


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
def get_emails(
    db: Session = Depends(get_db),
    status: str = "",
    search: str = "",
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    return sent(db, status=status, search=search, limit=limit, offset=offset)


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
        if str(exc) != "Email not found":
            db.execute(
                text("UPDATE emails SET status='failed', error=:error, attempts=attempts+1, updated_at=:updated WHERE id=:id"),
                {"error": str(exc), "updated": now(), "id": email_id},
            )
            db.commit()
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        db.execute(
            text("UPDATE emails SET status='failed', error=:error, attempts=attempts+1, updated_at=:updated WHERE id=:id"),
            {"error": str(exc), "updated": now(), "id": email_id},
        )
        db.commit()
        raise HTTPException(502, str(exc)) from exc


@app.post("/api/workers/process-email")
def process_email_worker(limit: int = Query(25, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    return process_email_queue(db, limit)


@app.post("/api/workers/sync-inbox")
def sync_inbox_worker(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return run_inbox_sync(db, limit=limit, trigger="manual")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        sync_scheduler.notify()


@app.get("/api/settings/sync")
def get_inbox_sync_settings(db: Session = Depends(get_db)) -> dict[str, Any]:
    return get_sync_settings(db)


@app.post("/api/settings/sync")
def update_inbox_sync_settings(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload(request)
    enabled = data.get("enabled")
    if enabled is not None and not isinstance(enabled, bool):
        raise HTTPException(422, "enabled must be a boolean")
    interval_raw = data.get("interval_seconds")
    interval_seconds: int | None = None
    if interval_raw is not None:
        if isinstance(interval_raw, bool):
            raise HTTPException(422, "interval_seconds must be an integer")
        try:
            interval_seconds = int(interval_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, "interval_seconds must be an integer") from exc
    try:
        updated = save_sync_settings(db, enabled=enabled, interval_seconds=interval_seconds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    sync_scheduler.notify()
    return updated


@app.post("/api/settings/sync/run")
def run_manual_inbox_sync(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return run_inbox_sync(db, limit=limit, trigger="manual")
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, str(exc)) from exc
    finally:
        sync_scheduler.notify()


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
    raw_uploads = rows(db, "SELECT id, title, workbook, created_at FROM uploaded_sheets ORDER BY created_at DESC")
    uploads = []
    for item in raw_uploads:
        try:
            records = json.loads(item.get("workbook") or "[]")
            if not isinstance(records, list):
                records = []
        except json.JSONDecodeError:
            records = []
        uploads.append({
            "id": item["id"],
            "title": item["title"],
            "created_at": item["created_at"],
            "row_count": len(records),
            "columns": _sheet_columns(records),
        })
    return {"sources": sources, "uploads": uploads}


@app.get("/api/sheets/{sheet_id}")
def get_sheet_detail(
    sheet_id: str,
    db: Session = Depends(get_db),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> dict[str, Any]:
    item = rows(db, "SELECT id, title, workbook, created_at FROM uploaded_sheets WHERE id = :id", {"id": sheet_id})
    if not item:
        raise HTTPException(404, "Uploaded sheet not found")
    record = item[0]
    try:
        all_rows = json.loads(record.get("workbook") or "[]")
        if not isinstance(all_rows, list):
            all_rows = []
    except json.JSONDecodeError:
        all_rows = []
    return {
        "id": record["id"],
        "title": record["title"],
        "created_at": record["created_at"],
        "columns": _sheet_columns(all_rows),
        "total": len(all_rows),
        "rows": all_rows[offset : offset + limit],
    }


@app.delete("/api/sheets/{sheet_id}")
def delete_sheet(sheet_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    existing = rows(db, "SELECT id FROM uploaded_sheets WHERE id = :id", {"id": sheet_id})
    if not existing:
        raise HTTPException(404, "Uploaded sheet not found")
    db.execute(text("DELETE FROM uploaded_sheets WHERE id = :id"), {"id": sheet_id})
    db.commit()
    return {"ok": True}


@app.post("/api/sheets/{sheet_id}/queue")
def queue_sheet_campaign(sheet_id: str, request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    item = rows(db, "SELECT id, title, workbook FROM uploaded_sheets WHERE id = :id", {"id": sheet_id})
    if not item:
        raise HTTPException(404, "Uploaded sheet not found")
    try:
        records = json.loads(item[0].get("workbook") or "[]")
        if not isinstance(records, list):
            records = []
    except json.JSONDecodeError:
        records = []
    if not records:
        raise HTTPException(422, "Sheet contains no rows to queue")

    data = payload(request)
    channel = str(data.get("channel") or "email").strip().lower()
    recipient_col = str(data.get("recipient_column") or "").strip()
    if not recipient_col:
        raise HTTPException(422, "recipient_column is required")

    created = now()
    queued = 0
    already_queued = 0
    skipped = 0
    errors: list[str] = []

    if channel == "whatsapp":
        message_col = str(data.get("message_column") or "").strip()
        message_tpl = str(data.get("message_template") or "").strip()
        if not message_col and not message_tpl:
            raise HTTPException(422, "Select a message column or provide a message template")
        for idx, row in enumerate(records, start=1):
            recipient = str(row.get(recipient_col, "") or "").strip()
            message = _render_sheet_template(message_tpl, row) if message_tpl else str(row.get(message_col, "") or "").strip()
            if not recipient or not message:
                skipped += 1
                if len(errors) < 10:
                    errors.append(f"Row {idx}: missing recipient or message")
                continue
            db.execute(
                text("INSERT INTO whatsapp_sends (recipient,message,status,device_id,created_at) VALUES (:recipient,:message,'queued',NULL,:created)"),
                {"recipient": recipient, "message": message, "created": created},
            )
            queued += 1
        db.commit()
        return {"channel": "whatsapp", "queued": queued, "already_queued": 0, "skipped": skipped, "total_rows": len(records), "errors": errors}

    if channel != "email":
        raise HTTPException(422, "channel must be 'email' or 'whatsapp'")

    subject_col = str(data.get("subject_column") or "").strip()
    subject_tpl = str(data.get("subject_template") or "").strip()
    body_col = str(data.get("body_column") or "").strip()
    body_tpl = str(data.get("body_template") or "").strip()
    cc_col = str(data.get("cc_column") or "").strip()
    bcc_col = str(data.get("bcc_column") or "").strip()

    if not subject_col and not subject_tpl:
        raise HTTPException(422, "Select a subject column or provide a subject template")
    if not body_col and not body_tpl:
        raise HTTPException(422, "Select a body column or provide a message template")

    for idx, row in enumerate(records, start=1):
        recipient = str(row.get(recipient_col, "") or "").strip()
        subject = _render_sheet_template(subject_tpl, row) if subject_tpl else str(row.get(subject_col, "") or "").strip()
        body = _render_sheet_template(body_tpl, row) if body_tpl else str(row.get(body_col, "") or "").strip()
        cc = str(row.get(cc_col, "") or "").strip() if cc_col else ""
        bcc = str(row.get(bcc_col, "") or "").strip() if bcc_col else ""

        if not recipient or not _EMAIL_RE.match(recipient):
            skipped += 1
            if len(errors) < 10:
                errors.append(f"Row {idx}: invalid recipient email '{recipient or '(empty)'}'")
            continue
        if not subject or not body:
            skipped += 1
            if len(errors) < 10:
                errors.append(f"Row {idx}: missing subject or body")
            continue

        key = hashlib.sha256(f"{recipient}|{subject}|{body}".encode()).hexdigest()
        if rows(db, "SELECT id FROM emails WHERE idempotency_key = :key", {"key": key}):
            already_queued += 1
            continue
        db.execute(
            text("""INSERT INTO emails (recipient,cc,bcc,subject,body,status,idempotency_key,created_at,updated_at)
                VALUES (:recipient,:cc,:bcc,:subject,:body,'pending',:key,:created,:created)"""),
            {"recipient": recipient, "cc": cc, "bcc": bcc, "subject": subject[:255], "body": body, "key": key, "created": created},
        )
        queued += 1

    if queued:
        db.execute(
            text("INSERT INTO activity (email_id, action, detail, created_at) VALUES (NULL, 'sheet_queued', :detail, :created)"),
            {"detail": f"{item[0]['title']}: {queued} queued", "created": created},
        )
    db.commit()
    return {
        "channel": "email",
        "queued": queued,
        "already_queued": already_queued,
        "skipped": skipped,
        "total_rows": len(records),
        "errors": errors,
    }


@app.get("/api/google/status")
def google_status(db: Session = Depends(get_db)) -> dict[str, Any]:
    connected = bool(rows(db, "SELECT id FROM google_connection WHERE id = 1"))
    return {"configured": bool(settings.google_client_id and settings.google_client_secret), "connected": connected, "upload_limit": MAX_SHEET_UPLOAD_BYTES}


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
async def upload_sheet(file: UploadFile = File(...), db: Session = Depends(get_db)) -> dict[str, Any]:
    if not file.filename or not file.filename.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(422, "Upload a CSV or XLSX file")
    content = await file.read()
    if len(content) > MAX_SHEET_UPLOAD_BYTES:
        raise HTTPException(422, "File exceeds the 10 MB upload limit")
    if file.filename.lower().endswith(".csv"):
        try:
            parsed = parse_csv(content)
        except csv.Error as exc:
            raise HTTPException(422, f"Could not read CSV file: {exc}") from exc
    else:
        try:
            parsed = parse_xlsx(content)
        except (KeyError, ET.ParseError, zipfile.BadZipFile) as exc:
            raise HTTPException(422, f"Could not read XLSX file: {exc}") from exc
    if not parsed:
        raise HTTPException(422, "Spreadsheet contains no data rows")
    source_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO uploaded_sheets (id,title,workbook,created_at) VALUES (:id,:title,:workbook,:created)"),
        {"id": source_id, "title": file.filename, "workbook": json.dumps(parsed), "created": now()},
    )
    db.commit()
    return {
        "status": "accepted",
        "filename": file.filename,
        "id": source_id,
        "rows": len(parsed),
        "columns": _sheet_columns(parsed),
    }


@app.post("/api/sheets/import-google-url")
def import_google_sheet_url(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload(request)
    url = str(data.get("url") or "").strip()
    custom_title = str(data.get("title") or "").strip()
    try:
        spreadsheet_id, gid = extract_google_sheet_ref(url)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    access_token: str | None = None
    conn = rows(db, "SELECT token_data FROM google_connection WHERE id = 1")
    if conn:
        try:
            token_obj = json.loads(conn[0].get("token_data") or "{}")
            access_token = str(token_obj.get("access_token") or "") or None
        except json.JSONDecodeError:
            access_token = None

    try:
        content, detected_name = fetch_google_sheet_csv(spreadsheet_id, gid=gid, access_token=access_token)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    try:
        parsed = parse_csv(content)
    except csv.Error as exc:
        raise HTTPException(422, f"Could not parse Google Sheet CSV: {exc}") from exc
    if not parsed:
        raise HTTPException(422, "Google Sheet contains no data rows")

    title = custom_title or detected_name
    source_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO uploaded_sheets (id,title,workbook,created_at) VALUES (:id,:title,:workbook,:created)"),
        {"id": source_id, "title": title, "workbook": json.dumps(parsed), "created": now()},
    )
    db.commit()
    return {
        "status": "accepted",
        "filename": title,
        "id": source_id,
        "rows": len(parsed),
        "columns": _sheet_columns(parsed),
        "spreadsheet_id": spreadsheet_id,
        "gid": gid,
    }


@app.post("/api/sheets/import-paste")
def import_pasted_sheet(request: dict[str, Any], db: Session = Depends(get_db)) -> dict[str, Any]:
    data = payload(request)
    raw_text = str(data.get("text") or "")
    title = str(data.get("title") or "").strip() or f"Pasted Sheet ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')})"
    if not raw_text.strip():
        raise HTTPException(422, "Paste rows copied from Google Sheets or CSV")
    if len(raw_text.encode("utf-8")) > MAX_SHEET_UPLOAD_BYTES:
        raise HTTPException(422, "Pasted data exceeds the 10 MB limit")
    try:
        parsed = parse_pasted_table(raw_text)
    except csv.Error as exc:
        raise HTTPException(422, f"Could not read pasted table: {exc}") from exc
    if not parsed:
        raise HTTPException(422, "Need at least 1 header row and 1 data row")

    source_id = str(uuid.uuid4())
    db.execute(
        text("INSERT INTO uploaded_sheets (id,title,workbook,created_at) VALUES (:id,:title,:workbook,:created)"),
        {"id": source_id, "title": title, "workbook": json.dumps(parsed), "created": now()},
    )
    db.commit()
    return {
        "status": "accepted",
        "filename": title,
        "id": source_id,
        "rows": len(parsed),
        "columns": _sheet_columns(parsed),
    }

