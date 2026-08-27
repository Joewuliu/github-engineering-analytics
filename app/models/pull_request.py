from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.pull_request_review import PullRequestReview


class PullRequest(Base):
    __tablename__ = "pull_requests"
    __table_args__ = (
        UniqueConstraint("repository_id", "number", name="uq_pull_requests_repository_id_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    repository_id: Mapped[int] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    number: Mapped[int] = mapped_column()
    # GitHub login strings are mutable and may be null for deleted accounts --
    # never treated as identity, unlike github_id.
    author_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(20))
    # GitHub event timestamps -- when things happened on GitHub.
    github_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    github_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Our own row timestamps -- when we ingested/last touched this record.
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    reviews: Mapped[list[PullRequestReview]] = relationship(
        back_populates="pull_request", passive_deletes=True
    )
