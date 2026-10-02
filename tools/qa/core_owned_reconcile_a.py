"""Explicit OWN-A recovery using supported services; no SQL terminalization."""
import argparse
import asyncio
import json
from uuid import UUID

W = UUID('55bdcfc4-e011-44f0-9b79-1a9f7acaa9c8')
A = UUID('dfd1b513-bbe8-5804-9a5c-dbbf00b34f5f')
O = UUID('b466bc13-e77d-47a3-b6dd-7fbd2191abf6')

async def main(args):
    from sqlalchemy import select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from yleum_api.core.db import get_engine, dispose_engine
    from yleum_api.models.project import Project
    from yleum_api.models.generation_run import GenerationRun
    from yleum_api.models.project_cell import ProjectCellWorkspace, ProjectCellOperation, ProjectCellActivityLease
    from yleum_api.services.orchestrator_client import HttpProjectCellOrchestratorClient
    from yleum_api.services.project_cell_activity import reconcile_activity
    from yleum_api.services.project_cell_lifecycle import reconcile_indeterminate_cell_operation
    from yleum_api.services.project_cells import reserve_cell_operation
    project, owner, run = UUID(args.project), UUID(args.owner), UUID(args.run)
    factory = async_sessionmaker(get_engine(), expire_on_commit=False)
    client = HttpProjectCellOrchestratorClient()

    async def guard(session):
        await session.execute(text('SELECT pg_advisory_xact_lock(hashtext(:id))'), {'id': str(W)})
        w = await session.scalar(select(ProjectCellWorkspace).where(ProjectCellWorkspace.id == W).with_for_update())
        p, r = await session.get(Project, project), await session.get(GenerationRun, run)
        if (w is None or p is None or r is None or w.project_id != project or w.owner_id != owner
            or p.owner_id != owner or r.project_id != project or r.user_id != owner
            or r.status != 'cancelled' or w.generation_run_id != run
            or w.orchestrator != 'commerce' or w.fencing_epoch != 2):
            raise PermissionError('Exact OWN-A context changed')
        a, o = await session.get(ProjectCellActivityLease, A), await session.get(ProjectCellOperation, O)
        if (a is None or o is None or a.workspace_id != W or a.generation_run_id != run
            or o.workspace_id != W or o.generation_run_id != run or o.kind != 'release'
            or o.status != 'indeterminate' or o.fencing_epoch != 2):
            raise PermissionError('Exact OWN-A unresolved receipts changed')
        competing = await session.scalar(select(ProjectCellOperation.id).where(
            ProjectCellOperation.workspace_id == W,
            ProjectCellOperation.status.in_(['pending','running','waiting_capacity'])).limit(1))
        if competing:
            raise PermissionError('Competing OWN-A lifecycle operation')
        return a, o

    async def terminal_status(operation_id):
        if operation_id != A:
            raise PermissionError('Wrong activity')
        s = await client.agent_operation_status(W, A)
        t = s.terminal_response
        if (s.operation_id != A or s.state not in {'completed','failed','timed_out','cancelled'}
            or t is None or t.operation_id != A or t.before_identity is None or t.after_identity is None):
            raise PermissionError('No exact observed terminal command receipt')
        return s

    try:
        if args.action == 'activity':
            async with factory() as session:
                a, _ = await guard(session)
                if a.state != 'active' or a.finished_at is not None:
                    raise PermissionError('Activity is already terminal; do not retry')
                await session.rollback()
            await terminal_status(A)
            status = await reconcile_activity(session_factory=factory, workspace_id=W,
                operation_id=A, poll_status=terminal_status, cancellation_requested=False)
            print(json.dumps({'activity_operation_id': str(A), 'observed_state': status.state,
                              'supported_reconciliation_completed': True}))
        else:
            # No command termination, destroy, ensure, or speculative timeout is issued.
            await terminal_status(A)
            async with factory() as session:
                a, _ = await guard(session)
                if a.state == 'active' or a.finished_at is None:
                    raise PermissionError('Activity must first settle from its terminal journal')
                other_lease = await session.scalar(select(ProjectCellActivityLease.operation_id).where(
                    ProjectCellActivityLease.workspace_id == W,
                    ProjectCellActivityLease.finished_at.is_(None)).limit(1))
                if other_lease:
                    raise PermissionError('Another unfinished OWN-A activity')
                key = 'qa-owned-a-observed-release-reconcile:' + str(O) + ':1'
                existing = await session.scalar(select(ProjectCellOperation.id).where(
                    ProjectCellOperation.workspace_id == W, ProjectCellOperation.idempotency_key == key))
                if existing:
                    raise PermissionError('Reconcile already attempted; inspect existing receipt, do not retry')
                operation, replay = await reserve_cell_operation(session, workspace_id=W,
                    generation_run_id=run, kind='reconcile', idempotency_key=key,
                    request={'indeterminate_operation_id': str(O)})
                if replay:
                    raise PermissionError('Unexpected replay')
                reconciliation_id = operation.id
                await session.commit()
            # Supported executor claims a higher epoch and validates exact workspace/fence response.
            outcome = await reconcile_indeterminate_cell_operation(factory, O, reconciliation_id, client)
            r = outcome.response
            print(json.dumps({'reconcile_operation_id': str(outcome.operation_id),
                'target_operation_id': str(O), 'status': outcome.status, 'fencing_epoch': outcome.fencing_epoch,
                'exact_target_receipt': outcome.reconciles_operation_id == O,
                'resource_response_present': r is not None,
                'resource_identity_matches': r is not None and r.workspace_id == W and r.fencing_epoch == outcome.fencing_epoch,
                'higher_epoch': outcome.fencing_epoch is not None and outcome.fencing_epoch > 2}))
            if (outcome.status != 'completed' or outcome.reconciles_operation_id != O or r is None
                or r.workspace_id != W or r.fencing_epoch != outcome.fencing_epoch
                or r.state not in {'retained','resources_ready','resources_paused','degraded','partial','destroyed'}
                or outcome.fencing_epoch is None or outcome.fencing_epoch <= 2):
                raise RuntimeError('No accepted resource reconciliation receipt; remain blocked')
    finally:
        await dispose_engine()

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['activity','release'])
    parser.add_argument('project')
    parser.add_argument('owner')
    parser.add_argument('run')
    parser.add_argument('--exclusive-owned-a-ack', required=True, choices=['yes'])
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as exc:
        print(json.dumps({'blocked': True, 'error_type': type(exc).__name__}))
        raise SystemExit(1) from None
