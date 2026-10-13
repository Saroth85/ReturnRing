import clsx from 'clsx';
import { Info, OctagonAlert, TriangleAlert, type LucideIcon } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import { DEMO_USERS } from '@/api/contract.gen';
import { personaName, setPersona } from '@/app/persona';
import { FEEDBACK, SHELL } from '@/copy/common';
import { LLM_RETRY_SECONDS } from '@/copy/constants';
import { Button } from '@/components/ui/Button';

type Tone = 'info' | 'attention' | 'danger';
const TONES: Record<Tone, { className: string; Icon: LucideIcon }> = {
  info: { className: 'border-border bg-surface-2 text-ink-1', Icon: Info },
  attention: { className: 'border-attn-border bg-attn-bg text-attn-fg', Icon: TriangleAlert },
  danger: { className: 'border-danger-fg bg-danger-bg text-danger-fg', Icon: OctagonAlert },
};

/** An inline notice with an icon and words (colour is never the only carrier). */
export function InlineNotice({ tone = 'info', title, children }: { tone?: Tone; title?: string; children?: ReactNode }) {
  const { className, Icon } = TONES[tone];
  return (
    <div role={tone === 'danger' ? 'alert' : 'status'} className={clsx('flex gap-2 rounded-control border px-3 py-2', className)}>
      <Icon aria-hidden="true" size={16} className="mt-0.5 shrink-0" />
      <div className="text-body">
        {title && <p className="font-medium">{title}</p>}
        {children}
      </div>
    </div>
  );
}

/** The language model is down: the decision is unaffected (docs/ui-design.md 5.8). */
export function LlmUnavailableNotice({ onRetry }: { onRetry?: () => void }) {
  const [wait, setWait] = useState(LLM_RETRY_SECONDS);
  useEffect(() => {
    if (wait <= 0) return;
    const timer = setTimeout(() => setWait((w) => w - 1), 1000);
    return () => clearTimeout(timer);
  }, [wait]);
  return (
    <InlineNotice tone="attention">
      <p>{FEEDBACK.llmUnavailable}</p>
      {onRetry && (
        <Button className="mt-2" disabled={wait > 0} onClick={onRetry} data-constant>
          {wait > 0 ? FEEDBACK.llmRetryIn(wait) : FEEDBACK.retry}
        </Button>
      )}
    </InlineNotice>
  );
}

/** A 403 that RoleGate should have prevented: say why and offer the other personas. */
export function ForbiddenRoleNotice({ role, action }: { role: string; action: string }) {
  return (
    <InlineNotice tone="attention">
      <p>{FEEDBACK.forbidden(SHELL.roles[role] ?? role, action)}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {DEMO_USERS.filter((u) => u.role !== role).map((u) => (
          <Button key={u.id} onClick={() => setPersona(u.id)}>
            {SHELL.actingAs} {personaName(u.id)} · {SHELL.roles[u.role] ?? u.role}
          </Button>
        ))}
      </div>
    </InlineNotice>
  );
}
