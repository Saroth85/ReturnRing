import clsx from 'clsx';
import { useState } from 'react';
import { PROVENANCE } from '@/copy/common';

/** An id (`RET-000102`, `CL-000417`) in mono at 13 px; a click copies it (docs/ui-design.md 4.4). */
export function IdText({ id, className }: { id: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(id);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  };
  return (
    <button
      type="button"
      data-id
      onClick={copy}
      title={copied ? PROVENANCE.copiedId : PROVENANCE.copyId}
      aria-label={`${id}, ${(copied ? PROVENANCE.copiedId : PROVENANCE.copyId).toLowerCase()}`}
      className={clsx('rounded-chip font-mono text-dense text-ink-1 hover:bg-surface-2', className)}
    >
      {id}
    </button>
  );
}
