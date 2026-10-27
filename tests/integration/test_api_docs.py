"""API reference pages and per-route documentation."""

from fastapi.testclient import TestClient

from ptjobs.api import create_app

client = TestClient(create_app())


def test_docs_serves_scalar_reference() -> None:
    resp = client.get("/docs")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "@scalar/api-reference@1.76.0" in resp.text
    assert "/openapi.json" in resp.text


def test_swagger_still_available() -> None:
    resp = client.get("/swagger")
    assert resp.status_code == 200
    assert "swagger-ui" in resp.text


def test_every_operation_is_documented() -> None:
    paths = create_app().openapi()["paths"]
    undocumented = [
        f"{method.upper()} {path}"
        for path, ops in paths.items()
        for method, op in ops.items()
        if not op.get("description")
    ]
    assert undocumented == []


def test_tags_have_descriptions() -> None:
    tags = create_app().openapi().get("tags", [])
    assert {t["name"] for t in tags} >= {"jobs", "subscriptions", "meta"}
    assert all(t.get("description") for t in tags)
