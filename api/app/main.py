from fastapi import FastAPI

from app.routers import jobs

app = FastAPI(title="Video Slicer API")
app.include_router(jobs.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
