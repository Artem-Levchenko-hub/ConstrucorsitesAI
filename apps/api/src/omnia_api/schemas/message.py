from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SelectedElement(BaseModel):
    """Элемент, выделенный пользователем в превью (select-mode), с комментарием.

    Фронт собирает их через element-picker и шлёт в `PromptRequest`; мы
    сохраняем их на user-сообщении (для истории) и вставляем в промпт, чтобы
    модель меняла именно эти блоки. Это недоверенный ввод, попадающий в
    LLM-контекст, поэтому каждое поле жёстко ограничено по длине (R-10
    fail-fast на границе) — иначе огромный `outerHTML` раздул бы промпт.
    """

    selector: str = Field(min_length=1, max_length=600)
    label: str | None = Field(default=None, max_length=200)
    html: str | None = Field(default=None, max_length=2000)
    text: str | None = Field(default=None, max_length=300)
    comment: str | None = Field(default=None, max_length=1000)


class MessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    project_id: UUID
    snapshot_id: UUID | None = None
    role: Literal["user", "assistant", "system"]
    content: str
    model_id: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    selected_elements: list[SelectedElement] | None = None
    created_at: datetime


class PromptRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10_000)
    model_id: str = Field(min_length=1)
    selected_elements: list[SelectedElement] | None = Field(default=None, max_length=12)


class PromptResponse(BaseModel):
    message_id: UUID
    snapshot_id: UUID | None = None
