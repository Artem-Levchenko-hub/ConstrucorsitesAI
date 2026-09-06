"""Owner metadata overlay for the trusted MAX core, never project code or SQL."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

from docker.errors import NotFound  # type: ignore[import-untyped]

from omnia_orchestrator.core.cell_resources import CellResourceError


def config_source(config: dict[str, Any]) -> str:
    # JSON is data, not interpolated expressions. No user-supplied paths/commands.
    return "export const omniaMaxConfig = " + json.dumps(config, ensure_ascii=True) + ";\n"


def _core_file_matches(core: Any, name: str, expected: bytes) -> bool:
    """Read only one trusted file; no process/heap is allocated inside the core."""
    try:
        chunks, _stat = core.get_archive("/app/" + name)
    except NotFound:
        return False
    archive = io.BytesIO()
    try:
        for chunk in chunks:
            # A single file needs only a small tar header/padding allowance.
            if archive.tell() + len(chunk) > len(expected) + 65536:
                return False
            archive.write(chunk)
    finally:
        close = getattr(chunks, "close", None)
        if close:
            close()
    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:") as contents:
        members = contents.getmembers()
        if len(members) != 1 or not members[0].isfile():
            raise CellResourceError("unsafe trusted MAX core file")
        source = contents.extractfile(members[0])
        return source is not None and source.read(len(expected) + 1) == expected


def _upload_changed_files(core: Any, files: dict[str, bytes], error: str) -> None:
    changed = {name: data for name, data in files.items()
               if not _core_file_matches(core, name, data)}
    if not changed:
        return
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as output:
        for name, data in changed.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o644, 1000, 1000
            output.addfile(entry, io.BytesIO(data))
    if core.put_archive("/app", archive.getvalue()) is False:
        raise CellResourceError(error)


def is_compiled_core(core: Any) -> bool:
    labels = getattr(core, "attrs", {}).get("Config", {}).get("Labels") or {}
    return labels.get("omnia.max-core.protocol") == "1"


def _write_compiled_config(core: Any, config: dict[str, Any]) -> None:
    data = json.dumps(config, ensure_ascii=True).encode()
    if _core_file_matches(core, "omnia-business-config.json", data):
        return
    _upload_changed_files(
        core, {".omnia-business-config.next.json": data}, "MAX configuration upload failed",
    )
    # Workspace operation lock serializes writers. Readers see the old complete
    # JSON or the new complete JSON, never an in-place partially extracted file.
    result = core.exec_run([
        "mv", "-f", "--", "/app/.omnia-business-config.next.json",
        "/app/omnia-business-config.json",
    ], user="1000:1000")
    if result.exit_code != 0:
        raise CellResourceError("MAX configuration atomic replacement failed")


def apply_core_config(core: Any, address: str, config: dict[str, Any]) -> None:
    import http.client

    files = {
        "src/lib/omnia/max-config.ts": config_source(config),
        # This is the separate trusted core, not the generated app layout.
        # Public legal pages need no MAX login/provider or product JS bundle.
        "src/app/layout.tsx": (
            "export default function Layout({ children }: { children: React.ReactNode }) {"
            'return <html lang="ru"><body style={{margin:0,fontFamily:"system-ui",'
            'color:"#17202a",background:"#fff"}}>{children}</body></html>; }\n'
        ),
        "src/app/api/omnia/config/route.ts": (
            'import { NextResponse } from "next/server";\n'
            'import { omniaMaxConfig } from "@/lib/omnia/max-config";\n'
            'export const dynamic = "force-dynamic";\n'
            "export function GET() { return NextResponse.json(omniaMaxConfig, "
            '{ headers: { "Cache-Control": "no-store" } }); }\n'
        ),
    }
    if is_compiled_core(core):
        _write_compiled_config(core, config)
    else:
        _upload_changed_files(
            core, {name: source.encode() for name, source in files.items()},
            "MAX configuration upload failed",
        )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        connection = http.client.HTTPConnection(address, 3000, timeout=5)
        try:
            connection.request("GET", "/api/omnia/config")
            response = connection.getresponse()
            body = response.read(1024 * 1024)
            if response.status == 200 and json.loads(body) == config:
                for path in ("/support", "/legal/privacy", "/legal/terms"):
                    connection.close()
                    connection = http.client.HTTPConnection(address, 3000, timeout=15)
                    connection.request("GET", path)
                    page = connection.getresponse()
                    page.read(1024 * 1024)
                    if page.status != 200:
                        break
                else:
                    return
        except (OSError, ValueError, http.client.HTTPException):
            pass
        finally:
            connection.close()
        time.sleep(0.5)
    raise CellResourceError("MAX configuration readback was not confirmed")


def boundary_source() -> str:
    return Path(__file__).with_name("machine_boundary.py").read_text(encoding="utf-8")


def apply_public_core_overlay(core: Any) -> None:
    """Deliver trusted current auth-facing code even when the pinned kit is older."""
    if is_compiled_core(core):
        # Current trusted routes are baked into its immutable image, not HMR.
        return
    from omnia_orchestrator.core.stack_registry import get_stack
    from omnia_orchestrator.services.provisioner import _template_source_dir

    template = _template_source_dir(get_stack("max-miniapp-nextjs").template_dir)
    paths = (
        "src/app/api/max/webhook/route.ts",
        "src/app/api/omnia/integrations/[...path]/route.ts",
        "src/app/api/max/session/route.ts",
        "src/lib/max/session.ts",
    )
    _upload_changed_files(
        core, {relative: (template / relative).read_bytes() for relative in paths},
        "public MAX core update failed",
    )
