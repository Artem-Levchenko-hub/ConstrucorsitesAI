import hashlib
import io
import tarfile
from pathlib import Path

from yleum_orchestrator.services.restoration_adaptation_source import (
    canonical_source_files,
    source_manifest,
    write_source_archive,
)


def test_canonical_source_preserves_binary_assets_and_excludes_runtime_and_secrets() -> None:
    favicon = b"\x00\x00\x01\x00\xff"
    logo = b"\x89PNG\r\n\x1a\n\x00\xff"

    source = canonical_source_files(
        {
            "app/page.tsx": b"export default function Page() {}",
            "public/favicon.ico": favicon,
            "public/logo.png": logo,
            ".next/cache/compiler.bin": b"mutable",
            "node_modules/pkg/index.js": b"runtime",
            ".env.production": b"SECRET=value",
        }
    )

    assert source == {
        "app/page.tsx": b"export default function Page() {}",
        "public/favicon.ico": favicon,
        "public/logo.png": logo,
    }
    assert source_manifest(source) == (
        {
            "path": "app/page.tsx",
            "size": 33,
            "sha256": hashlib.sha256(source["app/page.tsx"]).hexdigest(),
        },
        {
            "path": "public/favicon.ico",
            "size": len(favicon),
            "sha256": hashlib.sha256(favicon).hexdigest(),
        },
        {
            "path": "public/logo.png",
            "size": len(logo),
            "sha256": hashlib.sha256(logo).hexdigest(),
        },
    )


def test_source_archive_is_deterministic_and_keeps_exact_binary_bytes(
    tmp_path: Path,
) -> None:
    files = {
        "public/favicon.ico": b"\x00\xff\x00\x80",
        "app/page.tsx": b"export default 1",
    }
    first = tmp_path / "first.tar"
    second = tmp_path / "second.tar"

    first_digest = write_source_archive(files, first)
    second_digest = write_source_archive(dict(reversed(tuple(files.items()))), second)

    assert first_digest == second_digest
    assert first.read_bytes() == second.read_bytes()
    with tarfile.open(fileobj=io.BytesIO(first.read_bytes()), mode="r:") as archive:
        assert archive.getnames() == ["app/page.tsx", "public/favicon.ico"]
        extracted = archive.extractfile("public/favicon.ico")
        assert extracted is not None
        assert extracted.read() == files["public/favicon.ico"]
