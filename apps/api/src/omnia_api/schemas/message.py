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
    # Deprecated / ignored: the server orchestrates per-role models (no user
    # model picker). Kept optional so a stale frontend that still sends it
    # doesn't 422. Honoured only as an admin override via env OMNIA_FORCE_MODEL.
    model_id: str | None = Field(default=None)
    selected_elements: list[SelectedElement] | None = Field(default=None, max_length=12)
    # The onboarding quiz collects the brief client-side on the first prompt, so
    # the server-side clarify interview must NOT also fire — otherwise it would
    # ask questions again instead of building the already-enriched prompt. The
    # quiz (and the "just generate" skip) send skip_clarify=true. Optional →
    # legacy clients keep the server clarify behaviour.
    skip_clarify: bool = Field(default=False)


class ClientErrorReport(BaseModel):
    """An uncaught JS error / unhandled rejection observed in the live preview,
    reported by the inspector → workspace shell. Untrusted browser input headed
    into the chat, so every field is length-clamped (R-10 fail-fast at the edge).
    """

    message: str = Field(min_length=1, max_length=2000)
    source: str = Field(default="", max_length=2000)
    line: int = Field(default=0, ge=0)
    col: int = Field(default=0, ge=0)
    stack: str = Field(default="", max_length=4000)


class PromptResponse(BaseModel):
    message_id: UUID
    snapshot_id: UUID | None = None
    # How the server will handle this turn, so the workspace can set the right
    # expectation immediately (before any WS event):
    #   "build"   — full (re)generation of the page (first prompt / rebuild)
    #   "edit"    — surgical, scoped change (cheap, preserves the rest of the page)
    #   "clarify" — no generation this turn; the server is asking questions first
    mode: Literal["build", "edit", "clarify"] = "build"
