// Simulated outbox /outbox (US-304, docs/ui-design.md 6.7). Stub: the messages and the first one
// in its three layers (connective text, template fields, fixed legal clause).
import { Mail } from 'lucide-react';
import { useSearchParams } from 'react-router';
import { useOutbox } from '@/api/queries';
import { DataCheck, EmptyState, InlineNotice, ScreenPlaceholder } from '@/components/feedback';
import { MessagePreview } from '@/components/message';
import { DateText, IdText } from '@/components/provenance';
import { OPERATOR } from '../copy';

export default function OutboxPage() {
  const [params] = useSearchParams();
  const returnId = params.get('return_id');
  const outbox = useOutbox(returnId ? { return_id: returnId } : {});
  const t = OPERATOR.outbox;
  const first = outbox.data?.items[0];
  return (
    <ScreenPlaceholder title={OPERATOR.titles.outbox} area="operator" stories={['US-304']} spec="docs/ui-design.md 6.7">
      <InlineNotice tone="info">{t.banner}</InlineNotice>
      <DataCheck label="GET /api/outbox" result={outbox} />
      {outbox.data && outbox.data.items.length === 0 && <EmptyState icon={Mail} message={t.empty} />}
      {outbox.data && outbox.data.items.length > 0 && (
        <ul className="divide-y divide-border rounded-card border border-border bg-surface">
          {outbox.data.items.map((m) => (
            <li key={m.message_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-dense">
              <IdText id={m.message_id} />
              <span className="font-medium">{m.subject}</span>
              <span className="text-ink-3">{m.lang}</span>
              <span className="text-ink-3">{t.status[m.status] ?? m.status}</span>
              <span className="text-ink-3">
                <DateText value={m.created_at} />
              </span>
            </li>
          ))}
        </ul>
      )}
      {first && <MessagePreview message={first} stored={first.body} />}
    </ScreenPlaceholder>
  );
}
