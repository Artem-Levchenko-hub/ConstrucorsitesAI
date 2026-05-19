from typing import Literal

from pydantic import BaseModel

Provider = Literal["anthropic", "openai", "yandex", "alibaba", "sber"]
Recommended = Literal["fast", "quality", "budget"]


class ModelInfo(BaseModel):
    id: str
    display_name: str
    provider: Provider
    price_rub_per_1k_in: float
    price_rub_per_1k_out: float
    context_window: int
    recommended_for: list[Recommended] = []
    available: bool = True
