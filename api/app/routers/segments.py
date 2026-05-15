from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Asset, AssetKind, Segment, Upload
from app.schemas import SegmentOut, SegmentPatch, SegmentsList, ThumbnailOut
from app.services import storage

router = APIRouter()


def _segment_to_out(seg: Segment, db: Session) -> SegmentOut:
    thumbs = db.execute(
        select(Asset).where(
            Asset.segment_id == seg.id,
            Asset.kind == AssetKind.THUMBNAIL,
        ).order_by(Asset.position_idx)
    ).scalars().all()

    upload = db.execute(
        select(Upload).where(Upload.segment_id == seg.id)
    ).scalar_one_or_none()

    seg_video = db.execute(
        select(Asset).where(
            Asset.segment_id == seg.id,
            Asset.kind == AssetKind.SEGMENT_VIDEO,
        )
    ).scalar_one_or_none()

    return SegmentOut(
        id=seg.id,
        job_id=seg.job_id,
        index=seg.index,
        start_sec=seg.start_sec,
        end_sec=seg.end_sec,
        title=seg.title,
        summary=seg.summary,
        yt_title=upload.youtube_title if upload else None,
        yt_description=upload.youtube_description if upload else None,
        yt_tags=list(upload.tags) if upload and upload.tags else None,
        selected_thumbnail_id=seg.selected_thumbnail_id,
        thumbnails=[
            ThumbnailOut(
                asset_id=a.id,
                position_idx=a.position_idx or 0,
                url=storage.presigned_get_url(a.s3_key),
            )
            for a in thumbs
        ],
        video_download_url=(
            f"/segments/{seg.id}/download" if seg_video else None
        ),
        status=seg.status.value,
    )


@router.get("/jobs/{job_id}/segments", response_model=SegmentsList)
def list_segments(job_id: uuid.UUID, db: Session = Depends(get_session)) -> SegmentsList:
    segments = db.execute(
        select(Segment).where(Segment.job_id == job_id).order_by(Segment.index)
    ).scalars().all()
    return SegmentsList(items=[_segment_to_out(s, db) for s in segments])


@router.get("/segments/{segment_id}", response_model=SegmentOut)
def get_segment(segment_id: uuid.UUID, db: Session = Depends(get_session)) -> SegmentOut:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail="segment not found")
    return _segment_to_out(seg, db)


@router.patch("/segments/{segment_id}", response_model=SegmentOut)
def patch_segment(
    segment_id: uuid.UUID,
    body: SegmentPatch,
    db: Session = Depends(get_session),
) -> SegmentOut:
    seg = db.get(Segment, segment_id)
    if seg is None:
        raise HTTPException(status_code=404, detail="segment not found")

    if body.selected_thumbnail_id is not None:
        thumb = db.get(Asset, body.selected_thumbnail_id)
        if thumb is None or thumb.segment_id != seg.id or thumb.kind != AssetKind.THUMBNAIL:
            raise HTTPException(status_code=400, detail="invalid thumbnail")
        seg.selected_thumbnail_id = thumb.id

    if body.yt_title is not None or body.yt_description is not None or body.yt_tags is not None:
        upload = db.execute(
            select(Upload).where(Upload.segment_id == seg.id)
        ).scalar_one_or_none()
        if upload is None:
            upload = Upload(segment_id=seg.id)
            db.add(upload)
        if body.yt_title is not None:
            upload.youtube_title = body.yt_title
        if body.yt_description is not None:
            upload.youtube_description = body.yt_description
        if body.yt_tags is not None:
            upload.tags = body.yt_tags

    db.commit()
    db.refresh(seg)
    return _segment_to_out(seg, db)


@router.get("/segments/{segment_id}/download")
def download_segment(segment_id: uuid.UUID, db: Session = Depends(get_session)) -> Response:
    asset = db.execute(
        select(Asset).where(
            Asset.segment_id == segment_id,
            Asset.kind == AssetKind.SEGMENT_VIDEO,
        )
    ).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="segment video not ready")
    url = storage.presigned_get_url(asset.s3_key)
    return RedirectResponse(url=url, status_code=302)
