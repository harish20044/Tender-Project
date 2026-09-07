"""Smoke tests for the application shell.

These run without any database, queue or model provider, so CI has a fast
signal that the app still builds and boots before slower suites run.
"""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint_reports_ok() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_openapi_schema_is_generated() -> None:
    with TestClient(create_app()) as client:
        response = client.get("/openapi.json")

    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Tender Intelligence API"
