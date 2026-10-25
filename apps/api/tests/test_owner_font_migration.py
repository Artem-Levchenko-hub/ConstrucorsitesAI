"""A font schema rollback cannot erase accepted recovery/history evidence."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command

from tests.test_migrations_single_head import _execute, project_cell_migration_database
from tests.test_reference_image_migration import _message

assert project_cell_migration_database


def test_font_upgrade_and_guarded_downgrade_preserve_existing_messages(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade("0092_reference_images")
    user, project, message = _message(db)
    db.upgrade("0093_owner_fonts")
    assert db.fetchval(f"SELECT font_selections FROM messages WHERE id='{message}'") is None
    command.downgrade(db.config, "0092_reference_images")
    assert (
        db.fetchval(f"SELECT content FROM messages WHERE id='{message}'") == "Текст до фотографий"
    )
    db.upgrade("0093_owner_fonts")
    asset = str(uuid4())
    asyncio.run(
        _execute(
            db.dsn,
            f"INSERT INTO font_assets(id,project_id,uploader_id,object_key,family,filename,sha256,"
            f"size_bytes,metadata_json,status,is_bound) VALUES ('{asset}','{project}','{user}',"
            f"'font-tests/{asset}','Synthetic','synthetic.ttf','{'a' * 64}',12,"
            "'{\"source\":\"upload\"}'::jsonb,'ready',true)",
        )
    )
    with pytest.raises(RuntimeError, match="font_evidence_present"):
        command.downgrade(db.config, "0092_reference_images")
    assert db.fetchval(f"SELECT count(*) FROM font_assets WHERE id='{asset}'") == 1
    assert db.fetchval("SELECT version_num FROM alembic_version") == "0093_owner_fonts"
