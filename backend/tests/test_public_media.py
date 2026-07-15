from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import app as production_app
from app.public_media import PublicMediaStaticFiles


def test_production_app_uses_the_restricted_static_mount() -> None:
    static_route = next(route for route in production_app.routes if route.name == "static")

    assert isinstance(static_route.app, PublicMediaStaticFiles)


def test_public_media_serves_media_but_blocks_colocated_private_files(tmp_path) -> None:
    (tmp_path / "poster.png").write_bytes(b"synthetic-image")
    (tmp_path / "finale.json").write_text(
        '{"owner":{"telegramId":123},"prompt":"private"}',
        encoding="utf-8",
    )
    (tmp_path / "prompt.txt").write_text("private prompt", encoding="utf-8")
    app = FastAPI()
    app.mount("/static", PublicMediaStaticFiles(directory=tmp_path), name="static")
    client = TestClient(app)

    media_response = client.get("/static/poster.png")

    assert media_response.status_code == 200
    assert media_response.content == b"synthetic-image"
    assert client.head("/static/poster.png").status_code == 200
    assert client.get("/static/finale.json").status_code == 404
    assert client.get("/static/prompt.txt").status_code == 404


def test_public_media_blocks_extensionless_and_nested_metadata(tmp_path) -> None:
    metadata_dir = tmp_path / "travel" / "attempts"
    metadata_dir.mkdir(parents=True)
    (metadata_dir / "private.json").write_text("{}", encoding="utf-8")
    (metadata_dir / "README").write_text("private", encoding="utf-8")
    app = FastAPI()
    app.mount("/static", PublicMediaStaticFiles(directory=tmp_path), name="static")
    client = TestClient(app)

    assert client.get("/static/travel/attempts/private.json").status_code == 404
    assert client.get("/static/travel/attempts/README").status_code == 404


def test_public_media_blocks_private_directories_even_for_media_suffixes(tmp_path) -> None:
    private_dir = tmp_path / ".private"
    private_dir.mkdir()
    (private_dir / "owner-registry.png").write_bytes(b"must-stay-private")
    app = FastAPI()
    app.mount("/static", PublicMediaStaticFiles(directory=tmp_path), name="static")

    response = TestClient(app).get("/static/.private/owner-registry.png")

    assert response.status_code == 404
