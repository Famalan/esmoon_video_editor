from __future__ import annotations

import uuid


def _seed_segment_with_thumbs(session, job_id):
    from app.models import (
        Asset, AssetKind, Job, JobStatus, Segment, SegmentStatus,
        SourceType, Upload, UploadStatus,
    )
    from shared.stages import Stage

    job = Job(
        id=job_id,
        source_type=SourceType.URL,
        source_url="https://x",
        status=JobStatus.SUCCEEDED,
        current_stage=Stage.DONE,
        created_by="t@t",
    )
    session.add(job)
    seg = Segment(
        job_id=job_id, index=0, start_sec=0.0, end_sec=300.0,
        title="t", summary="s", status=SegmentStatus.METADATA_READY,
    )
    session.add(seg)
    session.flush()
    thumb_asset = Asset(
        job_id=job_id, kind=AssetKind.THUMBNAIL,
        s3_key=f"{job_id}/thumbnails/{seg.id}/1.jpg",
        mime="image/jpeg", size_bytes=10,
        segment_id=seg.id, position_idx=1,
    )
    session.add(thumb_asset)
    session.flush()
    seg.selected_thumbnail_id = thumb_asset.id
    upload = Upload(
        segment_id=seg.id,
        youtube_title="title v1",
        youtube_description="desc v1",
        tags=["a", "b"],
        status=UploadStatus.PENDING,
    )
    session.add(upload)
    session.commit()
    return seg.id, thumb_asset.id


def test_list_segments_for_job(client, db_session, monkeypatch):
    from app.services import storage as api_storage
    monkeypatch.setattr(
        api_storage, "presigned_get_url",
        lambda key, expires_in=3600: f"http://minio.local/{key}?sig=x",
    )

    job_id = uuid.uuid4()
    seg_id, thumb_id = _seed_segment_with_thumbs(db_session, job_id)

    r = client.get(f"/jobs/{job_id}/segments")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(seg_id)
    assert body["items"][0]["yt_title"] == "title v1"
    assert body["items"][0]["relevance"] == 100
    assert body["items"][0]["pain"] == 100
    assert body["items"][0]["hook"] == 100
    assert body["items"][0]["value"] == 100
    assert body["items"][0]["decision"] == "publish"
    assert body["items"][0]["thumbnails"][0]["position_idx"] == 1


def test_patch_segment_updates_metadata(client, db_session, monkeypatch):
    from app.models import Upload
    from app.services import storage as api_storage
    from sqlalchemy import select

    monkeypatch.setattr(
        api_storage, "presigned_get_url",
        lambda key, expires_in=3600: f"http://minio.local/{key}?sig=x",
    )

    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)

    r = client.patch(
        f"/segments/{seg_id}",
        json={"expected_revision": 1, "yt_title": "new title", "yt_tags": ["x", "y"]},
    )
    assert r.status_code == 200

    db_session.expire_all()
    upload = db_session.execute(select(Upload).where(Upload.segment_id == seg_id)).scalar_one()
    assert upload.youtube_title == "new title"
    assert upload.tags == ["x", "y"]


def test_patch_segment_changes_thumbnail_selection(client, db_session, monkeypatch):
    from app.models import Asset, AssetKind, Segment
    from app.services import storage as api_storage

    monkeypatch.setattr(
        api_storage, "presigned_get_url",
        lambda key, expires_in=3600: f"http://minio.local/{key}?sig=x",
    )

    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)

    new_thumb = Asset(
        job_id=job_id, kind=AssetKind.THUMBNAIL,
        s3_key=f"{job_id}/thumbnails/{seg_id}/2.jpg",
        mime="image/jpeg", size_bytes=10,
        segment_id=seg_id, position_idx=2,
    )
    db_session.add(new_thumb)
    db_session.commit()

    r = client.patch(
        f"/segments/{seg_id}",
        json={"expected_revision": 1, "selected_thumbnail_id": str(new_thumb.id)},
    )
    assert r.status_code == 200

    db_session.expire_all()
    seg = db_session.get(Segment, seg_id)
    assert seg.selected_thumbnail_id == new_thumb.id


def test_legacy_download_remains_available(client, db_session, monkeypatch):
    from app.models import Asset, AssetKind
    from app.services import storage
    from fastapi import Response
    job_id = uuid.uuid4()
    seg_id, _ = _seed_segment_with_thumbs(db_session, job_id)
    db_session.add(Asset(job_id=job_id,kind=AssetKind.SEGMENT_VIDEO,s3_key='legacy.mp4',mime='video/mp4',size_bytes=3,segment_id=seg_id))
    db_session.commit()
    monkeypatch.setattr(storage,'media_response',lambda key,request,filename=None: Response(b'mp4',media_type='video/mp4'))
    response = client.get(f'/segments/{seg_id}/download')
    assert response.status_code == 200
    assert response.content == b'mp4'
