"""Offline authorization regressions: no object store, object reads, or mutations."""

import json
from fnmatch import fnmatchcase
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from yleum_api.core import minio as storage
from yleum_api.services import agent_media

PROJECT = "afe27034-ab68-48cd-be9f-8883702c6bd3"
DIGEST = "a" * 32


@pytest.fixture
def configured(monkeypatch):
    settings = SimpleNamespace(
        minio_bucket_previews="previews",
        minio_bucket_images="omnia-images",
        minio_bucket_photos="omnia-photos",
        minio_bucket_videos="omnia-videos",
        minio_bucket_projects="projects",
        minio_bucket_backups="backups",
        minio_bucket_task_board="task-board",
        minio_public_legacy_media_keys="",
    )
    monkeypatch.setattr(storage, "get_settings", lambda: settings)
    return settings


def anonymous_read_allowed(policy, bucket, key):
    """Evaluate the generated, unconditional GetObject Allow statements only."""
    resource = f"arn:aws:s3:::{bucket}/{key}"
    return any(
        statement["Effect"] == "Allow"
        and statement["Principal"] == {"AWS": ["*"]}
        and statement["Action"] == ["s3:GetObject"]
        and any(fnmatchcase(resource, pattern) for pattern in statement["Resource"])
        for statement in json.loads(policy)["Statement"]
    )


@pytest.mark.parametrize("bucket", ["previews", "omnia-images", "omnia-photos", "omnia-videos"])
@pytest.mark.parametrize(
    "key",
    [
        f"exe/{PROJECT}/{PROJECT}/app-Setup.exe",
        f"source/{PROJECT}/logo.png",
        f"archives/{PROJECT}.zip",
        f"repos/{PROJECT}.tar.gz",
        f"exports/{PROJECT}.json",
        f"{PROJECT}/{DIGEST}.zip",
        f"{PROJECT}/source/logo.png",
    ],
)
def test_old_exact_archive_urls_lose_anonymous_access(configured, bucket, key):
    legacy = json.dumps(
        {
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": ["*"]},
                    "Action": ["s3:GetObject"],
                    "Resource": [f"arn:aws:s3:::{bucket}/*"],
                }
            ]
        }
    )
    assert anonymous_read_allowed(legacy, bucket, key)
    client = Mock()
    client.bucket_exists.return_value = True
    storage.ensure_public_bucket(client, bucket)
    installed = client.set_bucket_policy.call_args.args[1]
    assert not anonymous_read_allowed(installed, bucket, key)


@pytest.mark.parametrize(
    "bucket,key",
    [
        ("previews", f"{PROJECT}.png"),
        ("omnia-images", f"{PROJECT}/{DIGEST}.png"),
        *[
            ("omnia-images", f"max-content/{PROJECT}/{DIGEST}.{ext}")
            for ext in ("jpg", "png", "webp", "gif")
        ],
        ("omnia-photos", f"{PROJECT}/{DIGEST}.jpg"),
        ("omnia-videos", f"{PROJECT}/{DIGEST}.mp4"),
    ],
)
def test_all_current_public_media_writer_keys_remain_allowed(configured, bucket, key):
    client = Mock()
    storage.ensure_public_bucket(client, bucket)
    assert anonymous_read_allowed(client.set_bucket_policy.call_args.args[1], bucket, key)


def test_startup_replaces_existing_policy_and_initializes_missing_media_only(configured):
    client = Mock()
    client.bucket_exists.side_effect = lambda bucket: bucket != "omnia-photos"
    storage.reconcile_public_bucket_policies(client)
    assert {call.args[0] for call in client.set_bucket_policy.call_args_list} == {
        "previews",
        "omnia-images",
        "omnia-photos",
        "omnia-videos",
    }
    client.make_bucket.assert_called_once_with("omnia-photos")
    for call in client.set_bucket_policy.call_args_list:
        policy = json.loads(call.args[1])
        assert all(statement["Effect"] == "Allow" for statement in policy["Statement"])
        assert all(statement["Action"] == ["s3:GetObject"] for statement in policy["Statement"])
        assert f"arn:aws:s3:::{call.args[0]}/*" not in policy["Statement"][0]["Resource"]
    client.get_object.assert_not_called()
    client.put_object.assert_not_called()
    client.list_objects.assert_not_called()
    client.remove_object.assert_not_called()
    client.remove_bucket.assert_not_called()


def test_private_bucket_collision_rejected_before_any_storage_call(configured):
    configured.minio_bucket_images = configured.minio_bucket_projects
    client = Mock()
    with pytest.raises(ValueError, match="private"):
        storage.reconcile_public_bucket_policies(client)
    assert client.mock_calls == []


def test_public_bucket_aliases_merge_only_approved_media_shapes(configured):
    configured.minio_bucket_videos = configured.minio_bucket_images
    client = Mock()
    storage.ensure_public_bucket(client, "omnia-images")
    policy = client.set_bucket_policy.call_args.args[1]
    assert anonymous_read_allowed(policy, "omnia-images", f"{PROJECT}/{DIGEST}.png")
    assert anonymous_read_allowed(policy, "omnia-images", f"{PROJECT}/{DIGEST}.mp4")
    assert not anonymous_read_allowed(policy, "omnia-images", f"exe/{PROJECT}.exe")


def test_unconfigured_bucket_cannot_be_made_public(configured):
    client = Mock()
    with pytest.raises(ValueError, match="not configured"):
        storage.ensure_public_bucket(client, "omnia-user-uploads")
    assert client.mock_calls == []


def test_policy_failure_is_not_silently_accepted(configured):
    client = Mock()
    error = S3Error("AccessDenied", "policy denied", "/", "r", "h", None)
    client.set_bucket_policy.side_effect = error
    with pytest.raises(S3Error):
        storage.reconcile_public_bucket_policies(client)


def test_video_upload_cannot_reopen_archive_access(configured):
    client = Mock()
    assert agent_media._ensure_video_bucket(client, "omnia-videos")
    installed = client.set_bucket_policy.call_args.args[1]
    assert anonymous_read_allowed(installed, "omnia-videos", f"{PROJECT}/{DIGEST}.mp4")
    assert not anonymous_read_allowed(installed, "omnia-videos", f"exe/{PROJECT}.exe")


def test_video_policy_failure_stops_upload(configured):
    client = Mock()
    client.set_bucket_policy.side_effect = S3Error(
        "AccessDenied",
        "policy denied",
        "/",
        "r",
        "h",
        None,
    )
    assert not agent_media._ensure_video_bucket(client, "omnia-videos")


# Synthetic object identifiers only; no actual inventory keys are used.
LEGACY_IMAGE = f"uploads/{PROJECT}/{DIGEST}.png"
LEGACY_PHOTO = f"smoke2/{DIGEST}.jpg"
SIBLING = "00000000-0000-4000-8000-000000000001"


@pytest.mark.parametrize(
    "bucket,key",
    [
        ("omnia-images", LEGACY_IMAGE),
        ("omnia-photos", LEGACY_PHOTO),
        ("omnia-photos", f"Sample/multi/segment/{PROJECT.upper()}.JPG"),
    ],
)
def test_exact_legacy_keys_allow_only_the_approved_object(configured, bucket, key):
    configured.minio_public_legacy_media_keys = json.dumps({bucket: [key]})
    policy = storage.public_media_bucket_policy(bucket)
    resources = json.loads(policy)["Statement"][0]["Resource"]
    assert f"arn:aws:s3:::{bucket}/{key}" in resources
    assert anonymous_read_allowed(policy, bucket, key)
    assert not anonymous_read_allowed(
        policy, bucket, key.rsplit("/", 1)[0] + f"/{SIBLING}." + key.rsplit(".", 1)[1]
    )
    assert not anonymous_read_allowed(policy, bucket, key + ".zip")
    assert not anonymous_read_allowed(policy, bucket, f"source/{PROJECT}/{PROJECT}.png")
    assert not anonymous_read_allowed(policy, bucket, f"exe/{PROJECT}/{PROJECT}.exe")


@pytest.mark.parametrize(
    "raw",
    [
        "invalid-json",
        "[]",
        "null",
        '"text"',
        '{"omnia-images":"key"}',
        '{"omnia-images":[null]}',
        '{"omnia-images":[12]}',
        '{"projects":[]}',
        '{"backups":[]}',
        '{"task-board":[]}',
        '{"unknown":[]}',
        '{"previews":[]}',
        '{"omnia-videos":[]}',
    ],
)
def test_invalid_legacy_config_fails_before_any_storage_call(configured, raw):
    configured.minio_public_legacy_media_keys = raw
    client = Mock()
    with pytest.raises(ValueError, match="legacy public media configuration") as error:
        storage.reconcile_public_bucket_policies(client)
    assert error.value.args == ("invalid legacy public media configuration",)
    assert client.mock_calls == []


@pytest.mark.parametrize(
    "key",
    [
        LEGACY_IMAGE + ".zip",
        LEGACY_IMAGE.replace(".png", ".exe"),
        LEGACY_IMAGE.replace("uploads", "source"),
        LEGACY_IMAGE.replace("uploads", "archives"),
        LEGACY_IMAGE.replace("uploads", "exports"),
        LEGACY_IMAGE.replace("uploads", "exe"),
        LEGACY_IMAGE.replace(DIGEST + ".png", "*.png"),
        LEGACY_IMAGE.replace(DIGEST + ".png", "?.png"),
        "../" + LEGACY_IMAGE,
        LEGACY_IMAGE.replace("/", "\\"),
        LEGACY_IMAGE.replace("uploads/", "uploads/%2e%2e/"),
        LEGACY_IMAGE.replace(".png", ".svg"),
        LEGACY_IMAGE.replace(".png", ".zip.png"),
        LEGACY_IMAGE.replace("uploads/", "uploads/source-files/"),
        LEGACY_IMAGE.replace("uploads/", "uploads/\ud800/"),
        LEGACY_IMAGE.replace("uploads/", "uploads//"),
        LEGACY_IMAGE.replace("uploads/", "uploads/./"),
    ],
)
def test_unsafe_image_exceptions_are_rejected_without_key_disclosure(configured, key):
    configured.minio_public_legacy_media_keys = json.dumps({"omnia-images": [key]})
    client = Mock()
    with pytest.raises(ValueError) as error:
        storage.reconcile_public_bucket_policies(client)
    assert error.value.args == ("invalid legacy public media configuration",)
    assert client.mock_calls == []


@pytest.mark.parametrize(
    "namespace", ["source", "export", "backup", "EXE", "archives", "repos", "src"]
)
def test_source_archive_namespaces_cannot_be_photo_exceptions(configured, namespace):
    configured.minio_public_legacy_media_keys = json.dumps(
        {
            "omnia-photos": [f"{namespace}/{PROJECT}/{PROJECT}.jpg"],
        }
    )
    client = Mock()
    with pytest.raises(ValueError, match="legacy public media configuration"):
        storage.reconcile_public_bucket_policies(client)
    assert client.mock_calls == []


@pytest.mark.parametrize("raw", ["", "  ", "{}"])
def test_empty_compatibility_config_preserves_strict_v1(configured, raw):
    strict = storage.public_media_bucket_policy("omnia-images")
    configured.minio_public_legacy_media_keys = raw
    assert storage.public_media_bucket_policy("omnia-images") == strict
    assert not anonymous_read_allowed(strict, "omnia-images", LEGACY_IMAGE)


def test_only_other_bucket_legacy_key_is_invalid_before_client_creation(configured, monkeypatch):
    configured.minio_public_legacy_media_keys = json.dumps({"unknown": [LEGACY_IMAGE]})
    created = Mock()
    monkeypatch.setattr(storage, "get_minio_client", created)
    with pytest.raises(ValueError, match="legacy public media configuration"):
        storage.reconcile_public_bucket_policies()
    created.assert_not_called()


def test_duplicate_keys_produce_only_one_literal_resource(configured):
    configured.minio_public_legacy_media_keys = json.dumps({"omnia-images": [LEGACY_IMAGE] * 2})
    policy = json.loads(storage.public_media_bucket_policy("omnia-images"))
    resource = f"arn:aws:s3:::omnia-images/{LEGACY_IMAGE}"
    assert policy["Statement"][0]["Resource"].count(resource) == 1


def test_startup_and_later_uploads_preserve_same_literal_exceptions(configured):
    configured.minio_public_legacy_media_keys = json.dumps(
        {
            "omnia-images": [LEGACY_IMAGE],
            "omnia-photos": [LEGACY_PHOTO],
        }
    )
    client = Mock()
    storage.reconcile_public_bucket_policies(client)
    policies = {call.args[0]: call.args[1] for call in client.set_bucket_policy.call_args_list}
    storage.ensure_public_bucket(client, "omnia-images")
    assert client.set_bucket_policy.call_args.args[1] == policies["omnia-images"]
    assert anonymous_read_allowed(
        policies["omnia-images"], "omnia-images", f"{PROJECT}/{DIGEST}.png"
    )
    assert anonymous_read_allowed(
        policies["omnia-photos"], "omnia-photos", f"{PROJECT}/{DIGEST}.jpg"
    )
    for operation in ("list_objects", "get_object", "stat_object", "put_object", "remove_object"):
        getattr(client, operation).assert_not_called()


def test_legacy_field_blank_env_and_json_roundtrip(monkeypatch):
    from yleum_api.core.config import Settings

    for value in ("", json.dumps({"omnia-images": [LEGACY_IMAGE]})):
        monkeypatch.setenv("MINIO_PUBLIC_LEGACY_MEDIA_KEYS", value)
        settings = Settings(_env_file=None, database_url="postgresql://unused", jwt_secret="unused")
        assert settings.minio_public_legacy_media_keys == value
        assert "minio_public_legacy_media_keys" not in repr(settings)
    assert settings.offhost_backup_read_token is None
