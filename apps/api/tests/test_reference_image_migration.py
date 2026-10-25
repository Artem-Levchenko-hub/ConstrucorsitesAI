"""Actual PostgreSQL retention across the reference-image schema boundary."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command

from tests.test_migrations_single_head import _execute, project_cell_migration_database

assert project_cell_migration_database
OLD = "0091_generation_credit_units"
NEW = "0092_reference_images"


def _message(db):
    user, project, message = (str(uuid4()) for _ in range(3))
    asyncio.run(
        _execute(
            db.dsn,
            f"INSERT INTO users(id,email) VALUES ('{user}','{user}@example.invalid');"
            "INSERT INTO projects(id,owner_id,name,slug,template) "
            f"VALUES ('{project}','{user}','synthetic','{project}','blank');"
            "INSERT INTO messages(id,project_id,role,content) "
            f"VALUES ('{message}','{project}','user','Текст до фотографий');",
        )
    )
    return user, project, message


def test_real_reference_upgrade_and_empty_downgrade_keep_old_messages(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade(OLD)
    _, _, message = _message(db)
    db.upgrade(NEW)
    assert (
        db.fetchval(f"SELECT content FROM messages WHERE id='{message}'") == "Текст до фотографий"
    )
    assert db.fetchval(f"SELECT reference_images FROM messages WHERE id='{message}'") is None
    assert (
        db.fetchval(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name='reference_images' AND column_name='project_id'"
        )
        == "YES"
    )
    assert (
        db.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE tablename='reference_images' "
            "AND indexname='ix_reference_images_cleanup'"
        )
        == 1
    )
    asyncio.run(
        _execute(db.dsn, f"UPDATE messages SET reference_images='[]'::jsonb WHERE id='{message}'")
    )
    command.downgrade(db.config, OLD)
    assert (
        db.fetchval(f"SELECT content FROM messages WHERE id='{message}'") == "Текст до фотографий"
    )
    db.upgrade(NEW)
    assert db.fetchval(f"SELECT reference_images FROM messages WHERE id='{message}'") is None


@pytest.mark.parametrize("evidence", ["metadata", "message", "json_null"])
def test_reference_downgrade_refuses_existing_evidence_without_loss(
    project_cell_migration_database,
    evidence,
):
    db = project_cell_migration_database
    db.upgrade(NEW)
    user, project, message = _message(db)
    if evidence == "metadata":
        asyncio.run(
            _execute(
                db.dsn,
                "INSERT INTO reference_images(id,project_id,uploader_id,object_key,"
                "filename,mime_type,"
                "sha256,size_bytes,width,height) "
                f"VALUES ('{uuid4()}','{project}','{user}','synthetic/{uuid4()}',"
                "'synthetic.png','image/png','" + "a" * 64 + "',4,1,1)",
            )
        )
    else:
        value = '[{"id":"synthetic-reference"}]' if evidence == "message" else "null"
        asyncio.run(
            _execute(
                db.dsn,
                f"UPDATE messages SET reference_images='{value}'::jsonb WHERE id='{message}'",
            )
        )
    before = db.fetchval("SELECT count(*) FROM reference_images")
    manifest = db.fetchval(f"SELECT reference_images::text FROM messages WHERE id='{message}'")
    with pytest.raises(RuntimeError, match="reference_image_evidence_present"):
        command.downgrade(db.config, OLD)
    assert db.fetchval("SELECT version_num FROM alembic_version") == NEW
    assert db.fetchval("SELECT count(*) FROM reference_images") == before
    assert (
        db.fetchval(f"SELECT reference_images::text FROM messages WHERE id='{message}'") == manifest
    )
    assert (
        db.fetchval(f"SELECT content FROM messages WHERE id='{message}'") == "Текст до фотографий"
    )


def test_deleting_project_and_uploader_retains_private_object_metadata(
    project_cell_migration_database,
):
    db = project_cell_migration_database
    db.upgrade(NEW)
    user, project, _ = _message(db)
    image, key = str(uuid4()), "synthetic/" + str(uuid4())
    asyncio.run(
        _execute(
            db.dsn,
            "INSERT INTO reference_images(id,project_id,uploader_id,object_key,filename,mime_type,"
            "sha256,size_bytes,width,height) "
            f"VALUES ('{image}','{project}','{user}','{key}','synthetic.png','image/png','"
            + "b"
            * 64
            + "',4,1,1);"
            f"DELETE FROM projects WHERE id='{project}';DELETE FROM users WHERE id='{user}';",
        )
    )
    row = db.fetch(
        f"SELECT project_id,uploader_id,object_key FROM reference_images WHERE id='{image}'"
    )[0]
    assert row["project_id"] is None and row["uploader_id"] is None
    assert row["object_key"] == key
