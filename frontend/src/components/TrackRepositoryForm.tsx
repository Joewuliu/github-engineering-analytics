import { useState } from "react";
import type { FormEvent } from "react";
import { ApiError, trackRepository } from "../api/client";
import { Button } from "./Button";
import { Spinner } from "./Spinner";
import styles from "./TrackRepositoryForm.module.css";

interface TrackRepositoryFormProps {
  onTracked: () => void;
}

// The backend is authoritative on what "owner/repository" actually means
// (RepositoryCreateRequest's validator, then a real GitHub lookup) -- this
// form deliberately does not replicate that validation, only submits.
export function TrackRepositoryForm({ onTracked }: TrackRepositoryFormProps) {
  const [fullName, setFullName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await trackRepository(fullName.trim());
      setFullName("");
      onTracked();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form className={styles.form} onSubmit={(event) => void handleSubmit(event)}>
      <label className={styles.label} htmlFor="track-repository-input">
        Track a repository
      </label>
      <div className={styles.row}>
        <input
          id="track-repository-input"
          className={styles.input}
          type="text"
          placeholder="fastapi/fastapi"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
          disabled={submitting}
          required
        />
        <Button type="submit" variant="primary" disabled={submitting || fullName.trim() === ""}>
          {submitting ? <Spinner label="Tracking repository" /> : "Track"}
        </Button>
      </div>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
