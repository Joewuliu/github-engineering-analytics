from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200


def test_health_returns_expected_body(client: TestClient) -> None:
    response = client.get("/health")
    assert response.json() == {"status": "ok"}


def test_health_content_type_is_json(client: TestClient) -> None:
    response = client.get("/health")
    assert response.headers["content-type"] == "application/json"


def test_openapi_schema_is_available(client: TestClient) -> None:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
