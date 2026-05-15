import io
import uuid


def test_create_job_with_url_returns_201(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtube.com/watch?v=abc"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "queued"
    assert body["current_stage"] == "fetch"
    assert body["source_url"] == "https://youtube.com/watch?v=abc"
    assert body["created_by"] == "alice@example.com"
    assert "id" in body


def test_create_job_with_url_requires_source_url(client):
    response = client.post(
        "/jobs",
        json={"source_type": "url"},
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 422


def test_create_job_upload(client):
    files = {"file": ("video.mp4", io.BytesIO(b"FAKEMP4"), "video/mp4")}
    response = client.post(
        "/jobs/upload",
        files=files,
        headers={"X-User": "alice@example.com"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "file"
    assert body["source_url"] is None
