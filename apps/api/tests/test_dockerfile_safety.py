from pathlib import Path


def test_api_image_installs_the_committed_lockfile() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert dockerfile.count("uv sync --frozen") == 2


def test_browser_download_is_cached_before_application_source() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    browser_install = dockerfile.index("playwright install chromium")
    application_copy = dockerfile.index("COPY . ./")

    assert browser_install < application_copy
    assert "playwright install chromium --with-deps" not in dockerfile
