import { Link } from "react-router-dom";
import type { TrackedRepository } from "../api/types";
import { ConfirmButton } from "./ConfirmButton";
import styles from "./RepositoryList.module.css";

interface RepositoryListProps {
  repositories: TrackedRepository[];
  onUntrack: (repositoryId: number) => void;
}

export function RepositoryList({ repositories, onUntrack }: RepositoryListProps) {
  if (repositories.length === 0) {
    return <p className={styles.empty}>You aren&rsquo;t tracking any repositories yet.</p>;
  }

  return (
    <ul className={styles.list}>
      {repositories.map((repository) => (
        <li key={repository.id} className={styles.item}>
          <Link to={`/repositories/${repository.id}`} className={styles.link}>
            {repository.full_name}
          </Link>
          <ConfirmButton
            label="Untrack"
            confirmQuestion={`Stop tracking ${repository.full_name}?`}
            onConfirm={() => onUntrack(repository.id)}
          />
        </li>
      ))}
    </ul>
  );
}
