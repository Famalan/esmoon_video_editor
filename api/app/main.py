from fastapi import FastAPI

from app.routers import assets, jobs, segments

app = FastAPI(title="Video Slicer API")
app.include_router(jobs.router)
app.include_router(segments.router)
app.include_router(assets.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
