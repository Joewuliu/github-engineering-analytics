import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Legal application-level transitions: queued -> running -> {succeeded, failed}.
# The HTTP layer also allows one additional terminal transition,
# queued -> failed, for a job whose enqueue call itself failed (see
# app/services/sync_jobs.py) -- that path never reaches the worker.


class SyncJob(Base):
    __tablename__ = "sync_jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="status"),
        # The correctness boundary for "at most one active sync per
        # repository" (see app/services/sync_jobs.py) -- enforced here so
        # that even two concurrent requests that both pass an
        # application-level pre-check can't both insert an active job.
        Index(
            "uq_sync_jobs_active_repository",
            "repository_id",
            unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(20), default="queued")
    pull_requests_processed: Mapped[int | None] = mapped_column(default=None)
    reviews_processed: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    # Fixed, closed vocabularies only (see app/worker/tasks.py) -- never a
    # raw exception message or traceback.
    safe_error_code: Mapped[str | None] = mapped_column(String(40), default=None)
    safe_error_message: Mapped[str | None] = mapped_column(String(255), default=None)
