"""Identity of an independent executor, including its capacity-side effects."""

from contextvars import ContextVar
from uuid import UUID

execution_run_id: ContextVar[UUID | None] = ContextVar("execution_run_id", default=None)
