import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getRepositoryMetrics } from "../api/client";
import type { RepositoryMetrics } from "../api/types";
import { MetricCard } from "../components/MetricCard";
import { Spinner } from "../components/Spinner";
import { SyncPanel } from "../components/SyncPanel";
import { formatHours, formatPercent } from "../utils/format";
import styles from "./RepositoryDetailPage.module.css";

export function RepositoryDetailPage() {
  const { id } = useParams<{ id: string }>();
  const repositoryId = Number(id);

  const [metrics, setMetrics] = useState<RepositoryMetrics | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMetrics(await getRepositoryMetrics(repositoryId));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err : new ApiError(0, "Something went wrong."));
    }
  }, [repositoryId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  if (error) {
    return (
      <main className={`container ${styles.status}`}>
        <p className={styles.error} role="alert">
          {error.status === 404 ? "Repository not found." : error.message}
        </p>
        <Link to="/">&larr; Back to dashboard</Link>
      </main>
    );
  }

  if (metrics === null) {
    return (
      <main className={`container ${styles.status}`}>
        <Spinner label="Loading metrics" />
      </main>
    );
  }

  return (
    <main className={`container ${styles.page}`}>
      <Link to="/" className={styles.back}>
        &larr; Back to dashboard
      </Link>
      <h1 className={styles.title}>{metrics.full_name}</h1>
      <div className={styles.grid}>
        <MetricCard label="Total pull requests" value={String(metrics.total_pull_requests)} />
        <MetricCard label="Merged pull requests" value={String(metrics.merged_pull_requests)} />
        <MetricCard label="Merge rate" value={formatPercent(metrics.merge_rate)} />
        <MetricCard label="Median PR cycle time" value={formatHours(metrics.median_pr_cycle_time_hours)} />
        <MetricCard
          label="Median time to first review"
          value={formatHours(metrics.median_time_to_first_review_hours)}
        />
      </div>
      <section className={styles.syncSection}>
        <h2 className={styles.sectionTitle}>Background sync</h2>
        <SyncPanel repositoryId={repositoryId} onSucceeded={() => void refresh()} />
      </section>
    </main>
  );
}
