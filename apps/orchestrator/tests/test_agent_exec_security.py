from omnia_orchestrator.routers.runtime import (
    _command_exposes_environment,
    _redact_exec_output,
)


def test_blocks_environment_enumeration_commands() -> None:
    assert _command_exposes_environment("env")
    assert _command_exposes_environment("printenv | sort")
    assert _command_exposes_environment("node -e 'console.log(process.env)'")
    assert _command_exposes_environment("python -c 'import os; print(os.getenv(\"TOKEN\"))'")
    assert _command_exposes_environment("echo $DATABASE_URL")
    assert _command_exposes_environment("cat /proc/1/environ")
    assert _command_exposes_environment("cat .env")
    assert _command_exposes_environment("grep TOKEN config/.env.production")
    assert not _command_exposes_environment("pnpm build")
    assert not _command_exposes_environment("export NODE_ENV=test && pnpm test")


def test_redacts_secret_assignments_and_dsn_passwords() -> None:
    result = _redact_exec_output(
        "AUTH_SECRET=hidden\n"
        "DATABASE_URL=postgresql://app:password@postgres:5432/app\n"
        "Authorization: Bearer hidden-bearer\n"
        "sk-exampleexampleexampleexample\n"
        "build clean"
    )

    assert "hidden" not in result
    assert "password" not in result
    assert "sk-example" not in result
    assert result.endswith("build clean")
