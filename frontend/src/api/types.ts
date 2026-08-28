// Hand-written to mirror app/schemas/*.py exactly (field names, optionality).
// Not code-generated -- the response surface is small and stable enough that
// generation would be more machinery than it's worth for this MVP.

export interface User {
  id: number;
  github_id: number;
  github_login: string;
  created_at: string;
  updated_at: string;
}

export interface Repository {
  id: number;
  github_id: number;
  full_name: string;
  created_at: string;
  updated_at: string;
}

export interface TrackedRepository extends Repository {
  tracked_at: string;
}

export interface SyncJobCreated {
  job_id: string;
  repository_id: number;
  status: string;
}

export type SyncJobStatus = "queued" | "running" | "succeeded" | "failed";

export interface SyncJob {
  job_id: string;
  repository_id: number;
  status: SyncJobStatus;
  pull_requests_processed: number | null;
  reviews_processed: number | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  safe_error_code: string | null;
  safe_error_message: string | null;
}

export interface RepositoryMetrics {
  repository_id: number;
  full_name: string;
  total_pull_requests: number;
  merged_pull_requests: number;
  merge_rate: number | null;
  median_pr_cycle_time_hours: number | null;
  median_time_to_first_review_hours: number | null;
}
