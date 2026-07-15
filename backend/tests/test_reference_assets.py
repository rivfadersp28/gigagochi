from types import SimpleNamespace

import pytest

from app.services.reference_assets import trusted_generated_asset_url


@pytest.fixture
def settings() -> SimpleNamespace:
    return SimpleNamespace(
        backend_public_url="https://api.example.test/base",
        webapp_url="https://app.example.test/app",
    )


def test_trusted_generated_asset_url_accepts_only_configured_generated_images(settings) -> None:
    assert (
        trusted_generated_asset_url(
            "/static/generated/pet/idle.png?v=7",
            settings,
        )
        == "https://api.example.test/static/generated/pet/idle.png?v=7"
    )
    assert (
        trusted_generated_asset_url(
            "https://app.example.test/static/generated/pet/idle.webp",
            settings,
        )
        == "https://app.example.test/static/generated/pet/idle.webp"
    )


@pytest.mark.parametrize(
    "image_url",
    [
        "http://169.254.169.254/latest/meta-data.png",
        "http://10.0.0.2/static/generated/pet/idle.png",
        "http://127.0.0.2/static/generated/pet/idle.png",
        "http://[fc00::1]/static/generated/pet/idle.png",
        "https://cdn.example.test/static/generated/pet/idle.png",
        "https://api.example.test/private/config.png",
        "https://api.example.test/static/generated/../private/config.png",
        "https://api.example.test/static/generated/%2e%2e/private/config.png",
        "data:image/png;base64,eA==",
    ],
)
def test_trusted_generated_asset_url_rejects_untrusted_sources(
    image_url: str,
    settings,
) -> None:
    assert trusted_generated_asset_url(image_url, settings) == ""
