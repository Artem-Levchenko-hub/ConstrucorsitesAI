"""Exact SDK boundary, with no server or credentials and no actual network."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from minio.error import S3Error

from yleum_gateway.services.generation_response_objects import PrivateObjects
from yleum_gateway.services.generation_response_store import CHUNK_BYTES, ResponseIndeterminate


def objects(client):
    value = object.__new__(PrivateObjects)
    value.client, value.bucket = client, "private-synthetic-only"
    return value


@pytest.mark.parametrize(
    "policy",
    [
        {},
        {"Statement": [{}]},
        {"Statement": [{"Effect": "Allow", "Principal": "*"}]},
    ],
)
async def test_any_public_or_unknown_policy_blocks_capture(policy):
    client = SimpleNamespace(
        bucket_exists=lambda _: True, get_bucket_policy=lambda _: json.dumps(policy)
    )
    with pytest.raises(ResponseIndeterminate):
        await objects(client).preflight()


async def test_policy_absence_is_explicit_and_other_sdk_denials_are_not_absence():
    for code in ("NoSuchBucketPolicy", "AccessDenied", "NoSuchBucket"):
        client = Mock()
        client.bucket_exists.return_value = True
        client.get_bucket_policy.side_effect = S3Error(
            code=code,
            message="synthetic",
            resource="synthetic",
            request_id="id",
            host_id="id",
            response=None,
        )
        if code == "NoSuchBucketPolicy":
            await objects(client).preflight()
        else:
            with pytest.raises(S3Error):
                await objects(client).preflight()
        client.make_bucket.assert_not_called()
        client.set_bucket_policy.assert_not_called()


@pytest.mark.parametrize(
    "body", [b"complete", b"x" * (CHUNK_BYTES + 1)], ids=["valid", "oversized-chunk"]
)
async def test_read_always_releases_sdk_connection_and_bounds_only_each_chunk(body):
    response = Mock(wraps=io.BytesIO(body))
    response.release_conn = Mock()
    client = SimpleNamespace(get_object=lambda *args: response)
    if len(body) <= CHUNK_BYTES:
        assert await objects(client).get("synthetic-key") == body
    else:
        with pytest.raises(ResponseIndeterminate):
            await objects(client).get("synthetic-key")
    response.close.assert_called_once()
    response.release_conn.assert_called_once()
