from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Asset
from app.services import storage

router = APIRouter()


@router.get("/assets/{asset_id}/url")
def asset_url(asset_id: uuid.UUID, db: Session = Depends(get_session)) -> dict[str, str]:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="asset not found")
    return {"url": storage.presigned_get_url(asset.s3_key)}
