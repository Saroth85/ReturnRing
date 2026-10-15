import { isApiError } from '@/api/errors';
import { InlineNotice } from '@/components/feedback';
import { PLATFORM } from '../copy';

const t = PLATFORM.demo.noReset;

/**
 * Before a demo reset GET /api/demo/cases answers 404 with what to run (error.no_demo_reset): the
 * notice quotes the API verbatim and names the fix. Any other failure keeps its own message.
 */
export function NoResetNotice({ error }: { error: unknown }) {
  return (
    <div data-no-demo-reset>
      <InlineNotice tone="attention" title={t.title}>
        <p>{t.body}</p>
        <p className="font-mono text-dense break-words" data-verbatim>
          {isApiError(error) ? error.message : PLATFORM.demo.unreachable}
        </p>
        <p className="mt-1">{t.fix}</p>
      </InlineNotice>
    </div>
  );
}
