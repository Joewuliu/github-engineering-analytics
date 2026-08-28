import type {
  Repository,
  RepositoryMetrics,
  SyncJob,
  SyncJobCreated,
  TrackedRepository,
  User,
} from "./types";

/** Carries the backend's own safe `detail` string -- never a stack trace,
 * since every backend domain exception already maps to one of these
 * (see app/api/errors.py). status 0 means the request never reached the
 * server at all (offline/network failure). */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // Relative paths, same-origin in both dev (via Vite's proxy) and
  // production (FastAPI serves the SPA itself) -- no `credentials:
  // "include"` needed, since same-origin requests already send cookies by
  // default. The HttpOnly session cookie is never read here or anywhere
  // else in this app; the browser handles it entirely on its own.
  let response: Response;
  try {
    response = await fetch(path, init);
  } catch {
    throw new ApiError(0, "Could not reach the server. Check your connection and try again.");
  }

  if (!response.ok) {
    throw new ApiError(response.status, await safeDetail(response));
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return (await response.json()) as T;
}

async function safeDetail(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { detail?: unknown };
    if (typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // No JSON body -- fall through to the generic message below.
  }
  return "Something went wrong. Please try again.";
}

function get<T>(path: string): Promise<T> {
  return request<T>(path);
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(
    path,
    body === undefined
      ? { method: "POST" }
      : {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
  );
}

function del<T>(path: string): Promise<T> {
  return request<T>(path, { method: "DELETE" });
}

export function getCurrentUser(): Promise<User> {
  return get<User>("/auth/me");
}

export function logout(): Promise<void> {
  return post<void>("/auth/logout");
}

export function listTrackedRepositories(): Promise<TrackedRepository[]> {
  return get<TrackedRepository[]>("/me/repositories");
}

export function trackRepository(fullName: string): Promise<Repository> {
  return post<Repository>("/me/repositories", { full_name: fullName });
}

export function untrackRepository(repositoryId: number): Promise<void> {
  return del<void>(`/me/repositories/${repositoryId}`);
}

export function getRepositoryMetrics(repositoryId: number): Promise<RepositoryMetrics> {
  return get<RepositoryMetrics>(`/me/repositories/${repositoryId}/metrics`);
}

export function startSync(repositoryId: number): Promise<SyncJobCreated> {
  return post<SyncJobCreated>(`/me/repositories/${repositoryId}/sync`);
}

// The exact, stable safe-error text app/api/errors.py maps
// BackgroundSyncDisabledError to (app/services/sync_jobs.py). Both this and
// a Redis/Dramatiq enqueue failure return 503, but they mean different
// things -- this is how the frontend tells them apart, purely by reading
// the response the backend already sent, never a separate config flag.
export const SYNC_DISABLED_DETAIL = "Background synchronization is unavailable in this deployment.";

export function getSyncJob(jobId: string): Promise<SyncJob> {
  return get<SyncJob>(`/me/sync-jobs/${jobId}`);
}
