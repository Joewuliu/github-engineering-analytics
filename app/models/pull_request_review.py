from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.pull_request import PullRequest


class PullRequestReview(Base):
    __tablename__ = "pull_request_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    github_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    pull_request_id: Mapped[int] = mapped_column(
        ForeignKey("pull_requests.id", ondelete="CASCADE"), index=True
    )
    # Mutable, may be null for deleted accounts -- never treated as identity.
    reviewer_login: Mapped[str | None] = mapped_column(String(255), nullable=True)
    state: Mapped[str] = mapped_column(String(30))
    # Null for PENDING (not-yet-submitted) reviews.
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    pull_request: Mapped[PullRequest] = relationship(back_populates="reviews")
