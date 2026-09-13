"""Safe terminal infrastructure failures that source editing cannot repair."""

from __future__ import annotations

from uuid import UUID

from omnia_api.services.orchestrator_client import OrchestratorBadRequest

PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED = "protected_environment_recovery_required"


class ProjectCellInfrastructureError(RuntimeError):
    """Stop generation without exposing controller diagnostics to the model."""

    def __init__(self, code: str, operation_id: UUID | None = None) -> None:
        self.code = code
        self.operation_id = operation_id
        super().__init__(code + (f" (operation_id={operation_id})" if operation_id else ""))


def terminal_cell_error(
    error: Exception,
    *,
    operation_id: UUID | None = None,
) -> ProjectCellInfrastructureError | None:
    if isinstance(error, ProjectCellInfrastructureError):
        return error
    if (
        isinstance(error, OrchestratorBadRequest)
        and error.upstream_code == PROTECTED_ENVIRONMENT_RECOVERY_REQUIRED
    ):
        return ProjectCellInfrastructureError(error.upstream_code, operation_id)
    return None


def raise_if_terminal_cell_error(error: Exception, *, operation_id: UUID | None = None) -> None:
    terminal = terminal_cell_error(error, operation_id=operation_id)
    if terminal is not None:
        raise terminal from None
