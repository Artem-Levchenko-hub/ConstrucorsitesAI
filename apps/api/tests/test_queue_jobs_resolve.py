"""Every job the API can enqueue must exist in the worker image.

RQ stores a job as a dotted path and imports it only when the worker picks it up,
so a job whose module was moved or removed fails in production, not in a test.
"""

from __future__ import annotations

import pytest
from rq.utils import import_attribute

from omnia_api.services import queue

JOBS = sorted(name for name in vars(queue) if name.endswith("_JOB"))


def test_the_queue_module_declares_its_jobs() -> None:
    assert JOBS, "job constants are expected to be named *_JOB"


@pytest.mark.parametrize("name", JOBS)
def test_job_path_resolves_to_a_callable(name: str) -> None:
    assert callable(import_attribute(getattr(queue, name)))
