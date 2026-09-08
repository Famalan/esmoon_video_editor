from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import assets, jobs, segments, sources, exports, readiness
from app.middleware import UploadLimitMiddleware

app = FastAPI(title="Video Slicer API")
app.add_middleware(UploadLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "ETag"],
)
app.include_router(jobs.router)
app.include_router(segments.router)
app.include_router(assets.router)
app.include_router(sources.router)
app.include_router(exports.router)
app.include_router(readiness.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
