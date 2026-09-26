from uuid import uuid4

from yleum_api.services.project_cell_executor import valid_project_migration_receipt


def test_full_build_requires_structured_bound_database_receipt():
    workspace, run = uuid4(), uuid4()
    kwargs = dict(workspace_id=workspace, generation_run_id=run, fencing_epoch=7,
                  source_revision="1" * 64, adaptation=False)
    receipt = dict(contract="project-migrations-v1", mode="apply", workspace_id=str(workspace),
                   generation_run_id=str(run), fencing_epoch=7, source_revision="1" * 64,
                   source_digest="2" * 64, database_identity="3" * 64, catalog_digest="4" * 64,
                   migration_count=1)
    assert valid_project_migration_receipt(receipt, **kwargs)
    assert not valid_project_migration_receipt(None, **kwargs)
    for key, value in [("generation_run_id", str(uuid4())), ("catalog_digest", ""),
                       ("fencing_epoch", 6), ("mode", "verify_only")]:
        assert not valid_project_migration_receipt({**receipt, key: value}, **kwargs)
    assert valid_project_migration_receipt({**receipt, "mode": "verify_only"},
                                          **{**kwargs, "adaptation": True})
