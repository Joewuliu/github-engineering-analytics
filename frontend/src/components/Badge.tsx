import type { HTMLAttributes } from "react";
import styles from "./Badge.module.css";

type Tone = "neutral" | "info" | "success" | "danger";

interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  tone?: Tone;
}

export function Badge({ tone = "neutral", className, ...rest }: BadgeProps) {
  const classes = [styles.badge, styles[tone], className].filter(Boolean).join(" ");
  return <span className={classes} {...rest} />;
}
