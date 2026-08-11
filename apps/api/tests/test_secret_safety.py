from __future__ import annotations

import inspect

import pytest

from omnia_api.routers import messages
from omnia_api.services.secret_safety import (
    contains_provider_secret,
    is_secret_file,
    max_model_write_rejection,
    prepare_safe_max_prompt,
    redact_provider_secrets,
)


def test_secret_detector_is_high_confidence() -> None:
    assert contains_provider_secret("sk-" + "a" * 24)
    assert contains_provider_secret("AIza" + "a" * 32)
    assert not contains_provider_secret("Use process.env.DEEPSEEK_API_KEY")
    assert not contains_provider_secret("подключи API-ключ через интеграции")
    raw = "подключи " + "sk-" + "a" * 24
    assert redact_provider_secrets(raw) == "подключи [CREDENTIAL REDACTED]"


def test_secret_paste_keeps_ai_native_max_request_buildable() -> None:
    secret = "sk-" + "z" * 24
    prepared = prepare_safe_max_prompt(f"Собери AI-тренера внутри MAX, используй {secret}")

    assert prepared.credential_removed is True
    assert secret not in prepared.chat_text
    assert secret not in prepared.model_text
    assert "[CREDENTIAL REDACTED]" in prepared.chat_text
    assert "requestOmniaAI" in prepared.model_text
    assert "не имитируй AI" in prepared.model_text


def test_max_prompt_handler_no_longer_redirects_secret_pastes_to_dead_end() -> None:
    source = inspect.getsource(messages.post_prompt)

    assert "prepare_safe_max_prompt" in source
    assert source.index("prepare_safe_max_prompt") < source.index("reserve_generation_run")
    assert "credential_redirect" not in source
    assert "Ключ не сохранён и не передан агенту" not in source


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
