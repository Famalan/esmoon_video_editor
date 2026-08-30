from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import assets, jobs, segments

app = FastAPI(title="Video Slicer API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(jobs.router)
app.include_router(segments.router)
app.include_router(assets.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
