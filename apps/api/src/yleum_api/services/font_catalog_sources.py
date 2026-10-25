"""Download official Fontsource packages as bounded, integrity-checked data only."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import tarfile
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx

from yleum_api.core.errors import ApiError

_LIMIT = 16 * 1024 * 1024
_LICENSES = {"OFL-1.1", "Apache-2.0", "Ubuntu-font-1.0"}


def family_id(value: str) -> str:
    value = value.strip()
    if "://" in value:
        try:
            url = urlsplit(value)
            valid = url.scheme == "https" and not url.username and not url.password and not url.port
        except ValueError:
            valid = False
        if not valid:
            raise ApiError("validation_failed", "Нужна ссылка на Google Fonts или Fontsource", 422)
        if url.hostname == "fonts.google.com" and url.path.startswith("/specimen/"):
            value = unquote(url.path.removeprefix("/specimen/")).rstrip("/").replace("+", " ")
        elif url.hostname == "fontsource.org" and url.path.startswith("/fonts/"):
            value = unquote(url.path.removeprefix("/fonts/")).rstrip("/")
        else:
            raise ApiError("validation_failed", "Нужна ссылка на Google Fonts или Fontsource", 422)
    slug = re.sub(r"\s+", "-", value.lower())
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug) or len(slug) > 100:
        raise ApiError(
            "validation_failed", "Укажите точное название семейства или загрузите файл", 422
        )
    return slug


def _download(client: httpx.Client, url: str, limit: int) -> bytes:
    with client.stream("GET", url) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_bytes():
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError("font package size limit")
        return bytes(data)


def fetch_family(value: str, version: str | None = None) -> dict[str, Any]:
    """No user URL/redirect/package hooks; return a concrete pinned version."""
    slug = family_id(value)
    try:
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            suffix = version or "latest"
            if not re.fullmatch(r"latest|\d+\.\d+\.\d+", suffix):
                raise ValueError("invalid font version")
            metadata = json.loads(
                _download(
                    client, f"https://registry.npmjs.org/@fontsource/{slug}/{suffix}", 256 * 1024
                )
            )
            pinned = metadata["version"]
            package_name = f"@fontsource/{slug}"
            if (
                metadata["name"] != package_name
                or metadata.get("scripts")
                or metadata.get("dependencies")
                or metadata.get("license") not in _LICENSES
                or not re.fullmatch(r"\d+\.\d+\.\d+", pinned)
            ):
                raise ValueError("unsupported font package")
            expected_url = f"https://registry.npmjs.org/@fontsource/{slug}/-/{slug}-{pinned}.tgz"
            if metadata["dist"]["tarball"] != expected_url:
                raise ValueError("untrusted archive destination")
            integrity = metadata["dist"]["integrity"]
            raw = _download(client, expected_url, _LIMIT)
        if integrity != "sha512-" + base64.b64encode(hashlib.sha512(raw).digest()).decode("ascii"):
            raise ValueError("font package integrity")
        entries: dict[str, bytes] = {}
        total = 0
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            for count, entry in enumerate(archive):
                total += entry.size
                if count >= 5000 or total > 128 * 1024 * 1024 or entry.size > 4 * 1024 * 1024:
                    raise ValueError("archive budget")
                if (
                    not entry.isfile()
                    or not entry.name.startswith("package/")
                    or ".." in entry.name.split("/")
                ):
                    raise ValueError("invalid archive entry")
                path = entry.name.removeprefix("package/")
                if path in entries:
                    raise ValueError("duplicate archive entry")
                # Extract into memory only; tar paths never reach a filesystem.
                stream = archive.extractfile(entry)
                if stream is None:
                    raise ValueError("missing archive entry")
                entries[path] = stream.read(entry.size + 1)
        info = json.loads(entries["metadata.json"])
        if family_id(info["family"]) != slug:
            raise ValueError("family metadata mismatch")
        license_text = entries["LICENSE"].decode("utf-8")
        if len(license_text) > 30000:
            raise ValueError("license size")
        weights = [w for w in (400, 500, 600, 700) if w in info["weights"]]
        if not weights:
            raise ValueError("no regular face")
        faces = []
        for weight in weights:
            for subset in ("cyrillic-ext", "cyrillic", "latin-ext", "latin"):
                filename = f"{slug}-{subset}-{weight}-normal.woff2"
                css_path = f"{subset}-{weight}.css"
                if f"files/{filename}" not in entries or css_path not in entries:
                    continue
                css = entries[f"{weight}.css"].decode("utf-8")
                blocks = re.findall(r"@font-face\s*\{([^}]+)\}", css)
                css = next(block for block in blocks if filename in block)
                match = re.search(r"unicode-range:\s*([Uu+0-9A-Fa-f?,\- ]+);", css)
                if not match:
                    raise ValueError("missing subset range")
                faces.append(
                    {
                        "filename": filename,
                        "css_path": css_path,
                        "raw": entries[f"files/{filename}"],
                        "weight": weight,
                        "style": "normal",
                        "unicode_range": match[1],
                    }
                )
        if not faces or len(faces) > 16:
            raise ValueError("font face budget")
        return {
            "family": info["family"],
            "package": package_name,
            "version": pinned,
            "integrity": integrity,
            "license": metadata["license"],
            "license_text": license_text,
            "weights": weights,
            "faces": faces,
        }
    except (
        httpx.HTTPError,
        KeyError,
        ValueError,
        TypeError,
        StopIteration,
        tarfile.TarError,
        UnicodeError,
    ):
        raise ApiError(
            "font_unavailable",
            "Бесплатный шрифт не найден или временно недоступен. "
            "Проверьте название либо загрузите файл шрифта.",
            422,
        ) from None
