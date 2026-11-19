from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app import db as db_module
from app import main as main_module
from app import services
from app.scheduler import InboxSyncScheduler


class AutoSyncBackendTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmpdir.name) / "test.sqlite"
        self.engine = create_engine(
            f"sqlite:///{self.db_path}",
            connect_args={"check_same_thread": False},
        )
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False)
        self._orig_engine = db_module.engine
        db_module.engine = self.engine
        db_module.init_db()

    def tearDown(self) -> None:
        db_module.engine = self._orig_engine
        self.engine.dispose()
        self._tmpdir.cleanup()

    def test_defaults_and_persistence(self) -> None:
        with self.SessionLocal() as db:
            initial = services.get_sync_settings(db)
            self.assertFalse(initial["enabled"])
            self.assertEqual(initial["interval_seconds"], 300)
            self.assertEqual(initial["last_status"], "idle")
            self.assertIsNone(initial["last_sync_at"])
            self.assertIsNone(initial["next_sync_at"])
            self.assertFalse(initial["is_running"])

            saved = services.save_sync_settings(db, enabled=True, interval_seconds=120)
            self.assertTrue(saved["enabled"])
            self.assertEqual(saved["interval_seconds"], 120)
            self.assertIsNotNone(saved["next_sync_at"])

            reloaded = services.get_sync_settings(db)
            self.assertTrue(reloaded["enabled"])
            self.assertEqual(reloaded["interval_seconds"], 120)

    def test_interval_validation(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaises(ValueError):
                services.save_sync_settings(db, interval_seconds=10)
            with self.assertRaises(ValueError):
                services.save_sync_settings(db, interval_seconds=100000)
            with self.assertRaises(ValueError):
                services.save_sync_settings(db, interval_seconds=True)

    def test_manual_and_auto_sync_when_imap_unconfigured(self) -> None:
        with self.SessionLocal() as db:
            with patch.object(services, "imap_configured", return_value=False):
                with self.assertRaises(ValueError):
                    services.run_inbox_sync(db, trigger="manual")
                state = services.get_sync_settings(db)
                self.assertEqual(state["last_status"], "error")
                self.assertEqual(state["last_trigger"], "manual")
                self.assertIn("IMAP", state["last_error"])
                self.assertIsNotNone(state["last_sync_at"])

                auto_res = services.run_inbox_sync(db, trigger="auto")
                self.assertEqual(auto_res["status"], "skipped")
                self.assertEqual(auto_res["sync_settings"]["last_status"], "skipped")
                self.assertEqual(auto_res["sync_settings"]["last_trigger"], "auto")

    def test_successful_sync_records_status_and_activity(self) -> None:
        fake_synced_at = datetime.now(timezone.utc).isoformat()
        with self.SessionLocal() as db:
            services.save_sync_settings(db, enabled=True, interval_seconds=180)
            with (
                patch.object(services, "imap_configured", return_value=True),
                patch.object(
                    services,
                    "sync_inbox",
                    return_value={"new_count": 4, "total_inbox": 19, "synced_at": fake_synced_at},
                ),
            ):
                result = services.run_inbox_sync(db, limit=30, trigger="manual")
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["new_count"], 4)
            self.assertEqual(result["total_inbox"], 19)
            self.assertFalse(result["sync_settings"]["is_running"])
            self.assertEqual(result["sync_settings"]["last_status"], "success")
            self.assertEqual(result["sync_settings"]["last_new_count"], 4)
            self.assertEqual(result["sync_settings"]["last_total_inbox"], 19)
            self.assertEqual(result["sync_settings"]["last_trigger"], "manual")
            self.assertEqual(result["sync_settings"]["last_sync_at"], fake_synced_at)

            # Updating interval should preserve last sync metadata
            after_save = services.save_sync_settings(db, interval_seconds=600)
            self.assertEqual(after_save["last_status"], "success")
            self.assertEqual(after_save["last_new_count"], 4)
            self.assertEqual(after_save["last_sync_at"], fake_synced_at)

            activity = db.execute(text("SELECT action, detail FROM activity WHERE action = 'inbox_sync'")).fetchall()
            self.assertEqual(len(activity), 1)

    def test_concurrent_sync_is_rejected(self) -> None:
        with self.SessionLocal() as db:
            acquired = services._SYNC_LOCK.acquire(blocking=False)
            self.assertTrue(acquired)
            try:
                self.assertTrue(services.get_sync_settings(db)["is_running"])
                with self.assertRaises(RuntimeError):
                    services.run_inbox_sync(db, trigger="manual")
            finally:
                services._SYNC_LOCK.release()

    def test_scheduler_delay_and_wakeup(self) -> None:
        now = datetime.now(timezone.utc)
        self.assertEqual(
            InboxSyncScheduler.compute_delay({"interval_seconds": 300, "last_sync_at": None}, now),
            0.0,
        )
        recent = (now - timedelta(seconds=100)).isoformat()
        delay = InboxSyncScheduler.compute_delay({"interval_seconds": 300, "last_sync_at": recent}, now)
        self.assertAlmostEqual(delay, 200.0, delta=1.0)
        overdue = (now - timedelta(seconds=400)).isoformat()
        self.assertEqual(
            InboxSyncScheduler.compute_delay({"interval_seconds": 300, "last_sync_at": overdue}, now),
            0.0,
        )

        triggered = threading.Event()
        calls: list[str] = []

        def runner(db) -> dict:
            calls.append("ran")
            services._record_sync_result(
                db,
                status="success",
                trigger="auto",
                new_count=2,
                total_inbox=10,
                synced_at=services.now_iso(),
            )
            triggered.set()
            return {"new_count": 2, "total_inbox": 10}

        scheduler = InboxSyncScheduler(session_factory=self.SessionLocal, sync_runner=runner)
        scheduler.start()
        try:
            time.sleep(0.05)
            self.assertEqual(calls, [])

            with self.SessionLocal() as db:
                services.save_sync_settings(db, enabled=True, interval_seconds=60)
            scheduler.notify()
            self.assertTrue(triggered.wait(timeout=2.0))
            self.assertEqual(len(calls), 1)
        finally:
            scheduler.stop()

    def test_api_handlers(self) -> None:
        with self.SessionLocal() as db:
            state = main_module.get_inbox_sync_settings(db=db)
            self.assertFalse(state["enabled"])

            updated = main_module.update_inbox_sync_settings({"enabled": True, "interval_seconds": 180}, db=db)
            self.assertTrue(updated["enabled"])
            self.assertEqual(updated["interval_seconds"], 180)

            with self.assertRaises(HTTPException) as bad_req:
                main_module.update_inbox_sync_settings({"interval_seconds": 5}, db=db)
            self.assertEqual(bad_req.exception.status_code, 422)

            with patch.object(services, "imap_configured", return_value=False):
                with self.assertRaises(HTTPException) as unconfigured:
                    main_module.run_manual_inbox_sync(limit=30, db=db)
                self.assertEqual(unconfigured.exception.status_code, 422)

            fake_synced_at = datetime.now(timezone.utc).isoformat()
            with (
                patch.object(services, "imap_configured", return_value=True),
                patch.object(
                    services,
                    "sync_inbox",
                    return_value={"new_count": 3, "total_inbox": 12, "synced_at": fake_synced_at},
                ),
            ):
                manual_result = main_module.run_manual_inbox_sync(limit=30, db=db)
                self.assertEqual(manual_result["status"], "success")
                self.assertEqual(manual_result["new_count"], 3)
                self.assertEqual(manual_result["sync_settings"]["last_status"], "success")

    def test_inbox_date_normalization_search_and_unread_count(self) -> None:
        mon = services._normalize_received_at("Mon, 16 Nov 2026 10:00:00 +0000")
        wed = services._normalize_received_at("Wed, 18 Nov 2026 07:30:00 +0000")
        fri = services._normalize_received_at("Fri, 20 Nov 2026 09:00:00 +0530")
        self.assertLess(mon, wed)
        self.assertLess(wed, fri)
        self.assertEqual(fri, "2026-11-20T03:30:00+00:00")

        with self.SessionLocal() as db:
            rows_to_insert = [
                (1, "Rahul Sharma", "rahul@example.com", "Old Monday", "Body one", 0, mon),
                (2, "Priya Patel", "priya@example.com", "Mid Wednesday", "Body two", 1, wed),
                (3, "Aman Verma", "aman@example.com", "Newest Friday", "Body three", 0, fri),
            ]
            for uid, name, addr, subj, body, is_read, rec in rows_to_insert:
                db.execute(
                    text("""INSERT INTO inbox_emails
                        (uid, message_id, from_name, from_email, to_email, subject, body, html_body, snippet, is_read, received_at, created_at)
                        VALUES (:uid, '', :name, :addr, 'desk@example.com', :subj, :body, '', :body, :is_read, :rec, :rec)"""),
                    {"uid": uid, "name": name, "addr": addr, "subj": subj, "body": body, "is_read": is_read, "rec": rec},
                )
            db.commit()

            all_res = services.inbox(db)
            self.assertEqual(all_res["total"], 3)
            self.assertEqual(all_res["unread_count"], 2)
            self.assertEqual([e["subject"] for e in all_res["emails"]], ["Newest Friday", "Mid Wednesday", "Old Monday"])

            by_name = services.inbox(db, search="Rahul Sharma")
            self.assertEqual(by_name["total"], 1)
            self.assertEqual(by_name["emails"][0]["from_email"], "rahul@example.com")
            self.assertEqual(by_name["unread_count"], 2)

    def test_sent_filters_search_counts_and_retry_clears_error(self) -> None:
        with self.SessionLocal() as db:
            entries = [
                ("a@example.com", "Launch Note", "Hello team", "sent", "k1", None, "2026-11-18T08:00:00+00:00", "2026-11-18T07:55:00+00:00"),
                ("b@example.com", "Pending Promo", "Queued body", "pending", "k2", None, None, "2026-11-18T09:00:00+00:00"),
                ("c@example.com", "Failed Followup", "Retry body", "failed", "k3", "SMTP timeout", None, "2026-11-18T10:00:00+00:00"),
                ("d@example.com", "Dry Run Test", "Preview body", "dry_run", "k4", None, "2026-11-18T11:00:00+00:00", "2026-11-18T10:55:00+00:00"),
            ]
            for rcpt, subj, body, status, key, err, sent_at, created_at in entries:
                db.execute(
                    text("""INSERT INTO emails
                        (recipient, cc, bcc, subject, body, status, idempotency_key, attempts, error, sent_at, created_at, updated_at)
                        VALUES (:rcpt, '', '', :subj, :body, :status, :key, 1, :err, :sent_at, :created_at, :created_at)"""),
                    {
                        "rcpt": rcpt,
                        "subj": subj,
                        "body": body,
                        "status": status,
                        "key": key,
                        "err": err,
                        "sent_at": sent_at,
                        "created_at": created_at,
                    },
                )
            db.commit()

            all_res = services.sent(db)
            self.assertEqual(all_res["total"], 4)
            self.assertEqual(all_res["counts"], {"all": 4, "sent": 1, "pending": 1, "failed": 1, "dry_run": 1})
            self.assertEqual(all_res["emails"][0]["recipient"], "d@example.com")

            failed_res = services.sent(db, status="failed")
            self.assertEqual(failed_res["total"], 1)
            self.assertEqual(failed_res["emails"][0]["error"], "SMTP timeout")

            search_res = services.sent(db, search="Pending Promo")
            self.assertEqual(search_res["total"], 1)
            self.assertEqual(search_res["emails"][0]["recipient"], "b@example.com")

            # Retrying the failed email in dry-run mode should clear the previous error
            failed_id = int(failed_res["emails"][0]["id"])
            with patch.object(
                services,
                "_mail_settings",
                return_value={"host": "", "port": 587, "user": "", "password": "", "from": "", "from_name": "Desk", "dry_run": True},
            ):
                retried = services.send_email(db, failed_id)
            self.assertEqual(retried["status"], "dry_run")
            updated = db.execute(text("SELECT status, error FROM emails WHERE id = :id"), {"id": failed_id}).mappings().first()
            self.assertEqual(updated["status"], "dry_run")
            self.assertIsNone(updated["error"])

    def test_sheets_xlsx_sparse_csv_preview_queue_and_delete(self) -> None:
        # Build an XLSX with a sparse row (blank B2) and inlineStr cell
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr(
                "xl/sharedStrings.xml",
                "<sst><si><t>name</t></si><si><t>email</t></si><si><t>note</t></si><si><t>Rahul</t></si></sst>",
            )
            zf.writestr(
                "xl/worksheets/sheet1.xml",
                """<worksheet><sheetData>
                    <row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c><c r="C1" t="s"><v>2</v></c></row>
                    <row r="2"><c r="A2" t="s"><v>3</v></c><c r="C2" t="inlineStr"><is><t>VIP</t></is></c></row>
                </sheetData></worksheet>""",
            )
        parsed_xlsx = main_module.parse_xlsx(buf.getvalue())
        self.assertEqual(parsed_xlsx, [{"name": "Rahul", "email": "", "note": "VIP"}])

        csv_bytes = "name,email,phone\nAsha,asha@example.com,+919999999999\nBad,not-an-email,+918888888888\n".encode("utf-8")
        parsed_csv = main_module.parse_csv(csv_bytes)
        self.assertEqual(len(parsed_csv), 2)

        with self.SessionLocal() as db:
            sheet_id = "sheet-test-1"
            db.execute(
                text("INSERT INTO uploaded_sheets (id, title, workbook, created_at) VALUES (:id, :title, :wb, :created)"),
                {"id": sheet_id, "title": "leads.csv", "wb": json.dumps(parsed_csv), "created": services.now_iso()},
            )
            db.commit()

            listing = main_module.get_sheets(db=db)
            self.assertEqual(len(listing["uploads"]), 1)
            self.assertEqual(listing["uploads"][0]["row_count"], 2)
            self.assertEqual(listing["uploads"][0]["columns"], ["name", "email", "phone"])

            detail = main_module.get_sheet_detail(sheet_id, db=db, limit=25, offset=0)
            self.assertEqual(detail["total"], 2)
            self.assertEqual(detail["rows"][0]["name"], "Asha")

            queued_email = main_module.queue_sheet_campaign(
                sheet_id,
                {
                    "channel": "email",
                    "recipient_column": "email",
                    "subject_template": "Hello {{name}}",
                    "body_template": "Welcome {{name}}!",
                },
                db=db,
            )
            self.assertEqual(queued_email["queued"], 1)
            self.assertEqual(queued_email["skipped"], 1)
            self.assertEqual(queued_email["already_queued"], 0)

            # Re-queuing same sheet deduplicates valid row
            queued_again = main_module.queue_sheet_campaign(
                sheet_id,
                {
                    "channel": "email",
                    "recipient_column": "email",
                    "subject_template": "Hello {{name}}",
                    "body_template": "Welcome {{name}}!",
                },
                db=db,
            )
            self.assertEqual(queued_again["queued"], 0)
            self.assertEqual(queued_again["already_queued"], 1)

            queued_wa = main_module.queue_sheet_campaign(
                sheet_id,
                {
                    "channel": "whatsapp",
                    "recipient_column": "phone",
                    "message_template": "Hi {{name}}",
                },
                db=db,
            )
            self.assertEqual(queued_wa["queued"], 2)

            deleted = main_module.delete_sheet(sheet_id, db=db)
            self.assertTrue(deleted["ok"])
            self.assertEqual(main_module.get_sheets(db=db)["uploads"], [])

    def test_google_sheet_url_and_paste_import(self) -> None:
        sheet_id, gid = main_module.extract_google_sheet_ref(
            "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=987654"
        )
        self.assertEqual(sheet_id, "1AbCdEfGhIjKlMnOpQrStUvWxYz012345")
        self.assertEqual(gid, "987654")

        with self.assertRaises(ValueError):
            main_module.extract_google_sheet_ref("https://evil.example.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345")

        tsv = "name\temail\nRohan\trohan@example.com\nMeera\tmeera@example.com\n"
        with self.SessionLocal() as db:
            pasted = main_module.import_pasted_sheet({"title": "Copied Sheet", "text": tsv}, db=db)
            self.assertEqual(pasted["rows"], 2)
            self.assertEqual(pasted["columns"], ["name", "email"])

            with patch.object(
                main_module,
                "fetch_google_sheet_csv",
                return_value=(b"name,email\nKiran,kiran@example.com\n", "Leads - Sheet1.csv"),
            ):
                imported = main_module.import_google_sheet_url(
                    {"url": "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0"},
                    db=db,
                )
            self.assertEqual(imported["filename"], "Leads - Sheet1.csv")
            self.assertEqual(imported["rows"], 1)
            self.assertEqual(imported["spreadsheet_id"], "1AbCdEfGhIjKlMnOpQrStUvWxYz012345")
            self.assertEqual(len(main_module.get_sheets(db=db)["uploads"]), 2)


if __name__ == "__main__":
    unittest.main()
