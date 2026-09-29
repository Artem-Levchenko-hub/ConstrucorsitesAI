from datetime import datetime

from sqlalchemy import DateTime, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from yleum_api.models.base import Base


class DeploymentDrain(Base):
    """Durable admission fence; an interrupted deploy never silently reopens it."""

    __tablename__ = "deployment_drains"
    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    release_sha: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
