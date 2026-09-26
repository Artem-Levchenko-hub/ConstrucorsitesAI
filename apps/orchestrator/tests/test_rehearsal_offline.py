"""Репетиция проверки, прогнанная офлайн против эталонной реализации.

Третья и последняя преграда адаптивного отката — репетиция: шесть шагов по HTTP
против поднятого кандидата с настоящей базой. До сих пор её можно было увидеть
только живым прогоном на проде: час времени, деньги на модель и один факт за
попытку.

Здесь она прогоняется целиком за секунды. Подменены ровно три вещи: транспорт
(ASGI вместо сети), читатель базы и контекст кандидата. Сам проверяющий код —
настоящий, тот же, что судит живой прогон.

Ценность двойная. Во-первых, видно, выполним ли контракт вообще: если эталонная
реализация проходит все шесть шагов, значит текст требований не противоречив.
Во-вторых, каждую беду теперь можно воспроизвести нарочно и увидеть, что скажет
отказ, — не дожидаясь, пока в неё упрётся живой прогон.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from yleum_orchestrator.schemas.restoration_adaptation_activation import (
    ActivationBusinessProbe,
    ActivationBusinessWitness,
)
from yleum_orchestrator.services.restoration_adaptation_health import (
    DockerRestorationAdaptationHealthProber,
    ProbeRehearsalFailure,
)

from tests._probe_reference_app import ProbeReferenceApp
from tests._versioning_pg import pg  # noqa: F401

def _text(value: object) -> str:
    """Обвязка отдаёт вывод psql байтами — приводим к строке в одном месте."""
    return value.decode() if isinstance(value, bytes) else str(value)


_SECRET = "0123456789abcdef0123456789abcdef"
_ENDPOINT = "/api/restoration-probe"
_SCHEMA = """
CREATE TABLE public.max_users (max_user_id text PRIMARY KEY, display_name text);
CREATE TABLE public.leads (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
  max_user_id text NOT NULL,
  name text NOT NULL,
  phone text NOT NULL,
  note text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  status text NOT NULL DEFAULT 'новая'
);
INSERT INTO public.max_users VALUES ('owner-1', 'Владелец');
"""


def _witness() -> ActivationBusinessWitness:
    return ActivationBusinessWitness(
        entity="leads",
        id_column="id",
        owner_column="max_user_id",
        value_column="note",
        create_values={"name": "Проверка", "phone": "+70000000000", "status": "новая"},
    )


def _probe(witness: ActivationBusinessWitness) -> ActivationBusinessProbe:
    """Собрать проверочную точку тем же кодом, что судит живой прогон.

    Отпечаток контракта считается внутри проверки, выдумать его нельзя — и это
    правильно: стенд должен идти тем же путём, что прод, иначе он проверяет себя.
    """
    from yleum_orchestrator.services.restoration_adaptation_probe import validate_probe_contract

    manifest = {
        "version": 1,
        "endpoint": _ENDPOINT,
        "max_payload_bytes": 4096,
        "witnesses": [witness.model_dump(mode="json")],
    }
    contract = {
        "version": 1,
        "tables": [
            {
                "name": "leads",
                "columns": [
                    {"name": "id", "type": "uuid", "nullable": False, "default": "gen_random_uuid()"},
                    {"name": "max_user_id", "type": "text", "nullable": False},
                    {"name": "name", "type": "text", "nullable": False},
                    {"name": "phone", "type": "text", "nullable": False},
                    {"name": "note", "type": "text", "nullable": True},
                    {"name": "status", "type": "text", "nullable": False, "default": "'новая'::text"},
                ],
                "owner_column": "max_user_id",
                "primary_key": ["id"],
            }
        ],
    }
    return validate_probe_contract(
        {".omnia/restoration-probe.json": json.dumps(manifest, ensure_ascii=False)}, contract
    )


@pytest.fixture
def rehearsal(pg, monkeypatch: pytest.MonkeyPatch):  # noqa: F811
    """Собрать репетицию так, чтобы она била по эталонной точке, а не по контейнеру."""
    from yleum_orchestrator.services import restoration_adaptation_health as health

    pg.run(_SCHEMA)
    witness = _witness()
    probe = _probe(witness)
    app = ProbeReferenceApp(
        sql=pg.run,
        secret=_SECRET,
        endpoint=_ENDPOINT,
        contract_digest=probe.contract_digest,
        witness=witness,
    )

    def client_factory(**kwargs):
        kwargs.pop("transport", None)
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), **kwargs)

    async def database_reader(_backend, w, item_id):
        out = _text(
            pg.run(
                f"SELECT {w.owner_column} || '|' || {w.value_column} FROM public.{w.entity} "
                f"WHERE {w.id_column}='{item_id}';"
            )
        ).strip()
        if not out:
            return None
        owner, _, value = out.partition("|")
        return {"id": item_id, "ownerId": owner, "value": value}

    monkeypatch.setattr(
        health, "live_database_volume_identity_digest", lambda _b, *, expected_volume: "v" * 64
    )
    prober = DockerRestorationAdaptationHealthProber(
        code_engine=object(),
        client_factory=client_factory,
        database_reader=database_reader,
    )
    workspace_id = uuid.uuid4()
    backend = type(
        "B", (), {"workspace_volume": "code-vol", "project_postgres_volume": "db-vol"}
    )()
    state = type(
        "S",
        (),
        {
            "workspace_id": workspace_id,
            "project_id": uuid.uuid4(),
            "owner_id": "owner-1",
            "fencing_epoch": 1,
            "active_generation_run_id": None,
            "active_generation_fencing_epoch": 1,
        },
    )()
    manager = type(
        "M",
        (),
        {
            "machine_runtime": type(
                "R",
                (),
                {"parts": lambda _s, _st: (object(), backend), "secret": lambda _s, _w: _SECRET},
            )()
        },
    )()

    async def run() -> str:
        run_id = uuid.uuid4()
        state.active_generation_run_id = run_id
        return await prober.rehearse_candidate(
            operation_id=uuid.uuid4(),
            generation_run_id=run_id,
            project_id=state.project_id,
            owner_id="owner-1",
            activation_id=uuid.uuid4(),
            candidate_workspace_id=workspace_id,
            candidate_fencing_epoch=1,
            business_probe=probe,
            manager=manager,
            state=state,
            backend=backend,
            candidate_source_manifest_digest="m" * 64,
        )

    return run, pg, app


async def test_the_reference_implementation_passes_every_leg(rehearsal) -> None:
    """Главное: контракт выполним, и вот код, который его выполняет."""
    run, _pg, _app = rehearsal

    digest = await run()

    assert isinstance(digest, str) and len(digest) == 64


async def test_the_rehearsal_leaves_the_table_as_it_found_it(rehearsal) -> None:
    """Репетиция обязана убирать за собой — иначе она сама ломает сверку копии."""
    run, pg, _app = rehearsal
    before = _text(pg.run("SELECT count(*) FROM public.leads;")).strip()

    await run()

    assert _text(pg.run("SELECT count(*) FROM public.leads;")).strip() == before


async def test_a_taken_key_answered_with_a_server_error_names_the_cross_owner_leg(
    rehearsal,
) -> None:
    """Самая вероятная ошибка агента: не предусмотреть занятый ключ.

    Репетиция от имени чужого пытается создать запись с уже существующим ключом.
    Приложение, которое просто вставляет строку, упадёт на нарушении уникальности
    и ответит ошибкой сервера — а репетиция ждёт осознанный отказ. Здесь
    закреплено, какой именно шаг назовёт отказ, чтобы по живому прогону это
    читалось сразу.
    """
    run, _pg, app = rehearsal
    app._exists = lambda _item_id: False  # приложение «забыло» про занятый ключ

    with pytest.raises(ProbeRehearsalFailure) as failure:
        await run()

    assert failure.value.leg == "cross_owner_denial"


async def test_answering_a_missing_row_with_403_breaks_the_owner_leg_first(rehearsal) -> None:
    """Вторая вероятная ошибка: отвечать «нет доступа» там, где надо «не найдено».

    Я ожидал, что это упрётся в отказ чужому, а стенд показал точнее: ломается
    РАНЬШЕ, на собственной ноге владельца. Причина в том, что репетиция после
    удаления перечитывает запись и требует именно «не найдено» — то есть код
    ответа проверяется не только на чужом доступе.

    Разница не косметическая: «нет доступа» подтверждает существование записи,
    то есть выдаёт факт чужих данных самим ответом. Здесь закреплено, на каком
    шаге это всплывёт, чтобы по живому прогону читалось сразу.
    """
    run, _pg, app = rehearsal
    original_reply = app._reply

    async def reply(send, status, payload):
        if status == 404 and payload.get("error") == "not found":
            status = 403
        return await original_reply(send, status, payload)

    app._reply = reply

    with pytest.raises(ProbeRehearsalFailure) as failure:
        await run()

    assert failure.value.leg == "signed_owner_mutation"
