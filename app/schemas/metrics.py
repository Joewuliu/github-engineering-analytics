from pydantic import BaseModel


class RepositoryMetricsResponse(BaseModel):
    repository_id: int
    full_name: str
    total_pull_requests: int
    merged_pull_requests: int
    merge_rate: float | None
    median_pr_cycle_time_hours: float | None
    median_time_to_first_review_hours: float | None
