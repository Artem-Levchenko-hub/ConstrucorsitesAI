from pathlib import Path


def test_api_image_installs_the_committed_lockfile() -> None:
    dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text(encoding="utf-8")

    assert "COPY pyproject.toml uv.lock ./" in dockerfile
    assert dockerfile.count("uv sync --frozen") == 2
