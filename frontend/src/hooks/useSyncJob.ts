import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, SYNC_DISABLED_DETAIL, getSyncJob, startSync } from "../api/client";
import type { SyncJob } from "../api/types";

export const DEFAULT_POLL_INTERVAL_MS = 2000;
// ~2 minutes at the default interval above -- a bounded cap so an abandoned
// tab (or a worker that never picks the job up) doesn't poll forever. See
// the backend's own documented "a worker crash can leave a job stuck
// running" limitation -- the frontend shouldn't pretend it can wait that out.
const MAX_POLLS = 60;

export type SyncState =
  | { status: "idle" }
  | { status: "starting" }
  | { status: "queued" | "running" }
  | { status: "succeeded"; job: SyncJob }
  | { status: "failed"; job: SyncJob }
  | { status: "timed-out" }
  | { status: "conflict" }
  | { status: "unavailable" }
  | { status: "error"; message: string };

/** Owns POST /sync + the bounded poll of GET /sync-jobs/{id} that follows a
 * 202. React never learns *why* a deployment can't sync (it doesn't know
 * about BACKGROUND_SYNC_ENABLED) -- it only ever reacts to the HTTP
 * response, via the "unavailable" state below. */
export function useSyncJob(
  repositoryId: number,
  onSucceeded?: () => void,
  pollIntervalMs: number = DEFAULT_POLL_INTERVAL_MS
): {
  state: SyncState;
  start: () => void;
} {
  const [state, setState] = useState<SyncState>({ status: "idle" });
  const pollCountRef = useRef(0);
  const timerRef = useRef<number | undefined>(undefined);
  const onSucceededRef = useRef(onSucceeded);
  onSucceededRef.current = onSucceeded;

  const stopPolling = useCallback(() => {
    if (timerRef.current !== undefined) {
      window.clearTimeout(timerRef.current);
      timerRef.current = undefined;
    }
  }, []);

  useEffect(() => stopPolling, [stopPolling]);

  const poll = useCallback((jobId: string) => {
    timerRef.current = window.setTimeout(() => {
      void (async () => {
        pollCountRef.current += 1;
        try {
          const job = await getSyncJob(jobId);
          if (job.status === "succeeded") {
            setState({ status: "succeeded", job });
            onSucceededRef.current?.();
            return;
          }
          if (job.status === "failed") {
            setState({ status: "failed", job });
            return;
          }
          if (pollCountRef.current >= MAX_POLLS) {
            setState({ status: "timed-out" });
            return;
          }
          setState({ status: job.status });
          poll(jobId);
        } catch (error) {
          setState({
            status: "error",
            message: error instanceof ApiError ? error.message : "Something went wrong.",
          });
        }
      })();
    }, pollIntervalMs);
  }, [pollIntervalMs]);

  const start = useCallback(() => {
    setState({ status: "starting" });
    pollCountRef.current = 0;
    void (async () => {
      try {
        const created = await startSync(repositoryId);
        setState({ status: "queued" });
        poll(created.job_id);
      } catch (error) {
        if (error instanceof ApiError && error.status === 409) {
          setState({ status: "conflict" });
          return;
        }
        if (error instanceof ApiError && error.status === 503 && error.message === SYNC_DISABLED_DETAIL) {
          // The specific, recognized "no worker on this deployment" case --
          // learned purely from the response text, not a hard-coded flag.
          setState({ status: "unavailable" });
          return;
        }
        setState({
          status: "error",
          message: error instanceof ApiError ? error.message : "Something went wrong.",
        });
      }
    })();
  }, [repositoryId, poll]);

  return { state, start };
}
