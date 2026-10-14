// Pieces shared by the action sheets (docs/ui-design.md 6.3): the ids line, the footer with the
// confirm button (Ctrl/Cmd+Enter; a bare Enter never confirms a restrictive action) and the
// reasons a confirm is disabled, printed under it.
import * as Dialog from '@radix-ui/react-dialog';
import type { DecisionView } from '@/api/models';
import { usePersona } from '@/app/persona';
import { IdText } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { useCaseUi } from '../context';
import { useNewerVersion } from './useNewerVersion';

/** The ids of the case and of the decision version the sheet acts on (the one shown). */
export function CaseIdsLine({ decision }: { decision?: DecisionView } = {}) {
  const { card } = useCaseUi();
  const d = decision ?? card.decision;
  return (
    <span className="flex flex-wrap items-center gap-x-2">
      <IdText id={card.return_id} />
      <IdText id={card.customer_id} />
      <span>{card.display_name}</span>
      {d.quadrant && <span data-id>{d.quadrant}</span>}
      <IdText id={d.decision_id} />
      <span data-version>v{d.version}</span>
    </span>
  );
}

export function ActingAs() {
  const persona = usePersona();
  return <p className="text-caption text-ink-2">{OPERATOR.sheets.actingAs(persona.id, roleWord(persona.role))}</p>;
}

interface ConfirmFooterProps {
  label: string;
  restrictive?: boolean;
  disabled: boolean;
  pending: boolean;
  /** Why the confirm is disabled, printed under it. */
  reasons: string[];
  onConfirm(): void;
  name: string;
}

export function ConfirmFooter({ label, restrictive = false, disabled, pending, reasons, onConfirm, name }: ConfirmFooterProps) {
  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap justify-end gap-2">
        <Dialog.Close asChild>
          <Button>
            {OPERATOR.sheets.cancel} <Kbd>Esc</Kbd>
          </Button>
        </Dialog.Close>
        <Button
          variant={restrictive ? 'restrictive' : 'primary'}
          disabled={disabled || pending}
          onClick={onConfirm}
          onKeyDown={(event) => {
            // No default button: Enter alone does nothing here (docs/ui-design.md 6.3.2); the sheet
            // handles Ctrl/Cmd+Enter once, so the button never activates on Enter itself.
            if (event.key === 'Enter') event.preventDefault();
          }}
          data-sheet-confirm={name}
        >
          {label} <Kbd>{OPERATOR.sheets.confirmKey}</Kbd>
        </Button>
      </div>
      {reasons.length > 0 && (
        <ul className="text-right text-caption text-ink-3" data-confirm-reasons>
          {reasons.map((r) => (
            <li key={r}>
              {OPERATOR.sheets.disabledBecause} {r}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * The stale state of a sheet (docs/ui-design.md 6.4): the card loaded again names a newer version
 * than the one the sheet was opened on. The confirm is blocked; "Load the new version" refetches,
 * lists what changed and moves the sheet onto the new version, keeping what was typed.
 */
export function StaleDecisionPanel({
  shown,
  onReload,
  changes,
}: {
  shown: Pick<DecisionView, 'decision_id'>;
  onReload(): void;
  changes?: string[] | null;
}) {
  const newer = useNewerVersion(shown);
  const s = OPERATOR.sheets;
  if (!newer) return null;
  return (
    <div
      role="alert"
      className="flex flex-col gap-2 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-body text-attn-fg"
      data-stale-panel
    >
      <p className="font-medium">{s.stale.title}</p>
      <p className="text-ink-1" data-verbatim>
        {s.stale.shownNotLatest(shown.decision_id, newer)}
      </p>
      {changes && changes.length > 0 && (
        <ul className="list-disc pl-5 text-caption text-ink-1">
          {changes.map((c) => (
            <li key={c} data-verbatim>
              {c}
            </li>
          ))}
        </ul>
      )}
      <span className="flex flex-wrap items-center gap-2">
        <Button onClick={onReload} data-load-new-version>
          {s.stale.load}
        </Button>
        <span className="text-caption text-ink-2">{s.stale.kept}</span>
      </span>
    </div>
  );
}
