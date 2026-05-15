from fastapi import FastAPI

app = FastAPI(title="Video Slicer API")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
