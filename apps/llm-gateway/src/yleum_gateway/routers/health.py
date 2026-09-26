"""Проба состояния: отвечает правду, а не «всё хорошо» по умолчанию.

Шлюз без базы не может записать списание: вызов модели пройдёт, деньги
потратятся, а ответ будет выброшен. Такое состояние обязано быть видно снаружи —
и человеку, и автоматической проверке. Один раз оно стоило десяти часов
оплаченных и потерянных ответов, потому что health отвечал 200.
"""

from fastapi import APIRouter, Response

from yleum_gateway.core import db

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(response: Response) -> dict[str, str]:
    # Проба заодно пробует поднять пул: если доступ к базе появился позже старта,
    # шлюз вылечится сам, без пересоздания контейнера человеком.
    pool = await db.try_init_pool()
    if pool is None:
        response.status_code = 503
        return {"status": "degraded", "reason": "database is unreachable"}
    return {"status": "ok"}
