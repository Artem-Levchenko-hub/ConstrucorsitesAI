"""Controller-only administrative SQL against a project PostgreSQL container."""

from __future__ import annotations

import socket
from typing import Any

from yleum_orchestrator.core.cell_resources import CellResourceError
from yleum_orchestrator.services.project_machine import machine_remaining_seconds


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
    command = ["psql", "-X", "-qAt", "-v", "ON_ERROR_STOP=1", *args]
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
    try:
        connection._sock.settimeout(machine_remaining_seconds(60))
        connection._sock.sendall(sql.encode())
        connection._sock.shutdown(socket.SHUT_WR)
        output = read_controller_output(connection, max_bytes=max_bytes)
    finally:
        close_controller_socket(connection)
    outcome = backend.client.api.exec_inspect(execution["Id"])
    if outcome.get("Running") or outcome.get("ExitCode") != 0:
        # PostgreSQL errors can echo SQL, credentials or row contents.
        raise CellResourceError("controller database operation failed")
    return output


def close_controller_socket(connection: Any) -> None:
    """Close Docker's owning HTTP response before its borrowed raw socket."""
    response = getattr(connection, "_response", None)
    try:
        if response is not None:
            response.close()
    finally:
        connection.close()


def read_controller_output(connection: Any, *, max_bytes: int) -> bytes:
    """Bound Docker stdout while reading, including a truncated or oversized frame."""
    output, pending = bytearray(), bytearray()
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
            del pending[: size + 8]
    if pending:
        raise CellResourceError("incomplete controller database output")
    return bytes(output)
