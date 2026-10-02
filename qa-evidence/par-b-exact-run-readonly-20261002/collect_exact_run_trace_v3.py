"""Core API-container only: exact owned run, SQL-projected metadata, no raw transcript."""
import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

PROJECT = '0949c826-14b0-41cf-8e93-14551bc348d0'
RUN = 'b5633285-2500-470c-818b-b79ce5e6f7e3'
ASSISTANT = '1475ecc7-d16d-41f0-a3e5-9d999684cf7e'
START = '2026-10-02T12:51:13.131783+00:00'
FINISH = '2026-10-02T12:53:03.865326+00:00'
LIMIT = 400
TOOLS = ('list_dir','read_file','grep','docs','provider_docs','write_file','edit_file',
         'build','bash','read_logs','runtime_check','generate_media','probe',
         'verify_isolation','done','provider_response')
KINDS = ('step','escalate','stalled','retry','done')
REASONS = ('output_limit','invalid_tool_arguments','missing_tool_arguments',
           'source_repair','no_progress','exploring','max_steps','error','provider_stopped')
EVENTS = ('agent.step','agent.done','llm.done','llm.delta','generation.started',
          'generation.failed','generation.completed','generation.cancelled')

BOUND = """WITH bound AS (
 SELECT r.id,r.project_id,r.assistant_message_id,r.status,r.execution_backend,
        r.response_mode,r.started_at,r.finished_at,r.agent_state,r.error,
        m.agent_steps,m.snapshot_id
 FROM generation_runs r
 JOIN projects p ON p.id=r.project_id AND p.owner_id=r.user_id
 JOIN messages m ON m.id=r.assistant_message_id AND m.project_id=r.project_id
 WHERE r.id=CAST(:run AS uuid) AND r.project_id=CAST(:project AS uuid)
   AND r.assistant_message_id=CAST(:assistant AS uuid) AND m.role='assistant'
   AND r.started_at=CAST(:start AS timestamptz)
   AND r.finished_at=CAST(:finish AS timestamptz)
) """

def enum_sql(expr, allowed):
    # Only static source constants become SQL syntax; all scope values are bound.
    values = ','.join("'" + item + "'" for item in allowed)
    return f"CASE WHEN {expr} IN ({values}) THEN {expr} ELSE NULL END"

def failure_sql(expr):
    return f"""CASE
 WHEN {expr}='edit produced no source changes' THEN 'edit produced no source changes'
 WHEN {expr}='generation_executor_interrupted' THEN 'generation_executor_interrupted'
 WHEN {expr}='build finished without a committed snapshot' THEN 'build finished without a committed snapshot'
 WHEN {expr} IS NULL OR {expr}='' THEN NULL
 ELSE 'UNRECOGNIZED_FAILURE_WITHHELD' END"""

def code_sql(expr):
    return f"""CASE
 WHEN lower({expr}) LIKE '%provider_auth_failed%' OR lower({expr}) LIKE '%payment_required%'
   OR lower({expr}) LIKE '%insufficient balance%' OR lower({expr}) LIKE '%wallet_empty%' THEN 'provider_access'
 WHEN lower({expr}) LIKE '%provider_unavailable%' THEN 'provider_unavailable'
 WHEN lower({expr}) LIKE '%deadline%' OR lower({expr}) LIKE '%timeout%' THEN 'deadline'
 WHEN lower({expr}) LIKE '%no source changes%' THEN 'no_changes'
 WHEN lower({expr}) LIKE '%migration%' OR lower({expr}) LIKE '%database%'
   OR lower({expr}) LIKE '%preservation%' THEN 'data_contract'
 WHEN {expr} IS NOT NULL AND {expr}<>'' THEN 'verification' ELSE NULL END"""

def path_sql(expr):
    # Source-relative path only. No command/URL/absolute/secret path output.
    return f"""CASE WHEN {expr} ~ '^(src|app|apps|components|lib|pages|public|styles|tests)/[A-Za-z0-9_./@+ -]{{1,150}}$'
 AND {expr} !~ '(^|/)\\.\\.(/|$)' AND {expr} !~ '[A-Za-z0-9_-]{{48}}'
 AND lower({expr}) !~ '(secret|password|credential|private.key|(^|/)\\.env|token)'
 THEN {expr} ELSE NULL END"""

def step_projection(x):
    return ','.join([
        enum_sql(f"{x}->>'kind'", KINDS)+' AS kind',
        enum_sql(f"{x}->>'tool'", TOOLS)+' AS tool',
        path_sql(f"{x}->>'path'")+' AS path',
        f"CASE WHEN jsonb_typeof({x}->'ok')='boolean' THEN ({x}->>'ok')::boolean END AS ok",
        f"CASE WHEN {x}->>'step' ~ '^[0-9]{{1,5}}$' THEN ({x}->>'step')::integer END AS step",
        enum_sql(f"{x}->>'reason'", REASONS)+' AS reason',
        f"CASE WHEN {x}->>'recovery_attempt' IN ('1','2','3') THEN ({x}->>'recovery_attempt')::integer END AS recovery_attempt",
        code_sql(f"{x}->>'detail'")+' AS diagnostic_code',
        f"CASE WHEN coalesce({x}->>'detail','')='' THEN false ELSE true END AS detail_present",
    ])

RUN_SQL = BOUND + "SELECT " + ','.join([
    enum_sql('status', ('pending','queued_for_capacity','running','cancel_requested','cancelled','completed','failed'))+' AS status',
    enum_sql('execution_backend', ('api','worker'))+' AS execution_backend',
    enum_sql('response_mode', ('build','chat','edit'))+' AS response_mode',
    'started_at,finished_at,snapshot_id IS NOT NULL AS has_message_snapshot',
    failure_sql('error')+' AS exact_allowlisted_failure', code_sql('error')+' AS public_failure_code',
    enum_sql("agent_state->'product_outcome'->>'status'", ('failed','completed'))+' AS product_outcome_status',
    failure_sql("agent_state->'product_outcome'->>'error'")+' AS product_outcome_exact_failure',
    code_sql("agent_state->'product_outcome'->>'error'")+' AS product_outcome_failure_code',
    "CASE WHEN agent_state->>'runtime_revision' ~ '^[0-9a-f]{40}$' THEN agent_state->>'runtime_revision' END AS runtime_revision",
    "CASE WHEN jsonb_typeof(agent_steps)='array' THEN jsonb_array_length(agent_steps) ELSE 0 END AS assistant_steps_count",
    "CASE WHEN jsonb_typeof(agent_state->'changed_files')='array' THEN jsonb_array_length(agent_state->'changed_files') ELSE 0 END AS recorded_changed_files_count",
]) + ' FROM bound'
EVENT_WHERE = " FROM bound b JOIN generation_events e ON e.generation_run_id=b.id AND e.project_id=b.project_id AND e.message_id=b.assistant_message_id "
EVENT_COUNT_SQL = BOUND + 'SELECT count(*) AS event_count'+EVENT_WHERE
EVENT_SQL = BOUND + 'SELECT e.seq,e.created_at,' + enum_sql('e.event_type', EVENTS) + ' AS event_type,' + step_projection('e.payload') + EVENT_WHERE + ' ORDER BY e.seq LIMIT 400'
STEPS_SQL = BOUND + 'SELECT s.ordinality AS ordinal,' + step_projection('s.item') + " FROM bound b CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(b.agent_steps)='array' THEN b.agent_steps ELSE '[]'::jsonb END) WITH ORDINALITY s(item,ordinality) ORDER BY s.ordinality LIMIT 400"
FILES_SQL = BOUND + 'SELECT s.ordinality AS ordinal,' + path_sql("s.item#>>'{}'") + " AS path FROM bound b CROSS JOIN LATERAL jsonb_array_elements(CASE WHEN jsonb_typeof(b.agent_state->'changed_files')='array' THEN b.agent_state->'changed_files' ELSE '[]'::jsonb END) WITH ORDINALITY s(item,ordinality) ORDER BY s.ordinality LIMIT 400"
SELECTS = (RUN_SQL, EVENT_COUNT_SQL, EVENT_SQL, STEPS_SQL, FILES_SQL)
PARAMS = dict(project=PROJECT,run=RUN,assistant=ASSISTANT,start=datetime.fromisoformat(START),finish=datetime.fromisoformat(FINISH))

def prepared():
    return {'phase':'prepared-only','mutations':0,'scope':{'project':PROJECT,'run':RUN,'assistant':ASSISTANT},'limit_per_trace':LIMIT}

async def collect(expected_release):
    # No application imports, environment/credential reads or connections in default mode.
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.config import get_settings
    from yleum_api.core.db import get_engine, dispose_engine
    import yleum_api.services.generation.agent_generation as generation_source
    release = get_settings().omnia_release_sha
    if release != expected_release:
        raise RuntimeError('exact_release_mismatch')
    source = Path(generation_source.__file__).read_bytes()
    if b'raise RuntimeError("edit produced no source changes")' not in source:
        raise RuntimeError('expected_no_source_change_guard_absent')
    out = {'phase':'collected','release':release,'scope':prepared()['scope'],
           'transaction_read_only':True,'mutations':0,
           'agent_generation_source_sha256':hashlib.sha256(source).hexdigest(),
           'known_no_source_change_guard_present':True,
           'limits':{'events':LIMIT,'assistant_steps':LIMIT,'changed_files':LIMIT},
           'raw_content_exported':False}
    try:
        async with async_sessionmaker(get_engine(),autoflush=False)() as session:
            try:
                await session.execute(text('SET TRANSACTION READ ONLY'))
                await session.execute(text("SET LOCAL statement_timeout='5000'"))
                run_rows = (await session.execute(text(RUN_SQL),PARAMS)).mappings().all()
                if len(run_rows) != 1:
                    raise RuntimeError('exact_owned_terminal_scope_not_found')
                out['run_metadata'] = dict(run_rows[0])
                out['event_count'] = (await session.execute(text(EVENT_COUNT_SQL),PARAMS)).scalar_one()
                for key,sql in (('events',EVENT_SQL),('assistant_steps',STEPS_SQL),('recorded_changed_files',FILES_SQL)):
                    out[key] = [dict(row) for row in (await session.execute(text(sql),PARAMS)).mappings()]
                out['truncated'] = {
                    'events':out['event_count']>LIMIT,
                    'assistant_steps':out['run_metadata']['assistant_steps_count']>LIMIT,
                    'recorded_changed_files':out['run_metadata']['recorded_changed_files_count']>LIMIT,
                }
            finally:
                await session.rollback()
    finally:
        await dispose_engine()
    return out

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--expected-release')
    args = p.parse_args()
    if not args.execute:
        print(json.dumps(prepared()))
        return
    if not args.expected_release or not re.fullmatch('[0-9a-f]{40}',args.expected_release):
        raise SystemExit('full_expected_release_required')
    try:
        result = asyncio.run(collect(args.expected_release))
    except Exception as exc:
        # Database/driver exception strings can contain SQL/connection details.
        safe = str(exc) if type(exc) is RuntimeError and str(exc) in {
            'exact_release_mismatch','expected_no_source_change_guard_absent',
            'exact_owned_terminal_scope_not_found'} else 'COLLECTOR_ERROR_DETAILS_WITHHELD'
        print(json.dumps({'phase':'FAILED','error_code':safe,'error_type':type(exc).__name__,'mutations':0}))
        raise SystemExit(1)
    print(json.dumps(result,default=lambda value:value.isoformat()))

if __name__ == '__main__':
    main()
