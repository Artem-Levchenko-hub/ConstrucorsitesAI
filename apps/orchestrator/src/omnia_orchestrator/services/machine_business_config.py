"""Owner metadata overlay for the trusted MAX core, never project code or SQL."""

from __future__ import annotations

import io
import json
import tarfile
import time
from pathlib import Path
from typing import Any

from omnia_orchestrator.core.cell_resources import CellResourceError


def config_source(config: dict[str, Any]) -> str:
    # JSON is data, not interpolated expressions. No user-supplied paths/commands.
    return "export const omniaMaxConfig = " + json.dumps(config, ensure_ascii=True) + ";\n"


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
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as output:
        for name, source in files.items():
            data = source.encode()
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o644, 1000, 1000
            output.addfile(entry, io.BytesIO(data))
    if core.put_archive("/app", archive.getvalue()) is False:
        raise CellResourceError("MAX configuration upload failed")
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
