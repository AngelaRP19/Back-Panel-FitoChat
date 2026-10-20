from fastapi.testclient import TestClient


def test_salud_ok(client: TestClient) -> None:
    response = client.get("/salud")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-Id"]


def test_salud_propaga_request_id(client: TestClient) -> None:
    response = client.get("/salud", headers={"X-Request-Id": "abc-123"})
    assert response.headers["X-Request-Id"] == "abc-123"
