import type { ButtonHTMLAttributes } from "react";
import styles from "./Button.module.css";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: "default" | "primary" | "danger";
}

export function Button({ variant = "default", className, ...rest }: ButtonProps) {
  const variantClass = variant === "primary" ? styles.primary : variant === "danger" ? styles.danger : "";
  const classes = [styles.button, variantClass, className].filter(Boolean).join(" ");
  return <button type="button" className={classes} {...rest} />;
}
