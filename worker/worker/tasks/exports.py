from __future__ import annotations

import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from worker.celery_app import app
from worker.db import engine
from worker.models import Export
from worker.services import storage
from worker.services.exporter import build_export_artifacts


def _same_attempt(export: Export | None, attempt_id: str) -> bool:
    return export is not None and str(export.attempt_id) == str(attempt_id)


@app.task(name="worker.tasks.exports.build_export")
def build_export(export_id: str, attempt_id: str) -> str:
    """Build and publish one frozen export; safe under duplicate delivery."""
    try:
        export_uuid = uuid.UUID(str(export_id))
        attempt_uuid = uuid.UUID(str(attempt_id))
    except ValueError as exc:
        raise ValueError("export_id and attempt_id must be UUIDs") from exc
    export_id = str(export_uuid)
    attempt_id = str(attempt_uuid)

    lock_key = f"video-slicer:export:{export_id}"
    tmp = Path(tempfile.mkdtemp(prefix=f"export_{export_id}_"))
    locked = False
    # The external connection stays checked out across commits, so its session lock
    # really protects the entire build and cannot leak into the general pool.
    with engine.connect() as connection, Session(bind=connection) as db:
        try:
            db.execute(
                text("SELECT pg_advisory_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            locked = True
            export = db.execute(
                select(Export).where(Export.id == export_uuid).with_for_update()
            ).scalar_one_or_none()
            if export is None:
                raise LookupError(f"Export {export_id} does not exist.")
            if not _same_attempt(export, attempt_id):
                return export_id
            if export.status == "succeeded":
                return export_id
            export.status = "processing"
            export.error = None
            db.commit()

            artifacts = build_export_artifacts(
                snapshot=export.snapshot,
                export_id=export_id,
                attempt_id=attempt_id,
                storage_backend=storage,
                output_dir=tmp,
            )
            prefix = f"exports/{export_id}/{attempt_id}"
            keys = {
                "zip_key": f"{prefix}/video-slicer-export.zip",
                "html_key": f"{prefix}/index.html",
                "pdf_key": f"{prefix}/report.pdf",
                "manifest_key": f"{prefix}/manifest.json",
            }
            storage.upload_file(artifacts.zip_path, keys["zip_key"], "application/zip")
            storage.upload_file(artifacts.html_path, keys["html_key"], "text/html; charset=utf-8")
            storage.upload_file(artifacts.pdf_path, keys["pdf_key"], "application/pdf")
            storage.upload_file(
                artifacts.manifest_path,
                keys["manifest_key"],
                "application/json; charset=utf-8",
            )

            current = db.execute(
                select(Export).where(Export.id == export_uuid).with_for_update()
            ).scalar_one()
            if not _same_attempt(current, attempt_id):
                db.rollback()
                return export_id
            if current.status == "succeeded":
                db.rollback()
                return export_id
            current.zip_key = keys["zip_key"]
            current.html_key = keys["html_key"]
            current.pdf_key = keys["pdf_key"]
            current.manifest_key = keys["manifest_key"]
            current.status = "succeeded"
            current.completed_at = datetime.now(timezone.utc)
            current.error = None
            db.commit()
            return export_id
        except Exception as exc:
            db.rollback()
            current = db.get(Export, export_uuid)
            if _same_attempt(current, attempt_id) and current.status != "succeeded":
                current.status = "failed"
                current.error = f"export: {exc}"[:1000]
                db.commit()
            raise
        finally:
            if locked:
                try:
                    db.execute(
                        text("SELECT pg_advisory_unlock(hashtextextended(:key, 0))"),
                        {"key": lock_key},
                    )
                    db.commit()
                except Exception:
                    db.rollback()
            shutil.rmtree(tmp, ignore_errors=True)
