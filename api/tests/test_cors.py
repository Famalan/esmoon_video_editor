def test_cors_preflight_allows_web_origin(client):
    origin = "http://localhost:3000"

    response = client.options(
        "/jobs",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-user",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == origin
    assert "POST" in response.headers["access-control-allow-methods"]
    assert "x-user" in response.headers["access-control-allow-headers"].lower()
