"""Старый путь задачи превью: `omnia_api.workers.preview.render_preview`.

RQ резолвит задачу по строке пути, и задачи, поставленные до переименования,
приходят именно сюда. Функция та же самая, просто под прежним адресом.
"""

from __future__ import annotations

from yleum_api.workers.preview import render_preview

__all__ = ["render_preview"]
