from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.frontend import configure_frontend


def _build_test_app(dist_path: Path) -> FastAPI:
    """A minimal standalone app -- no DB/session dependencies -- so these
    tests exercise configure_frontend() in isolation, deterministically
    controlling whether frontend/dist exists rather than depending on
    whatever incidentally happens to be on disk in this checkout."""
    app = FastAPI()

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/me/repositories")
    def list_my_repositories() -> list[str]:
        return []

    @app.get("/repositories")
    def list_repositories() -> list[str]:
        return []

    configure_frontend(app, dist_path)
    return app


def _write_built_frontend(dist_path: Path) -> None:
    assets_dir = dist_path / "assets"
    assets_dir.mkdir(parents=True)
    (dist_path / "index.html").write_text("<html><body>frontend shell</body></html>")
    (assets_dir / "index-abc123.js").write_text("console.log('hi');")


def test_backend_only_startup_works_when_dist_is_absent(tmp_path: Path) -> None:
    app = _build_test_app(tmp_path / "does-not-exist")
    client = TestClient(app)

    # No SPA fallback was registered at all -- a plain FastAPI 404, not a
    # startup failure and not HTML.
    response = client.get("/")
    assert response.status_code == 404

    # Existing routes are completely unaffected either way.
    assert client.get("/health").status_code == 200
    assert client.get("/me/repositories").status_code == 200


def test_root_serves_the_spa_index_when_built(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/")

    assert response.status_code == 200
    assert "frontend shell" in response.text


def test_deep_link_serves_the_spa_index(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/repositories/3")

    assert response.status_code == 200
    assert "frontend shell" in response.text


def test_existing_backend_routes_retain_precedence_over_the_spa_fallback(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_bare_repositories_route_still_hits_the_backend_not_the_spa(tmp_path: Path) -> None:
    """The one genuinely tricky case: GET /repositories is a real backend
    route, but /repositories/:id is the frontend's own React Router route.
    Both share the "repositories" first path segment, so this proves they
    don't get confused with each other."""
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/repositories")

    assert response.status_code == 200
    assert response.json() == []


def test_unmatched_reserved_prefix_path_still_returns_a_real_404(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/me/this-route-does-not-exist")

    assert response.status_code == 404
    assert "frontend shell" not in response.text


def test_assets_directory_is_served(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    response = client.get("/assets/index-abc123.js")

    assert response.status_code == 200
    assert "console.log" in response.text


def test_spa_fallback_is_excluded_from_the_openapi_schema(tmp_path: Path) -> None:
    dist = tmp_path / "dist"
    _write_built_frontend(dist)
    client = TestClient(_build_test_app(dist))

    schema = client.get("/openapi.json").json()

    assert "/{full_path:path}" not in schema["paths"]
