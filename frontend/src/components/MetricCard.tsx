import { NOT_ENOUGH_DATA } from "../utils/format";
import styles from "./MetricCard.module.css";

interface MetricCardProps {
  label: string;
  /** Already-formatted display text (see src/utils/format.ts) -- this
   * component never sees a raw number/null/undefined to accidentally render. */
  value: string;
}

export function MetricCard({ label, value }: MetricCardProps) {
  const muted = value === NOT_ENOUGH_DATA;
  return (
    <div className={styles.card}>
      <span className={styles.label}>{label}</span>
      <span className={muted ? styles.valueMuted : styles.value}>{value}</span>
    </div>
  );
}
