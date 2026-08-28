import { useState } from "react";
import { Button } from "./Button";
import styles from "./ConfirmButton.module.css";

interface ConfirmButtonProps {
  label: string;
  confirmQuestion: string;
  onConfirm: () => void;
  disabled?: boolean;
}

export function ConfirmButton({ label, confirmQuestion, onConfirm, disabled }: ConfirmButtonProps) {
  const [confirming, setConfirming] = useState(false);

  if (confirming) {
    return (
      <span className={styles.confirmRow}>
        <span className={styles.confirmLabel}>{confirmQuestion}</span>
        <Button
          variant="danger"
          onClick={() => {
            setConfirming(false);
            onConfirm();
          }}
        >
          Yes
        </Button>
        <Button onClick={() => setConfirming(false)}>Cancel</Button>
      </span>
    );
  }

  return (
    <Button variant="danger" onClick={() => setConfirming(true)} disabled={disabled}>
      {label}
    </Button>
  );
}
