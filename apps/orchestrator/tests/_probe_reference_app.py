"""Эталонная проверочная точка: то, что адаптированное приложение обязано реализовать.

Требования к ней живут в задании агента текстом. Пока текст не проверен
работающей реализацией, он остаётся обещанием: непонятно, выполним ли контракт
вообще и что именно ждёт репетиция. Здесь контракт выполнен минимальным кодом
поверх настоящей базы, чтобы репетицию можно было прогнать без контейнеров,
без ключа провайдера и без живого прогона.
"""

from __future__ import annotations

import base64
import hmac
import json
from typing import Any
from urllib.parse import parse_qs

_SESSION_COOKIE = "__Host-max_session"


def _text(value: object) -> str:
    """psql отдаёт вывод байтами; приложение работает со строками."""
    return value.decode() if isinstance(value, bytes) else str(value)


def _actor(cookie_header: str, secret: str) -> str | None:
    """Кто пришёл: тот же разбор подписи, что кладёт репетиция."""
    for chunk in cookie_header.split(";"):
        name, _, value = chunk.strip().partition("=")
        if name != _SESSION_COOKIE or not value:
            continue
        encoded, _, signature = value.partition(".")
        if not encoded or not signature:
            return None
        expected = (
            base64.urlsafe_b64encode(hmac.digest(secret.encode(), encoded.encode(), "sha256"))
            .rstrip(b"=")
            .decode()
        )
        if not hmac.compare_digest(signature, expected):
            return None
        padded = encoded + "=" * (-len(encoded) % 4)
        try:
            payload = json.loads(base64.urlsafe_b64decode(padded))
        except (ValueError, TypeError):
            return None
        actor = payload.get("id")
        return actor if isinstance(actor, str) else None
    return None


class ProbeReferenceApp:
    """Минимальное приложение, выполняющее контракт проверочной точки."""

    def __init__(
        self,
        *,
        sql: Any,
        secret: str,
        endpoint: str,
        contract_digest: str,
        witness: Any,
    ) -> None:
        self._sql = sql
        self._secret = secret
        self._endpoint = endpoint.rstrip("/")
        self._digest = contract_digest
        self._witness = witness

    # --- работа с настоящей таблицей владельца -------------------------------

    def _insert(self, item_id: str, owner: str, value: str, values: dict[str, Any]) -> None:
        columns = [self._witness.id_column, self._witness.owner_column, self._witness.value_column]
        literals = [f"'{item_id}'", f"'{owner}'", f"'{value}'"]
        for name, raw in values.items():
            columns.append(name)
            literals.append("'" + str(raw).replace("'", "''") + "'")
        self._sql(
            f"INSERT INTO public.{self._witness.entity} "
            f"({', '.join(columns)}) VALUES ({', '.join(literals)});"
        )

    def _exists(self, item_id: str) -> bool:
        out = _text(
            self._sql(
                f"SELECT 1 FROM public.{self._witness.entity} "
                f"WHERE {self._witness.id_column}='{item_id}';"
            )
        ).strip()
        return bool(out)

    def _select(self, item_id: str, owner: str) -> tuple[str, str] | None:
        out = self._sql(
            f"SELECT {self._witness.value_column} FROM public.{self._witness.entity} "
            f"WHERE {self._witness.id_column}='{item_id}' "
            f"AND {self._witness.owner_column}='{owner}';"
        )
        out = _text(out).strip()
        return (item_id, out) if out else None

    def _by_marker(self, owner: str, marker: str, limit: int) -> list[tuple[str, str]]:
        out = self._sql(
            f"SELECT {self._witness.id_column} || '|' || {self._witness.value_column} "
            f"FROM public.{self._witness.entity} "
            f"WHERE {self._witness.owner_column}='{owner}' "
            f"AND {self._witness.value_column} LIKE '{marker}:%' "
            f"ORDER BY {self._witness.id_column} LIMIT {limit};"
        )
        rows = []
        for line in _text(out).splitlines():
            line = line.strip()
            if line:
                item_id, _, value = line.partition("|")
                rows.append((item_id, value))
        return rows

    def _item(self, owner: str, item_id: str, value: str) -> dict[str, object]:
        marker, _, phase = value.rpartition(":")
        return {
            "id": item_id,
            "ownerId": owner,
            "entity": self._witness.entity,
            "marker": marker,
            "phase": phase,
        }

    # --- ASGI ----------------------------------------------------------------

    async def __call__(self, scope, receive, send) -> None:
        assert scope["type"] == "http"
        path = scope["path"]
        method = scope["method"]
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        body = b""
        while True:
            message = await receive()
            body += message.get("body", b"")
            if not message.get("more_body"):
                break

        if path == "/api/omnia/health":
            return await self._reply(send, 200, {"status": "ok", "platform": "max-miniapp"})

        if not path.startswith(self._endpoint):
            return await self._reply(send, 404, {"error": "not found"})

        actor = _actor(headers.get("cookie", ""), self._secret)
        if actor is None:
            return await self._reply(send, 401, {"error": "unauthorized"})

        tail = path[len(self._endpoint) :].strip("/")
        parts = tail.split("/") if tail else []

        if method == "GET" and not parts:
            query = parse_qs(scope.get("query_string", b"").decode())
            marker = (query.get("marker") or [""])[0]
            limit = int((query.get("limit") or ["32"])[0])
            rows = self._by_marker(actor, marker, limit) if marker else []
            items = [self._item(actor, item_id, value) for item_id, value in rows]
            return await self._reply(
                send,
                200,
                {"probeContractDigest": self._digest, "items": items, "complete": True},
            )

        if method == "POST" and not parts:
            payload = json.loads(body or b"{}")
            # Ключ уже занят — возможно, чужой записью. Создавать нельзя и
            # подменять владельца нельзя: репетиция проверяет именно это.
            if self._exists(payload["id"]):
                return await self._reply(send, 409, {"error": "conflict"})
            value = f"{payload['marker']}:{payload['phase']}"
            self._insert(payload["id"], actor, value, dict(payload.get("values") or {}))
            return await self._reply(
                send,
                201,
                {
                    "probeContractDigest": self._digest,
                    "item": self._item(actor, payload["id"], value),
                },
            )

        if len(parts) == 2:
            _entity, item_id = parts
            found = self._select(item_id, actor)
            if method == "GET":
                if found is None:
                    return await self._reply(send, 404, {"error": "not found"})
                return await self._reply(
                    send,
                    200,
                    {
                        "probeContractDigest": self._digest,
                        "item": self._item(actor, item_id, found[1]),
                    },
                )
            if method == "PATCH":
                if found is None:
                    return await self._reply(send, 404, {"error": "not found"})
                payload = json.loads(body or b"{}")
                marker = found[1].rpartition(":")[0]
                value = f"{marker}:{payload['phase']}"
                self._sql(
                    f"UPDATE public.{self._witness.entity} "
                    f"SET {self._witness.value_column}='{value}' "
                    f"WHERE {self._witness.id_column}='{item_id}' "
                    f"AND {self._witness.owner_column}='{actor}';"
                )
                return await self._reply(
                    send,
                    200,
                    {
                        "probeContractDigest": self._digest,
                        "item": self._item(actor, item_id, value),
                    },
                )
            if method == "DELETE":
                if found is None:
                    return await self._reply(send, 404, {"error": "not found"})
                self._sql(
                    f"DELETE FROM public.{self._witness.entity} "
                    f"WHERE {self._witness.id_column}='{item_id}' "
                    f"AND {self._witness.owner_column}='{actor}';"
                )
                return await self._reply(
                    send,
                    200,
                    {"probeContractDigest": self._digest, "deleted": True, "id": item_id},
                )

        return await self._reply(send, 404, {"error": "not found"})

    @staticmethod
    async def _reply(send, status: int, payload: dict[str, object]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(raw)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": raw})
