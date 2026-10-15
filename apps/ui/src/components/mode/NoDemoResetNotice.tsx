import { TriangleAlert } from 'lucide-react';
import { isApiError } from '@/api/errors';
import { useDemoCases } from '@/api/queries';
import { SHELL } from '@/copy/common';

/**
 * A line under the mode banner, on every page, while GET /api/demo/cases answers 404 (no demo
 * reset has stored the golden cases yet, error.no_demo_reset): the API's message verbatim and the
 * command to run. The shell loads the demo cases for every role, so this shows before any view
 * needs them; a page that cannot open its case says so in its own words as well.
 */
export function NoDemoResetNotice() {
  const cases = useDemoCases();
  const error = cases.error;
  if (!isApiError(error) || error.status !== 404) return null;
  const t = SHELL.noDemoReset;
  return (
    <div
      role="status"
      data-demo-reset-missing
      className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-attn-border bg-attn-bg px-4 py-1.5 text-caption text-attn-fg"
    >
      <TriangleAlert aria-hidden="true" size={14} className="self-center" />
      <strong className="font-semibold">{t.title}</strong>
      <span>{t.body}</span>
      <span className="font-mono break-words" data-verbatim>
        {error.message}
      </span>
      <span>{t.fix}</span>
    </div>
  );
}
