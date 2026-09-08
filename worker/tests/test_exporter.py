from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest


class FakeStorage:
    def __init__(self, objects: dict[str, bytes]):
        self.objects = dict(objects)
        self.downloads: list[str] = []
        self.uploads: dict[str, tuple[bytes, str]] = {}

    def download_file(self, key: str, local_path: Path) -> None:
        self.downloads.append(key)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self.objects[key])

    def upload_file(self, local_path: Path, key: str, content_type: str) -> int:
        body = local_path.read_bytes()
        self.uploads[key] = (body, content_type)
        return len(body)


def _clip(**overrides):
    value = {
        "segment_id": str(uuid.uuid4()),
        "revision_id": str(uuid.uuid4()),
        "revision": 2,
        "index": 1,
        "start_sec": 15.0,
        "end_sec": 105.0,
        "actual_duration_sec": 90.0,
        "title": "Как ответить на сложный вопрос <без паники>",
        "summary": "Цельный ответ с аргументами, примером и выводом.",
        "yt_title": "Ответ: спокойствие & ясность",
        "yt_description": "Описание на русском <script>alert(1)</script>",
        "yt_tags": ["ответ", "практика"],
        "video_key": "immutable/revision/video.mp4",
        "thumbnail_keys": ["immutable/revision/thumb-1.jpg"],
        "selected_thumbnail_key": "immutable/revision/thumb-1.jpg",
        "validation": {
            "technical": {"ok": True, "decoded": True},
            "narrative": {"ok": True, "reason": "Полный ответ"},
        },
        "metadata_needs_review": False,
    }
    value.update(overrides)
    return value


def _snapshot(clips=None):
    return {
        "schema_version": 1,
        "source": {
            "id": str(uuid.uuid4()),
            "title": "Уволили на стриме: как постоять за себя?",
            "source_url": "https://example.test/watch?v=abcdefghijk",
            "duration_sec": 3600.0,
        },
        "job": {
            "id": str(uuid.uuid4()),
            "analysis_version": 3,
            "policy_snapshot": {"id": "whole-episodes", "version": "1.0"},
            "transcript_snapshot": {"key": "transcripts/v3.json", "version": "3"},
            "created_at": "2026-09-06T09:00:00+00:00",
        },
        "clips": clips if clips is not None else [_clip()],
    }


def test_builds_offline_package_with_exact_snapshot_and_cyrillic_pdf(tmp_path):
    from worker.services.exporter import build_export_artifacts

    storage = FakeStorage(
        {
            "immutable/revision/video.mp4": b"fake mp4 payload" * 100,
            "immutable/revision/thumb-1.jpg": b"fake jpeg payload" * 100,
        }
    )
    snapshot = _snapshot()
    export_id = str(uuid.uuid4())
    attempt_id = str(uuid.uuid4())
    artifacts = build_export_artifacts(
        snapshot=snapshot,
        export_id=export_id,
        attempt_id=attempt_id,
        storage_backend=storage,
        output_dir=tmp_path / "package",
    )

    assert artifacts.clip_count == 1
    assert storage.downloads == [
        "immutable/revision/video.mp4",
        "immutable/revision/thumb-1.jpg",
    ]
    manifest = json.loads(artifacts.manifest_path.read_text(encoding="utf-8"))
    exported = manifest["clips"][0]
    frozen = snapshot["clips"][0]
    assert exported["segment_id"] == frozen["segment_id"]
    assert exported["revision_id"] == frozen["revision_id"]
    assert exported["revision"] == 2
    video_file = exported["files"]["video"]
    assert video_file["path"].startswith("clips/")
    assert video_file["sha256"] == hashlib.sha256(
        storage.objects["immutable/revision/video.mp4"]
    ).hexdigest()
    assert "video_key" not in exported

    document = artifacts.html_path.read_text(encoding="utf-8")
    assert "<script>alert(1)</script>" not in document
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in document
    assert f'href="{video_file["path"]}"' in document
    assert "Распакуйте ZIP целиком" in document

    with zipfile.ZipFile(artifacts.zip_path) as archive:
        names = set(archive.namelist())
        assert {"index.html", "report.pdf", "manifest.json"} <= names
        assert video_file["path"] in names
        assert exported["files"]["selected_thumbnail"]["path"] in names
        zip_manifest = json.loads(archive.read("manifest.json"))
        assert zip_manifest == manifest
        assert archive.read(video_file["path"]) == storage.objects[
            "immutable/revision/video.mp4"
        ]

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        extracted = tmp_path / "report.txt"
        subprocess.run(
            [pdftotext, str(artifacts.pdf_path), str(extracted)], check=True
        )
        pdf_text = extracted.read_text(encoding="utf-8")
        assert "Уволили на стриме" in pdf_text
        assert "Ответ: спокойствие" in pdf_text

    pdftoppm = shutil.which("pdftoppm")
    if pdftoppm:
        subprocess.run(
            [pdftoppm, "-f", "1", "-singlefile", "-png", "-r", "120", str(artifacts.pdf_path), str(tmp_path / "report")],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        rendered = tmp_path / "report.png"
        assert rendered.is_file() and rendered.stat().st_size > 10_000


@pytest.mark.parametrize("duration", [89.999, 1500.001])
def test_rejects_actual_duration_outside_policy(duration):
    from worker.services.exporter import ExportValidationError, validate_snapshot

    clip = _clip(end_sec=15.0 + duration, actual_duration_sec=duration)
    with pytest.raises(ExportValidationError, match="Фактическая длительность"):
        validate_snapshot(_snapshot([clip]))


@pytest.mark.parametrize("duration", [90.0, 1500.0])
def test_accepts_inclusive_actual_duration_boundaries(duration):
    from worker.services.exporter import validate_snapshot

    clip = _clip(end_sec=15.0 + duration, actual_duration_sec=duration)
    assert validate_snapshot(_snapshot([clip])) == [clip]


def test_rejects_unverified_or_multi_interval_snapshot():
    from worker.services.exporter import ExportValidationError, validate_snapshot

    with pytest.raises(ExportValidationError, match="technical verification"):
        validate_snapshot(
            _snapshot([_clip(validation={"technical": {"ok": False}})])
        )
    with pytest.raises(ExportValidationError, match="multiple source intervals"):
        validate_snapshot(_snapshot([_clip(parts=[[15, 105]])]))


def test_rejects_selected_thumbnail_outside_frozen_assets():
    from worker.services.exporter import ExportValidationError, validate_snapshot

    with pytest.raises(ExportValidationError, match="selected thumbnail"):
        validate_snapshot(
            _snapshot([_clip(selected_thumbnail_key="mutable/latest.jpg")])
        )


class _Result:
    def __init__(self, value=None):
        self.value = value

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value


class _ExportSession:
    def __init__(self, export):
        self.export = export

    def execute(self, statement, params=None):
        return _Result(None if params is not None else self.export)

    def get(self, model, identity):
        return self.export

    def commit(self):
        return None

    def rollback(self):
        return None


def _scope_for(session):
    @contextmanager
    def scope():
        yield session

    return scope


def test_export_task_is_duplicate_safe_and_publishes_only_attempt_snapshot(
    tmp_path, monkeypatch
):
    from worker.tasks import exports as task_module

    attempt_id = uuid.uuid4()
    export_id = uuid.uuid4()
    export = SimpleNamespace(
        id=export_id,
        snapshot=_snapshot(),
        status="queued",
        attempt_id=attempt_id,
        error=None,
        zip_key=None,
        html_key=None,
        pdf_key=None,
        manifest_key=None,
        completed_at=None,
    )
    monkeypatch.setattr(task_module, "engine", SimpleNamespace(connect=_scope_for(object())))
    monkeypatch.setattr(task_module, "Session", lambda **kwargs: _scope_for(_ExportSession(export))())

    fake = FakeStorage(
        {
            "immutable/revision/video.mp4": b"fake mp4 payload" * 100,
            "immutable/revision/thumb-1.jpg": b"fake jpeg payload" * 100,
        }
    )
    monkeypatch.setattr(task_module.storage, "download_file", fake.download_file)
    monkeypatch.setattr(task_module.storage, "upload_file", fake.upload_file)

    task_module.build_export.run(str(export_id), str(attempt_id))
    upload_count = len(fake.uploads)
    assert upload_count == 4
    task_module.build_export.run(str(export_id), str(attempt_id))
    assert len(fake.uploads) == upload_count

    assert export.status == "succeeded"
    assert export.zip_key == f"exports/{export_id}/{attempt_id}/video-slicer-export.zip"
    assert export.html_key.endswith("/index.html")
    assert export.pdf_key.endswith("/report.pdf")
    assert export.manifest_key.endswith("/manifest.json")
    assert export.completed_at is not None


def test_export_task_ignores_stale_attempt(monkeypatch):
    from worker.tasks import exports as task_module

    current_attempt = uuid.uuid4()
    export_id = uuid.uuid4()
    export = SimpleNamespace(
        id=export_id,
        snapshot=_snapshot(),
        status="queued",
        attempt_id=current_attempt,
        error=None,
    )
    monkeypatch.setattr(task_module, "engine", SimpleNamespace(connect=_scope_for(object())))
    monkeypatch.setattr(task_module, "Session", lambda **kwargs: _scope_for(_ExportSession(export))())

    monkeypatch.setattr(
        task_module,
        "build_export_artifacts",
        lambda **kwargs: pytest.fail("stale task must not build an export"),
    )
    task_module.build_export.run(str(export_id), str(uuid.uuid4()))
    assert export.status == "queued"
