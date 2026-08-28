import { DEFAULT_POLL_INTERVAL_MS, useSyncJob } from "../hooks/useSyncJob";
import type { SyncState } from "../hooks/useSyncJob";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { Spinner } from "./Spinner";
import styles from "./SyncPanel.module.css";

interface SyncPanelProps {
  repositoryId: number;
  onSucceeded: () => void;
  /** Overridable only so tests can poll faster than the real 2s default --
   * never something a page/caller has a product reason to change. */
  pollIntervalMs?: number;
}

export function SyncPanel({ repositoryId, onSucceeded, pollIntervalMs = DEFAULT_POLL_INTERVAL_MS }: SyncPanelProps) {
  const { state, start } = useSyncJob(repositoryId, onSucceeded, pollIntervalMs);
  const busy = state.status === "starting" || state.status === "queued" || state.status === "running";

  return (
    <div className={styles.panel}>
      <Button variant="primary" onClick={start} disabled={busy}>
        {busy ? <Spinner label="Syncing" /> : "Sync now"}
      </Button>
      <StatusMessage state={state} />
    </div>
  );
}

function StatusMessage({ state }: { state: SyncState }) {
  switch (state.status) {
    case "idle":
      return null;

    case "starting":
      return <span className={styles.text}>Starting sync…</span>;

    case "queued":
      return <Badge tone="info">Queued</Badge>;

    case "running":
      return <Badge tone="info">Running</Badge>;

    case "succeeded":
      return (
        <span className={styles.row}>
          <Badge tone="success">Succeeded</Badge>
          <span className={styles.text}>
            {state.job.pull_requests_processed ?? 0} pull requests,{" "}
            {state.job.reviews_processed ?? 0} reviews processed
          </span>
        </span>
      );

    case "failed":
      return (
        <span className={styles.row}>
          <Badge tone="danger">Failed</Badge>
          <span className={styles.text}>{state.job.safe_error_message ?? "The sync failed."}</span>
        </span>
      );

    case "timed-out":
      return (
        <span className={styles.text}>
          This sync is taking longer than expected. Refresh to check again.
        </span>
      );

    case "conflict":
      return <span className={styles.text}>A sync is already in progress for this repository.</span>;

    case "unavailable":
      return (
        <div className={styles.infoBox}>
          <p className={styles.text}>Live synchronization is disabled on the hosted demo.</p>
          <p className={styles.textMuted}>
            The full background worker architecture is implemented and runs in local Docker Compose.
          </p>
        </div>
      );

    case "error":
      return (
        <span className={styles.row}>
          <Badge tone="danger">Error</Badge>
          <span className={styles.text}>{state.message}</span>
        </span>
      );
  }
}
