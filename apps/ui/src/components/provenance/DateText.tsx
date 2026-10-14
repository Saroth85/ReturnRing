import { formatDate, formatPeriod, formatTimestamp } from '@/format';

/** A date (`27 Dec 2026`) or a UTC timestamp (`30 Sep 2026, 18:20 UTC`); exempt from the provenance scan. */
export function DateText({ value, className }: { value: string; className?: string }) {
  const isTimestamp = value.includes('T');
  return (
    <time dateTime={value} data-date className={className}>
      {isTimestamp ? formatTimestamp(value) : formatDate(value)}
    </time>
  );
}

/** `1 Oct 2025 – 30 Sep 2026`, or one date when start equals end. */
export function DateRange({ start, end, className }: { start: string; end: string; className?: string }) {
  return (
    <span data-date className={className}>
      {formatPeriod(start, end)}
    </span>
  );
}
