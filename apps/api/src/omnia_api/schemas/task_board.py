from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

TaskBoardStatus = Literal["backlog", "in_progress", "review", "done"]
TaskBoardAssignee = Literal["alexey", "alexey_jr", "artem", "roman"]
TaskBoardPriority = Literal["low", "medium", "high"]


class TaskBoardTaskCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    status: TaskBoardStatus = "backlog"
    assignee: TaskBoardAssignee
    priority: TaskBoardPriority = "medium"


class TaskBoardTaskUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    status: TaskBoardStatus | None = None
    assignee: TaskBoardAssignee | None = None
    priority: TaskBoardPriority | None = None


class TaskBoardTaskPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str
    status: TaskBoardStatus
    assignee: TaskBoardAssignee
    priority: TaskBoardPriority
    position: int
    created_at: datetime
    updated_at: datetime
