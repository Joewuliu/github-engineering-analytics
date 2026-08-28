export const NOT_ENOUGH_DATA = "Not enough data";

/** merge_rate is a 0..1 fraction from the backend (never a raw percentage). */
export function formatPercent(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return NOT_ENOUGH_DATA;
  }
  return `${Math.round(value * 100)}%`;
}

/** Backend hour values are always fractional (e.g. 19.4); render them at a
 * scale a human reads naturally instead of raw decimal hours. */
export function formatHours(value: number | null): string {
  if (value === null || Number.isNaN(value)) {
    return NOT_ENOUGH_DATA;
  }
  if (value < 1) {
    return `${Math.round(value * 60)} min`;
  }
  if (value < 48) {
    return `${trimTrailingZero(value)} hrs`;
  }
  return `${trimTrailingZero(value / 24)} days`;
}

function trimTrailingZero(value: number): string {
  return value.toFixed(1).replace(/\.0$/, "");
}
