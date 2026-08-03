from __future__ import annotations

import pytest

from omnia_api.services.secret_safety import (
    contains_provider_secret,
    is_secret_file,
    max_model_write_rejection,
    redact_provider_secrets,
)


def test_secret_detector_is_high_confidence() -> None:
    assert contains_provider_secret("sk-" + "a" * 24)
    assert contains_provider_secret("AIza" + "a" * 32)
    assert not contains_provider_secret("Use process.env.DEEPSEEK_API_KEY")
    assert not contains_provider_secret("подключи API-ключ через интеграции")
    raw = "подключи " + "sk-" + "a" * 24
    assert redact_provider_secrets(raw) == "подключи [CREDENTIAL REDACTED]"


def test_max_writer_blocks_secret_files_and_literals() -> None:
    assert is_secret_file(".env.local")
    assert is_secret_file("config/secrets.json")
    assert not is_secret_file("src/components/EnvironmentBadge.tsx")
    assert ".env" in str(max_model_write_rejection(".env.local", "VALUE=hidden"))
    assert "blocked" in str(
        max_model_write_rejection("src/lib/provider.ts", 'const key = "sk-' + "b" * 24 + '"')
    )
    assert (
        max_model_write_rejection("src/app/page.tsx", "export default function Page() {}") is None
    )


@pytest.mark.parametrize(
    "source",
    [
        "const token = process.env.MAX_BOT_TOKEN;",
        'const secret = process.env["AUTH_SECRET"];',
        'import fs from "node:fs";',
        'import { exec } from "child_process";',
        'import { db } from "../../../lib/db";',
        'const pg = require("pg");',
    ],
)
def test_max_writer_rejects_runtime_secret_and_privileged_access(source: str) -> None:
    assert max_model_write_rejection("src/app/page.tsx", source)


@pytest.mark.parametrize(
    "source",
    [
        '"use server";\nexport async function mutate() {}',
        'async function mutate() {\n  "use server";\n}',
        '"use server" /* harmless */; export async function mutate() {}',
        '"use server"; export async function mutate() {}',
        'import { cookies } from "next/headers";',
        'import "server-only";',
        'import { verifyMaxInitData } from "@/lib/max/validate-init-data";',
    ],
)
def test_max_writer_rejects_server_execution_escape_hatches(source: str) -> None:
    assert max_model_write_rejection("src/components/product/Action.tsx", source)
