"""Alembic must preserve application logging in a process that imports the API first."""

import json
import os
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]


def test_migration_environment_preserves_existing_provider_logging() -> None:
    # Isolate fileConfig's global handler changes from pytest's own logging state.
    # Offline base loads the real Alembic environment without needing a database.
    script = """
import io
import json
import logging
import sys
from pathlib import Path

import httpx
from alembic import command
from alembic.config import Config
from yleum_api.routers import integration_runtime

root = Path(sys.argv[1])
config = Config(str(root / "alembic.ini"), output_buffer=io.StringIO())
config.set_main_option("script_location", str(root / "migrations"))
command.upgrade(config, "base", sql=True)

logger = logging.getLogger(integration_runtime.__name__)
messages = io.StringIO()
handler = logging.StreamHandler(messages)
logger.addHandler(handler)
logger.setLevel(logging.WARNING)
integration_runtime._provider_failure("amoCRM", httpx.Response(400, json={}))
print(json.dumps({"disabled": logger.disabled, "messages": messages.getvalue()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(API_ROOT)],
        cwd=API_ROOT,
        env={
            **os.environ,
            "DATABASE_URL": "postgresql+asyncpg://test:test@127.0.0.1:1/migration_logging",
            "JWT_SECRET": "migration-logging-test-only-secret",
            "PYTHONPATH": str(API_ROOT / "src"),
        },
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    observation = json.loads(result.stdout)

    assert observation["disabled"] is False
    assert "provider=amoCRM" in observation["messages"]
    assert "status=400" in observation["messages"]
