import { useCallback, useEffect, useState } from "react";
import { ApiError, listTrackedRepositories, logout, untrackRepository } from "../api/client";
import type { TrackedRepository, User } from "../api/types";
import { Button } from "../components/Button";
import { RepositoryList } from "../components/RepositoryList";
import { Spinner } from "../components/Spinner";
import { TrackRepositoryForm } from "../components/TrackRepositoryForm";
import styles from "./DashboardPage.module.css";

interface DashboardPageProps {
  user: User;
  onLogout: () => void;
}

export function DashboardPage({ user, onLogout }: DashboardPageProps) {
  const [repositories, setRepositories] = useState<TrackedRepository[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setRepositories(await listTrackedRepositories());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleUntrack(repositoryId: number) {
    try {
      await untrackRepository(repositoryId);
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    }
  }

  async function handleLogout() {
    try {
      await logout();
    } finally {
      // Session cookie invalidation already happened server-side (or the
      // request failed, in which case there's nothing more we can do from
      // here anyway) -- either way, clearing local state and returning to
      // the landing page is the correct outcome.
      onLogout();
    }
  }

  return (
    <div>
      <header className={styles.header}>
        <div className={`container ${styles.headerInner}`}>
          <span className={styles.brand}>GitHub Engineering Analytics</span>
          <div className={styles.headerRight}>
            <span className={styles.username}>{user.github_login}</span>
            <Button onClick={() => void handleLogout()}>Sign out</Button>
          </div>
        </div>
      </header>
      <main className="container">
        <section className={styles.section}>
          <TrackRepositoryForm onTracked={() => void refresh()} />
        </section>
        <section className={styles.section}>
          <h2 className={styles.sectionTitle}>Tracked repositories</h2>
          {error && (
            <p role="alert" className={styles.error}>
              {error}
            </p>
          )}
          {repositories === null ? (
            <Spinner label="Loading repositories" />
          ) : (
            <RepositoryList repositories={repositories} onUntrack={(id) => void handleUntrack(id)} />
          )}
        </section>
      </main>
    </div>
  );
}
