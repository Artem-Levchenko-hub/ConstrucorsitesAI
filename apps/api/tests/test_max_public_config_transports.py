"""Exercise actual transport call sites against the installed controller receiver.

Only the transport call AST is isolated, so bootstrap/publish effects need no DB,
Docker or generation. Renamed/removed/multiply matched call sites fail this test.
"""
import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from yleum_api.schemas.max_studio import MaxProjectConfigPayload
from yleum_api.services.max_content_delivery import config_digest, public_business_config

ROOT = Path(__file__).resolve().parents[3]
MODULE = ROOT / 'apps/orchestrator/src/yleum_orchestrator/services/app_business_config.py'
spec = importlib.util.spec_from_file_location('public_receiver', MODULE)
receiver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receiver)


@pytest.mark.parametrize('modules', [None, [], ['catalog', 'orders']])
@pytest.mark.parametrize(('file', 'transport', 'occurrence'), [
    ('services/project_cell_executor.py', 'project_cell_apply_business_config', 0),
    ('services/project_cell_executor.py', '_seed_business_config', -1),
    ('services/cell_publication.py', 'publish_project_cell', 0),
    ('routers/max_studio.py', 'configure_published_cell', 0),
    ('routers/max_studio.py', 'project_cell_apply_business_config', 0),
])
async def test_public_transport_uses_receiver_compatible_projection(
    modules, file, transport, occurrence
):
    tree = ast.parse((ROOT / 'apps/api/src/yleum_api' / file).read_text(encoding='utf-8'))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and
             (getattr(n.func, 'id', None) or getattr(n.func, 'attr', None)) == transport]
    calls.sort(key=lambda n: n.lineno)
    call = calls[occurrence]
    payload = MaxProjectConfigPayload.default_for("Shop").model_copy(
        update={"management_modules": modules}
    )
    captured = []

    async def receive(*args, **kwargs):
        if transport == '_seed_business_config':
            version, config = args
        elif 'config' in kwargs:
            version, config = kwargs['version'], kwargs['config']
        else:
            version, config = args[1]['business_config_version'], args[1]['business_config']
        envelope = receiver.app_config_envelope(config, version)
        captured.append(envelope)
        assert config_digest(config) == receiver.config_digest(public_business_config(payload))
        assert version == 7

    identity = uuid4()
    context = dict(
        config=payload,
        payload=payload,
        version=7,
        workspace_id=identity,
        project_id=identity,
        user_id=identity,
        generation_run_id=identity,
        project=SimpleNamespace(id=identity),
        current_user=SimpleNamespace(id=identity),
        response=SimpleNamespace(fencing_epoch=3),
        record=SimpleNamespace(config_version=7),
        selection=SimpleNamespace(workspace=SimpleNamespace(id=identity)),
        evidence={},
        publication_key="fixed",
        integration=None,
        integration_runtime_env=lambda _: {},
        public_business_config=public_business_config,
        orchestrator_client=SimpleNamespace(**{transport: receive}),
        **{transport: receive},
    )
    await eval(compile(ast.Expression(call), file, 'eval'), context)
    assert len(captured) == 1
