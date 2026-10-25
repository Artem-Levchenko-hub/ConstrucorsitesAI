"""One atomic storage budget for all namespaces in the private attachment bucket."""

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yleum_api.models.reference_image import ReferenceImage
from yleum_api.models.task_board import TaskBoardAttachment, TaskBoardAttachmentCleanup

PRIVATE_BUCKET_BYTES_LIMIT = 1024 * 1024 * 1024
QUOTA_LOCK_KEY = "omnia-task-board-attachment-quota"


async def lock_private_attachment_quota(session: AsyncSession) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:attachment_key))"),
        {"attachment_key": QUOTA_LOCK_KEY},
    )


async def private_attachment_bytes(session: AsyncSession) -> int:
    # Deleting references are represented in the durable cleanup outbox instead;
    # pending uploads remain charged even after a failed or interrupted PUT.
    board = select(func.coalesce(func.sum(TaskBoardAttachment.size), 0)).scalar_subquery()
    cleanup = select(
        func.coalesce(func.sum(TaskBoardAttachmentCleanup.size), 0)
    ).scalar_subquery()
    references = (
        select(func.coalesce(func.sum(ReferenceImage.size_bytes), 0))
        .where(ReferenceImage.status != "deleting")
        .scalar_subquery()
    )
    # Use one MVCC snapshot: cleanup may move bytes between the metadata and
    # outbox concurrently, so separate SELECTs could miss both representations.
    return int(await session.scalar(select(board + cleanup + references)) or 0)
