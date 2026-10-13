// Pieces shared by the action sheets (docs/ui-design.md 6.3): the ids line, the footer with the
// confirm button (Ctrl/Cmd+Enter; a bare Enter never confirms a restrictive action) and the
// reasons a confirm is disabled, printed under it.
import * as Dialog from '@radix-ui/react-dialog';
import { usePersona } from '@/app/persona';
import { IdText } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { InlineNotice } from '@/components/feedback';
import { OPERATOR } from '../../copy';
import { roleWord } from '../../format';
import { useCaseUi } from '../context';

export function CaseIdsLine() {
  const { card } = useCaseUi();
  const d = card.decision;
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

export function StaleOnOpen({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <InlineNotice tone="attention" title={OPERATOR.sheets.stale.title}>
      <span data-verbatim>{message}</span>
    </InlineNotice>
  );
}
