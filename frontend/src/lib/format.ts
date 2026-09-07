/**
 * Formatting for tender figures.
 *
 * Money follows the Indian convention rather than the international one.
 * A tender worth 24,500,000 rupees is read and discussed as "2.45 Cr", never
 * as "24.5M", and getting this wrong makes every figure on the screen read as
 * foreign to the people who use it.
 */

const LAKH = 100_000;
const CRORE = 10_000_000;

export function formatRupees(amount: number | null): string {
  if (amount === null) return "—";

  if (amount >= CRORE) return `₹${(amount / CRORE).toFixed(2)} Cr`;
  if (amount >= LAKH) return `₹${(amount / LAKH).toFixed(2)} L`;

  // Below a lakh, the digit grouping is 2-2-3, not 3-3-3.
  return `₹${amount.toLocaleString("en-IN")}`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
}

export interface Countdown {
  days: number;
  label: string;
  /** Drives the row's urgency treatment. */
  urgency: "past" | "critical" | "warning" | "normal";
}

/**
 * Days until a tender closes.
 *
 * The thresholds are not arbitrary. A construction bid needs the earnest money
 * deposit arranged, and a joint venture agreement signed if one is needed, so
 * under a week is genuinely too late to start and under three days is lost.
 */
export function countdownTo(iso: string, now: Date = new Date()): Countdown {
  const closing = new Date(iso);
  const days = Math.ceil((closing.getTime() - now.getTime()) / 86_400_000);

  if (days < 0) return { days, label: "Closed", urgency: "past" };
  if (days === 0) return { days, label: "Closes today", urgency: "critical" };
  if (days <= 3) return { days, label: `${days}d left`, urgency: "critical" };
  if (days <= 7) return { days, label: `${days}d left`, urgency: "warning" };

  return { days, label: `${days}d left`, urgency: "normal" };
}
