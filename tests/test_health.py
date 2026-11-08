from fastapi.testclient import TestClient

from lan_distribution_lab.app import app


client = TestClient(app)


def test_health_returns_stable_success_payload() -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {
        "status": "ok",
        "service": "lan-distribution-lab",
    }


def test_health_rejects_an_unsupported_method() -> None:
    response = client.post("/api/health")

    assert response.status_code == 405
    assert response.json() == {"detail": "Method Not Allowed"}
