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


def test_get_job_by_id(client):
    create = client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtu.be/x"},
        headers={"X-User": "alice@example.com"},
    )
    job_id = create.json()["id"]

    response = client.get(f"/jobs/{job_id}")
    assert response.status_code == 200
    assert response.json()["id"] == job_id


def test_get_job_not_found(client):
    response = client.get(f"/jobs/{uuid.uuid4()}")
    assert response.status_code == 404


def test_list_jobs(client):
    client.post(
        "/jobs",
        json={"source_type": "url", "source_url": "https://youtu.be/x"},
        headers={"X-User": "alice@example.com"},
    )
    response = client.get("/jobs")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1
