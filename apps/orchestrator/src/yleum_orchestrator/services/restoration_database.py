"""Controller-only administrative SQL against a project PostgreSQL container."""

from __future__ import annotations

import re
import socket
from typing import Any

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.project_machine import machine_remaining_seconds


class ControllerDatabaseError(CellResourceError):
    """Safe diagnostics only; SQL/error rows never cross the controller boundary."""

    def __init__(self, *, sqlstate: str | None, terminated: bool) -> None:
        self.sqlstate = sqlstate if sqlstate and re.fullmatch(r"[0-9A-Z]{5}", sqlstate) else None
        self.terminated = terminated
        super().__init__("controller database operation failed")


def admin_args(backend: Any) -> tuple[list[str], dict[str, str]]:
    return (
        ["-h", "127.0.0.1", "-U", "postgres", "-d", "postgres"],
        {"PGPASSWORD": backend.project_postgres_password},
    )


def admin_sql(
    backend: Any, sql: str, *, max_bytes: int = 4 * 1024 * 1024,
    lifetime_seconds: int | None = None,
) -> bytes:
    postgres = backend._project_postgres()
    if postgres is None:
        raise CellResourceError("project database is not running")
    args, env = admin_args(backend)
    command = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1",
               "-v", "VERBOSITY=sqlstate", *args]
    if lifetime_seconds is not None:
        lifetime = min(lifetime_seconds, int(machine_remaining_seconds(lifetime_seconds)))
        if lifetime < 1:
            raise CellResourceError("controller database execution budget exhausted")
        # Closing a Docker attach socket does not terminate psql. Kill the
        # client inside the container so it cannot submit a later COMMIT.
        command = ["timeout", "-s", "KILL", str(lifetime), *command]
    execution = backend.client.api.exec_create(
        postgres.id,
        command,
        stdin=True,
        environment=env,
    )
    connection = backend.client.api.exec_start(execution["Id"], socket=True)
    error_states: list[str] = []
    try:
        connection._sock.settimeout(machine_remaining_seconds(60))
        connection._sock.sendall(sql.encode())
        connection._sock.shutdown(socket.SHUT_WR)
        output = read_controller_output(
            connection, max_bytes=max_bytes, error_states=error_states,
        )
    finally:
        close_controller_socket(connection)
    outcome = backend.client.api.exec_inspect(execution["Id"])
    if outcome.get("Running") or outcome.get("ExitCode") != 0:
        # PostgreSQL errors can echo SQL, credentials or row contents.
        raise ControllerDatabaseError(
            sqlstate=error_states[-1] if error_states else None,
            # psql's ON_ERROR_STOP exit 3 establishes an ended SQL script.
            # Signal exits, missing receipts and running processes are unknown.
            terminated=outcome.get("Running") is False and outcome.get("ExitCode") == 3,
        )
    return output


def close_controller_socket(connection: Any) -> None:
    """Close Docker's owning HTTP response before its borrowed raw socket."""
    response = getattr(connection, "_response", None)
    try:
        if response is not None:
            response.close()
    finally:
        connection.close()


def read_controller_output(
    connection: Any, *, max_bytes: int, error_states: list[str] | None = None,
) -> bytes:
    """Bound Docker stdout while reading, including a truncated or oversized frame."""
    output, pending, errors = bytearray(), bytearray(), bytearray()
    while chunk := connection._sock.recv(65536):
        pending.extend(chunk)
        while len(pending) >= 8:
            size = int.from_bytes(pending[4:8], "big")
            if size > max_bytes or pending[0] not in {1, 2}:
                raise CellResourceError("database output budget exceeded or invalid")
            if len(pending) < size + 8:
                break
            if pending[0] == 1:
                if len(output) + size > max_bytes:
                    raise CellResourceError("database output budget exceeded")
                output.extend(pending[8 : size + 8])
            elif error_states is not None:
                if len(errors) + size > max_bytes:
                    raise CellResourceError("database diagnostic budget exceeded")
                errors.extend(pending[8 : size + 8])
            del pending[: size + 8]
    if pending:
        raise CellResourceError("incomplete controller database output")
    if error_states is not None:
        # VERBOSITY=sqlstate emits only a code. Ignore all other stderr lines,
        # including malicious notices, SQL context, credential/row contents.
        error_states.extend(
            match.decode("ascii") for match in re.findall(
                rb"(?m)^(?:ERROR|FATAL):\s+([0-9A-Z]{5})\s*$", bytes(errors),
            )
        )
    return bytes(output)
