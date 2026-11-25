from dataclasses import dataclass

# SQL fragments below are static. The router binds periods and all scope values.
_WINDOW = "BETWEEN :start AND :end"
_RUNS = """
SELECT r.*, p.owner_id AS client_id, h.kind AS task_type
FROM generation_runs r
JOIN projects p ON p.id = r.project_id
LEFT JOIN generation_credit_reservations h ON h.run_id = r.id
WHERE (:client IS NULL OR p.owner_id = :client)
  AND (:project IS NULL OR r.project_id = :project)
"""
_CALLS = """
SELECT c.*, coalesce(p.owner_id, c.user_id) AS client_id,
       coalesce(c.tokens_in, 0) + coalesce(c.tokens_out, 0) AS tokens,
       coalesce(c.provider_cost_rub, c.calculated_cost_rub) AS rub
FROM provider_calls c
LEFT JOIN projects p ON p.id = c.project_id
WHERE c.status <> 'started'
  AND c.error_type IS DISTINCT FROM 'duplicate_provider_receipt'
  AND (:client IS NULL OR coalesce(p.owner_id, c.user_id) = :client)
  AND (:project IS NULL OR c.project_id = :project)
"""
_VISITS = """
SELECT a.*, p.owner_id AS client_id, p.name AS project_name
FROM max_analytics_events a
JOIN projects p ON p.id = a.project_id
WHERE a.kind = 'open'
  AND (:client IS NULL OR p.owner_id = :client)
  AND (:project IS NULL OR a.project_id = :project)
"""
_CURRENT = "NULL::timestamptz"
_PENDING = "status IN ('pending', 'queued_for_capacity')"
_FINISHED = "status IN ('completed', 'failed', 'cancelled')"
_MODEL = "provider_scope || ' / ' || coalesce(actual_model, 'модель неизвестна')"
_DAY = "date_trunc('day', created_at, 'UTC')"
_CALL_DAY = "date_trunc('day', finished_at, 'UTC')"
_HOUR = "date_trunc('hour', finished_at, 'UTC')"
_UNITS = "coalesce(task_type, 'тип неизвестен')"


@dataclass(frozen=True, slots=True)
class DatabaseMetric:
    sql: str
    source: str
    note: str | None = None


def _measure(
    source: str,
    value: str,
    *,
    label: str = "'Всего'",
    at: str = _CURRENT,
    condition: str = "true",
    note: str | None = None,
    extra: str = "",
) -> DatabaseMetric:
    # Returning a NULL for an empty cohort lets the UI distinguish it from a measured zero.
    sql = f"""
        WITH facts AS ({_INPUTS[source]})
        SELECT {label} AS series, {at} AS at, ({value})::double precision AS value,
               count(*) AS samples{extra}
        FROM facts
        WHERE {condition}
        GROUP BY 1, 2
        ORDER BY at DESC NULLS LAST, series
        LIMIT 750
    """
    return DatabaseMetric(sql, source, note)


def _percentile(expression: str, quantile: float) -> str:
    return f"percentile_cont({quantile}) WITHIN GROUP (ORDER BY {expression})"


def _distribution(
    source: str,
    expression: str,
    *,
    condition: str,
    label: str = "'Всего'",
    at: str = _CURRENT,
    note: str | None = None,
) -> DatabaseMetric:
    return _measure(
        source,
        _percentile(expression, 0.5),
        label=label,
        at=at,
        condition=condition,
        note=note,
        extra=f", ({_percentile(expression, 0.95)})::double precision AS p95",
    )


_COMPLETION = f"finished_at {_WINDOW}"
_ACCEPTANCE = f"created_at {_WINDOW}"
_CALL_TIME = f"finished_at {_WINDOW}"
_RUN_TIME = "extract(epoch FROM finished_at - created_at)"
_CALL_DURATION = "extract(epoch FROM finished_at - created_at) * 1000"
_QUEUE_WAIT = "extract(epoch FROM execution_started_at - created_at)"
_TERMINAL_NOTE = "История строится по сохранённым терминальным событиям задач."
_TASK_KIND_NOTE = "Разбивка по типу доступна для задач с сохранённым типом при принятии."
_POOL_NOTE = "Группировка по пулу выполнения. Для исторических задач VPS может быть неизвестна."
_COST_NOTE = "Приоритет у стоимости провайдера в рублях, затем у расчётной суммы."
_LATENCY_NOTE = "Границы измерения определены временем записей журнала провайдера."


# Client VPS here means an assigned BYO deployment target with a verified identity.
_TARGETS = """
SELECT DISTINCT d.id, d.label
FROM deploy_targets d
JOIN projects p ON p.deploy_target_id = d.id
WHERE d.verify_status = 'ok' AND p.owner_id = d.owner_id
  AND (:vps IS NULL OR d.id = :vps)
  AND (:client IS NULL OR d.owner_id = :client)
  AND (:project IS NULL OR p.id = :project)
"""
_PHASES = f"""
SELECT phase.key AS stage, phase.value::double precision / 1000 AS seconds, r.finished_at
FROM ({_RUNS}) r
CROSS JOIN LATERAL jsonb_each_text(
    CASE WHEN jsonb_typeof(r.agent_state->'max_finalization'->'phase_ms') = 'object'
         THEN r.agent_state->'max_finalization'->'phase_ms'
         ELSE '{{}}'::jsonb END
) phase
WHERE phase.value ~ '^[0-9]+$'
"""
_RUN_TOTALS = f"""
SELECT r.id, r.status, r.task_type, r.finished_at,
       count(c.id) AS calls, sum(c.rub) AS rub,
       sum(c.tokens) FILTER (WHERE c.tokens_in IS NOT NULL OR c.tokens_out IS NOT NULL) AS tokens
FROM ({_RUNS}) r
LEFT JOIN ({_CALLS}) c ON c.run_id = r.id
WHERE r.finished_at {_WINDOW}
GROUP BY r.id, r.status, r.task_type, r.finished_at
"""
_TASK_CALLS = f"""
SELECT c.*, r.task_type, r.status AS task_status
FROM ({_CALLS}) c
JOIN ({_RUNS}) r ON r.id = c.run_id
"""
_VERSION_CHECKS = f"""
SELECT v.id, v.created_at, v.status,
       (SELECT q.outcome FROM project_cell_proofs p
        JOIN project_cell_proof_results q ON q.proof_id = p.id
        WHERE p.generation_run_id = v.generation_run_id
          AND q.dimension IN ('fast_check', 'full_build', 'runtime')
        ORDER BY q.created_at DESC, q.id DESC LIMIT 1) AS last_check
FROM project_versions v
JOIN ({_RUNS}) r ON r.id = v.generation_run_id
WHERE v.created_at {_WINDOW} AND v.status IN ('ready', 'failed')
"""
_PROVIDER_RETRIES = f"""
SELECT c.*, p.terminal_reason AS previous_reason, old_call.provider_scope AS previous_provider,
       old_call.requested_model AS previous_model
FROM ({_CALLS}) c
JOIN generation_provider_attempts a ON a.provider_call_id = c.id
JOIN generation_provider_attempts p ON p.attempt_id = a.parent_attempt_id AND p.run_id = a.run_id
LEFT JOIN provider_calls old_call ON old_call.id = p.provider_call_id
"""
_RUN_ATTEMPTS = f"""
SELECT r.id, r.status, r.finished_at, r.task_type,
       count(*) FILTER (WHERE a.parent_attempt_id IS NOT NULL) AS retries
FROM ({_RUNS}) r
JOIN generation_provider_attempts a ON a.run_id = r.id
WHERE r.finished_at {_WINDOW} AND r.{_FINISHED}
GROUP BY r.id, r.status, r.finished_at, r.task_type
"""
_PROVEN_PREVIEWS = f"""
WITH ready AS (
    SELECT r.created_at, r.task_type,
           least(
               CASE WHEN jsonb_typeof(r.agent_state->'monitoring_first_draft_preview_epoch') =
                    'number' THEN
                    (r.agent_state->>'monitoring_first_draft_preview_epoch')::double precision
               END,
               CASE WHEN s.preview_status = 'ready' AND jsonb_typeof(
                    s.preview_manifest->0->'monitoring_ready_epoch') = 'number' THEN
                    (s.preview_manifest->0->>'monitoring_ready_epoch')::double precision
               END
           ) AS ready_epoch
    FROM ({_RUNS}) r
    LEFT JOIN messages m ON m.id = r.assistant_message_id AND m.project_id = r.project_id
    LEFT JOIN snapshots s ON s.id = m.snapshot_id AND s.project_id = r.project_id
)
SELECT task_type, to_timestamp(ready_epoch) AS preview_at,
       ready_epoch - extract(epoch FROM created_at) AS seconds
FROM ready
WHERE ready_epoch BETWEEN extract(epoch FROM created_at) AND extract(epoch FROM :end)
"""
_INTEGRATION_FAILURES = """
SELECT o.provider, o.kind, o.status, o.finished_at
FROM integration_operations o
JOIN projects p ON p.id = o.project_id
WHERE o.status IN ('rejected', 'unknown')
  AND (:client IS NULL OR p.owner_id = :client)
  AND (:project IS NULL OR o.project_id = :project)
"""
_STUDIO = """
SELECT a.actor_id, a.minute, a.release, a.active, a.error,
       coalesce(u.email, a.actor_id::text) AS client
FROM studio_activity a
JOIN users u ON u.id = a.actor_id
WHERE (:client IS NULL OR a.actor_id = :client)
  AND (:project IS NULL OR a.workspace_id = :project)
"""
_STUDIO_SESSIONS = f"""
WITH activity AS ({_STUDIO}), minutes AS (
    SELECT actor_id, client, minute, bool_or(active) AS active
    FROM activity
    WHERE minute BETWEEN :start - interval '30 minutes' AND :end
    GROUP BY 1, 2, 3
), previous AS (
    SELECT *, lag(minute) OVER (PARTITION BY actor_id ORDER BY minute) AS last_minute
    FROM minutes
), sessions AS (
    SELECT *, sum(CASE WHEN last_minute IS NULL OR minute - last_minute >= interval '30 minutes'
                       THEN 1 ELSE 0 END) OVER (PARTITION BY actor_id ORDER BY minute) AS session
    FROM previous
)
SELECT actor_id, client, session, min(minute) AS began, max(minute) AS last,
       count(*) FILTER (WHERE active) AS active_minutes
FROM sessions GROUP BY 1, 2, 3
"""
_STUDIO_FIRST_SUCCESS = f"""
WITH activity AS ({_STUDIO}), firsts AS (
    SELECT actor_id, client, min(minute) AS first_seen FROM activity GROUP BY 1, 2
)
SELECT f.*, min(r.finished_at) AS first_success
FROM firsts f
JOIN ({_RUNS}) r ON r.user_id = f.actor_id
WHERE r.status = 'completed' AND r.task_type = 'initial' AND r.finished_at >= f.first_seen
GROUP BY f.actor_id, f.client, f.first_seen
"""
_STUDIO_ERRORS = f"""
SELECT s.actor_id, s.client, s.session, s.began, a.release
FROM ({_STUDIO_SESSIONS}) s
JOIN ({_STUDIO}) a ON a.actor_id = s.actor_id AND a.minute BETWEEN s.began AND s.last
WHERE a.error
GROUP BY 1, 2, 3, 4, 5
"""
_STUDIO_DAYS = f"""
SELECT DISTINCT a.actor_id, a.client, date_trunc('day', a.minute, 'UTC') AS day,
       EXISTS (SELECT 1 FROM ({_RUNS}) r WHERE r.user_id = a.actor_id
                 AND r.created_at >= date_trunc('day', a.minute, 'UTC')
                 AND r.created_at < date_trunc('day', a.minute, 'UTC') + interval '1 day'
                 AND r.created_at <= :end) AS converted
FROM ({_STUDIO}) a WHERE a.minute {_WINDOW}
"""
_STUDIO_AI = f"""
WITH active AS (
  SELECT DISTINCT actor_id, client, minute FROM ({_STUDIO}) s WHERE active AND minute {_WINDOW}
), spend AS (
  SELECT user_id, date_trunc('minute', finished_at, 'UTC') AS minute, sum(tokens) AS tokens
  FROM ({_CALLS}) c
  WHERE {_CALL_TIME} AND (tokens_in IS NOT NULL OR tokens_out IS NOT NULL)
  GROUP BY 1, 2
)
SELECT a.actor_id, a.client, date_trunc('day', a.minute, 'UTC') AS day, count(*) AS minutes,
       sum(coalesce(s.tokens, 0)) AS tokens
FROM active a LEFT JOIN spend s ON s.user_id = a.actor_id AND s.minute = a.minute
GROUP BY 1, 2, 3
"""
_STUDIO_SOURCE = "Активность редактора"
_SESSION_SOURCE = "Сеансы редактора"
_STUDIO_AI_SOURCE = "Активность редактора и вызовы ИИ"
_INPUTS = {
    "Клиентские цели": _TARGETS,
    "События MAX": _VISITS,
    "Запуски генерации": _RUNS,
    "Финализация MAX": _PHASES,
    "Журнал вызовов ИИ": _CALLS,
    "Вызовы ИИ и задачи": _TASK_CALLS,
    "Расход по задаче": _RUN_TOTALS,
    _STUDIO_SOURCE: _STUDIO,
    _SESSION_SOURCE: _STUDIO_SESSIONS,
    _STUDIO_AI_SOURCE: _STUDIO_AI,
    "Проверки версий MAX": _VERSION_CHECKS,
    "Попытки провайдеров": _PROVIDER_RETRIES,
    "Журнал интеграций": _INTEGRATION_FAILURES,
    "Повторы провайдеров по задачам": _RUN_ATTEMPTS,
    "Подтверждённые предпросмотры": _PROVEN_PREVIEWS,
    "Первый вход и генерация": _STUDIO_FIRST_SUCCESS,
    "Ошибки сеансов": _STUDIO_ERRORS,
    "Конверсия редактора": _STUDIO_DAYS,
    "Активные пользователи по минутам": f"""
        SELECT minute, count(DISTINCT actor_id) AS users
        FROM ({_STUDIO}) s WHERE active AND minute {_WINDOW} GROUP BY 1
    """,
}
_RUN_SOURCE = "Запуски генерации"
_CALL_SOURCE = "Журнал вызовов ИИ"
_TASK_SOURCE = "Вызовы ИИ и задачи"
_TOTAL_SOURCE = "Расход по задаче"

DATABASE_METRICS = {
    1: DatabaseMetric(
        f"SELECT 'Всего' AS series, NULL::timestamptz AS at, "
        f"count(*)::double precision AS value, count(*) AS samples FROM ({_TARGETS}) t",
        "Клиентские цели",
        "Текущий набор проверенных клиентских VPS с назначенным проектом.",
    ),
    25: DatabaseMetric(
        f"""
        WITH activity AS ({_STUDIO}), periods(period, duration) AS (
            VALUES ('День', interval '1 day'), ('Неделя', interval '7 days'),
                   ('Месяц', interval '30 days')
        )
        SELECT a.client || ' / ' || p.period AS series, NULL::timestamptz AS at,
               count(DISTINCT a.actor_id)::double precision AS value, count(*) AS samples
        FROM activity a JOIN periods p
          ON a.minute > :end - p.duration AND a.minute <= :end
        GROUP BY a.client, p.period ORDER BY a.client, p.period LIMIT 750
        """,
        _STUDIO_SOURCE,
        "Скользящие окна 1, 7 и 30 дней. Клиент соответствует владельцу аккаунта.",
    ),
    26: _measure(_SESSION_SOURCE, "count(*)", label="client", condition=f"began {_WINDOW}"),
    27: _measure(
        _SESSION_SOURCE,
        "count(*)::numeric / nullif(count(DISTINCT actor_id), 0)",
        label="client",
        condition="began BETWEEN :end - interval '7 days' AND :end",
    ),
    28: _measure(
        _SESSION_SOURCE,
        _percentile("active_minutes", 0.5),
        label="client",
        condition=f"began {_WINDOW}",
        note="Активное время оценивается по минутам с взаимодействием пользователя.",
        extra=", avg(active_minutes)::double precision AS mean",
    ),
    29: _measure(
        "Активные пользователи по минутам",
        "max(users)",
        label="'Платформа / общий пул'",
        note="Пик по совокупности активных пользователей интерфейса.",
    ),
    30: _measure(
        "Конверсия редактора",
        "100.0 * count(*) FILTER (WHERE converted) / count(*)",
        at="day",
        label="client",
    ),
    31: _measure(
        "Первый вход и генерация",
        _percentile("extract(epoch FROM first_success - first_seen) / 60", 0.5),
        label="client",
        condition=f"first_success {_WINDOW}",
        note="Первый вход учитывается с начала сбора активности.",
        extra=", avg(extract(epoch FROM first_success - first_seen) / 60)"
        "::double precision AS mean",
    ),
    32: DatabaseMetric(
        f"""
        WITH visits AS ({_VISITS}),
        periods(period, duration) AS (
          VALUES ('День', interval '1 day'), ('Неделя', interval '7 days'),
                 ('Месяц', interval '30 days')
        )
        SELECT v.project_name || ' / ' || p.period AS series, NULL::timestamptz AS at,
               count(DISTINCT v.actor_key)::double precision AS value, count(*) AS samples
        FROM visits v
        JOIN periods p ON v.occurred_at > :end - p.duration AND v.occurred_at <= :end
        GROUP BY v.project_id, v.project_name, p.period
        ORDER BY v.project_name, v.project_id, p.period LIMIT 750
        """,
        "События MAX",
        "Скользящие окна 1, 7 и 30 дней на конец выбранного периода.",
    ),
    33: DatabaseMetric(
        f"""
        WITH visits AS ({_VISITS}), ordered AS (
          SELECT *, lag(occurred_at) OVER (
            PARTITION BY project_id, actor_key ORDER BY occurred_at, id
          ) AS previous
          FROM visits WHERE occurred_at BETWEEN :start - interval '30 minutes' AND :end
        )
        SELECT project_name AS series, NULL::timestamptz AS at,
               count(*) FILTER (
                 WHERE previous IS NULL OR occurred_at - previous >= interval '30 minutes'
               )::double precision AS value, count(*) AS samples
        FROM ordered WHERE occurred_at {_WINDOW}
        GROUP BY project_id, project_name ORDER BY project_name, project_id LIMIT 750
        """,
        "События MAX",
        "Сеанс определяется по событиям открытия с паузой в 30 минут.",
    ),
    34: _measure(
        _RUN_SOURCE,
        "count(*)",
        at=_DAY,
        condition=f"{_ACCEPTANCE} AND task_type = 'initial'",
        note=_TASK_KIND_NOTE,
    ),
    35: _measure(
        _RUN_SOURCE,
        "count(*)",
        at=_DAY,
        condition=f"{_ACCEPTANCE} AND task_type = 'edit'",
        note=_TASK_KIND_NOTE,
    ),
    36: _measure(
        _RUN_SOURCE,
        "count(*)",
        label=_UNITS,
        condition=f"{_COMPLETION} AND status = 'completed'",
        note=_TASK_KIND_NOTE,
    ),
    37: _measure(
        _RUN_SOURCE,
        "count(*) FILTER (WHERE status = 'failed')",
        label="coalesce(agent_state->'max_finalization'->>'current_phase', 'этап неизвестен')",
        condition=f"{_COMPLETION} AND {_FINISHED}",
        note="Доля ошибок рассчитывается по числу завершённых задач в выборке.",
    ),
    38: _measure(
        _RUN_SOURCE,
        "count(*)",
        label="coalesce(agent_state->'max_finalization'->>'current_phase', 'этап неизвестен')",
        condition=f"{_COMPLETION} AND status = 'cancelled'",
        note=_TERMINAL_NOTE,
    ),
    39: _measure(
        _RUN_SOURCE,
        "count(*)",
        label="coalesce(agent_state->'max_finalization'->>'current_phase', 'этап неизвестен')",
        condition=f"{_COMPLETION} AND status='failed'"
        " AND error LIKE 'generation deadline exceeded;%'",
        note="Учтены зарегистрированные превышения общего окна генерации.",
    ),
    40: _measure(
        "Повторы провайдеров по задачам",
        "avg(retries)",
        label=_UNITS,
        note="Связанные автоматические повторные вызовы провайдеров по терминальным задачам.",
    ),
    41: _measure(
        "Повторы провайдеров по задачам",
        "100.0 * count(*) FILTER (WHERE status = 'completed') / count(*)",
        condition="retries > 0",
        label=_UNITS,
        note="Доля успеха среди терминальных задач со связанным повтором провайдера.",
    ),
    43: _measure(
        _RUN_SOURCE,
        "count(*)",
        label="execution_backend || ' / ' || " + _UNITS,
        condition=_PENDING,
        note=_POOL_NOTE,
    ),
    44: _distribution(
        _RUN_SOURCE,
        _QUEUE_WAIT,
        condition=f"execution_started_at {_WINDOW} AND execution_started_at >= created_at",
        label="execution_backend",
        note=_POOL_NOTE,
    ),
    45: _measure(
        _RUN_SOURCE,
        "extract(epoch FROM :end - min(created_at))",
        label="execution_backend",
        condition=_PENDING,
        note=_POOL_NOTE,
    ),
    46: _measure(
        _RUN_SOURCE,
        "count(*)",
        label="execution_backend",
        condition="status IN ('running', 'cancel_requested')",
        note=_POOL_NOTE,
    ),
    47: _measure(
        _RUN_SOURCE,
        "count(*)",
        label="execution_backend",
        at=_HOUR,
        condition=f"{_COMPLETION} AND status = 'completed'",
        note=_POOL_NOTE,
    ),
    48: _distribution(
        _RUN_SOURCE,
        _RUN_TIME,
        condition=f"{_COMPLETION} AND status = 'completed' AND task_type = 'initial'",
        note=_TASK_KIND_NOTE,
    ),
    49: _distribution(
        _RUN_SOURCE,
        _RUN_TIME,
        condition=f"{_COMPLETION} AND status = 'completed' AND task_type = 'edit'",
        note=_TASK_KIND_NOTE,
    ),
    50: _distribution(
        "Подтверждённые предпросмотры",
        "seconds",
        condition=f"preview_at {_WINDOW} AND task_type = 'initial'",
        note="От запуска до первой успешной проверки рабочего или версионного предпросмотра.",
    ),
    51: _distribution(
        "Финализация MAX",
        "seconds",
        label="stage",
        condition=_COMPLETION,
        note="Этапы с сохранённой длительностью финализации MAX.",
    ),
    52: _measure(
        "Проверки версий MAX",
        "100.0 * count(*) FILTER (WHERE status = 'failed' AND last_check = 'red') / count(*)",
        condition="last_check IS NOT NULL AND (status = 'ready' OR last_check = 'red')",
        note="Версии MAX с сохранённым итогом сборки, проверки кода или запуска.",
    ),
    57: _measure(
        _CALL_SOURCE,
        "count(*)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=_CALL_TIME,
        note="Версию можно отобразить в составе фактического идентификатора модели.",
    ),
    58: _measure(
        _TASK_SOURCE,
        "count(*)",
        label=_MODEL + " || ' / ' || stage || ' / ' || " + _UNITS,
        condition=_CALL_TIME,
        note=_TASK_KIND_NOTE,
    ),
    59: _measure(_TOTAL_SOURCE, "avg(calls)", label=_UNITS, note=_TASK_KIND_NOTE),
    60: _measure(
        "Попытки провайдеров",
        "count(*)",
        at=_CALL_DAY,
        label="coalesce(previous_reason, 'причина неизвестна') || ' / ' || " + _MODEL,
        condition=f"{_CALL_TIME} AND (previous_provider <> provider_scope "
        "OR previous_model <> requested_model)",
        note="Подтверждённые смены провайдера или модели между связанными попытками.",
    ),
    61: _measure(
        _CALL_SOURCE,
        "sum(tokens_in)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND tokens_in IS NOT NULL",
    ),
    62: _measure(
        _CALL_SOURCE,
        "sum(tokens_out)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND tokens_out IS NOT NULL",
    ),
    64: _measure(
        _CALL_SOURCE,
        "sum(cache_read_tokens)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND cache_read_tokens IS NOT NULL",
    ),
    65: _measure(
        _CALL_SOURCE,
        "sum(cache_write_tokens)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND cache_write_tokens IS NOT NULL",
    ),
    66: _measure(
        _CALL_SOURCE,
        "100.0 * sum(cache_read_tokens) / nullif(sum(tokens_in), 0)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND tokens_in >= cache_read_tokens",
    ),
    67: _measure(
        _TASK_SOURCE,
        "sum(tokens)",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND task_type='initial' AND tokens_in IS NOT NULL",
    ),
    68: _measure(
        _TASK_SOURCE,
        "sum(tokens)",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND task_type='edit' AND tokens_in IS NOT NULL",
    ),
    69: _distribution(
        _TOTAL_SOURCE,
        "tokens",
        condition="status='completed' AND task_type='initial' AND tokens IS NOT NULL",
    ),
    70: _distribution(
        _TOTAL_SOURCE,
        "tokens",
        condition="status='completed' AND task_type='edit' AND tokens IS NOT NULL",
    ),
    71: _measure(
        _TASK_SOURCE,
        "sum(tokens)",
        label="stage",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND tokens_in IS NOT NULL",
    ),
    72: _measure(
        _TASK_SOURCE,
        "sum(tokens)",
        label="task_status",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND task_status IN ('failed','cancelled') "
        "AND tokens_in IS NOT NULL",
    ),
    73: _measure(
        "Попытки провайдеров",
        "sum(tokens)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND (tokens_in IS NOT NULL OR tokens_out IS NOT NULL)",
        note="Расход повторов, связанных с исходным вызовом в журнале попыток.",
    ),
    74: _measure(_STUDIO_AI_SOURCE, "avg(tokens)", at="day", label="client"),
    75: _distribution(
        _STUDIO_AI_SOURCE,
        "tokens * 60.0 / minutes",
        condition="minutes > 0",
    ),
    76: DatabaseMetric(
        f"""
        WITH facts AS ({_CALLS}), minute_totals AS (
          SELECT {_MODEL} AS series, client_id,
                 date_trunc('minute', finished_at, 'UTC') AS minute, sum(tokens) AS amount
          FROM facts WHERE {_CALL_TIME} AND tokens_in IS NOT NULL
          GROUP BY 1, 2, 3
        )
        SELECT series, NULL::timestamptz AS at, max(amount)::double precision AS value,
               count(*) AS samples
        FROM minute_totals GROUP BY series ORDER BY series LIMIT 750
        """,
        _CALL_SOURCE,
        "Пик рассчитан по минуте завершения вызовов, когда поступают итоговые токены.",
    ),
    81: _distribution(
        _CALL_SOURCE, _CALL_DURATION, label=_MODEL, condition=_CALL_TIME, note=_LATENCY_NOTE
    ),
    82: _measure(
        _CALL_SOURCE,
        "count(*)",
        label="provider_scope",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND retry_after_seconds IS NOT NULL",
        note="Отказы по лимиту, подтверждённые квитанцией и Retry-After.",
    ),
    83: _measure(
        _CALL_SOURCE,
        "count(*)",
        label="provider_scope || ' / ' || error_type",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND error_type IN ('ReadTimeout','ConnectTimeout','TimeoutError')",
        note="В журнале выделены тайм-ауты. Коды 5xx пока не хранятся отдельно.",
    ),
    86: _measure(
        _CALL_SOURCE,
        "sum(rub)",
        label=_MODEL,
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND rub IS NOT NULL",
        note=_COST_NOTE,
    ),
    87: _measure(
        _TOTAL_SOURCE,
        "avg(rub)",
        condition="status='completed' AND task_type='initial' AND rub IS NOT NULL",
        note=_COST_NOTE,
    ),
    88: _measure(
        _TOTAL_SOURCE,
        "avg(rub)",
        condition="status='completed' AND task_type='edit' AND rub IS NOT NULL",
        note=_COST_NOTE,
    ),
    89: _measure(
        _TASK_SOURCE,
        "sum(rub)",
        label="task_status",
        at=_CALL_DAY,
        condition=f"{_CALL_TIME} AND task_status IN ('failed','cancelled') AND rub IS NOT NULL",
        note=_COST_NOTE,
    ),
    94: _measure(
        "Ошибки сеансов",
        "count(*)",
        label="release",
        condition=f"began {_WINDOW}",
    ),
    96: DatabaseMetric(
        """
        SELECT 'PostgreSQL' AS series, NULL::timestamptz AS at,
               (100.0 * count(*) / nullif(current_setting('max_connections')::int
                  - current_setting('superuser_reserved_connections')::int
                  - coalesce(current_setting('reserved_connections', true)::int, 0), 0)
               )::double precision AS value, count(*) AS samples
        FROM pg_stat_activity WHERE backend_type = 'client backend'
        """,
        "PostgreSQL",
        "Включены открытые клиентские соединения всех БД экземпляра, в том числе простаивающие.",
    ),
    97: _measure(
        "Журнал интеграций",
        "count(*)",
        label="provider || ' / ' || kind || ' / ' || status",
        at=_CALL_DAY,
        condition=_COMPLETION,
        note="Отклонённые операции и операции с неизвестным результатом по журналу записей.",
    ),
}
