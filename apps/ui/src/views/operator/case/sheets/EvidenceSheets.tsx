// Favourable evidence and link disputes (US-310, docs/ui-design.md 6.3.7, flow F7): the case is
// recomputed into a new version, shown before and after; both versions stay in the audit log.
// There is no "add unfavourable evidence": the operator can only add what speaks for the customer.
import { useState } from 'react';
import { EVIDENCE_KINDS } from '@/api/contract.gen';
import type { LinkDisputeResponse as DisputeResponse, RecomputeResponse } from '@/api/models';
import { useAddEvidence, useDisputeLink } from '@/api/mutations';
import { usePersona } from '@/app/persona';
import { InlineNotice } from '@/components/feedback';
import { OPERATOR } from '../../copy';
import { disputeBody, evidenceBody, LINK_EXPLAINING_KINDS, type EvidenceKind } from '../../state/requests';
import { ReasonField } from '../../ui/fields';
import { Sheet } from '../../ui/Sheet';
import { reasonValid } from '../../ui/validation';
import { useCaseUi } from '../context';
import { activeLinks, linkState } from '../linked/linkModel';
import { ActionError } from './ActionError';
import { ActingAs, CaseIdsLine, ConfirmFooter, StaleOnOpen } from './common';


interface EvidenceSheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  linkId?: string;
  onDone(recompute: RecomputeResponse, explainedLinks: string[]): void;
  onReload(): void;
  changes: string[] | null;
  staleOnOpen: string | null;
}

export function AddEvidenceSheet({ open, onOpenChange, linkId, onDone, onReload, changes, staleOnOpen }: EvidenceSheetProps) {
  const { card, gates } = useCaseUi();
  const persona = usePersona();
  const mutation = useAddEvidence(card.return_id);
  const links = card.linked_accounts ? activeLinks(card.linked_accounts.links) : [];
  const [kind, setKind] = useState<EvidenceKind>(
    linkId ? 'shared_payment_explained' : links.length > 0 ? 'household_members' : 'customer_contact',
  );
  const [targets, setTargets] = useState<string[]>(linkId ? [linkId] : []);
  const [note, setNote] = useState('');
  const [tried, setTried] = useState(false);
  const [mismatch, setMismatch] = useState<string | null>(null);
  const e = OPERATOR.sheets.evidence;
  const explains = LINK_EXPLAINING_KINDS.includes(kind);
  const reasons = [
    ...(explains && targets.length === 0 ? [e.linksRequired] : []),
    ...(!reasonValid(note) ? [OPERATOR.sheets.reasonTooShort] : []),
    ...(!gates.evidence.allowed && gates.evidenceNote ? [gates.evidenceNote] : []),
  ];
  const confirm = () => {
    setTried(true);
    if (reasons.length > 0 || mutation.isPending) return;
    setMismatch(null);
    mutation.mutate(evidenceBody(card.decision, kind, note, targets, persona.id), {
      onSuccess: (result) => {
        if (result.return_id !== card.return_id) {
          setMismatch(OPERATOR.sheets.mismatch(result.return_id, card.return_id));
          return;
        }
        onDone(result, explains ? targets : []);
      },
    });
  };
  const toggle = (id: string) => setTargets((t) => (t.includes(id) ? t.filter((x) => x !== id) : [...t, id]));
  return (
    <Sheet
      name="evidence"
      open={open}
      onOpenChange={(next) => {
        if (!next) mutation.reset();
        onOpenChange(next);
      }}
      title={e.title}
      description={<CaseIdsLine />}
      onConfirm={confirm}
      dirty={note.trim().length > 0}
      footer={
        <ConfirmFooter
          label={e.submit}
          disabled={reasons.length > 0}
          pending={mutation.isPending}
          reasons={reasons}
          onConfirm={confirm}
          name="evidence"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleOnOpen message={staleOnOpen} />
        {mutation.isError && <ActionError error={mutation.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} />}
        {mismatch && <InlineNotice tone="attention">{mismatch}</InlineNotice>}
        <fieldset className="flex flex-col gap-2">
          <legend className="mb-1 text-title">{e.kind}</legend>
          {EVIDENCE_KINDS.map((k) => {
            const needsLinks = LINK_EXPLAINING_KINDS.includes(k);
            const disabled = needsLinks && links.length === 0;
            return (
              <label key={k} className="grid grid-cols-[auto_1fr] items-start gap-x-2 text-body" data-evidence-kind={k}>
                <input
                  type="radio"
                  name="evidence-kind"
                  className="mt-1"
                  checked={kind === k}
                  disabled={disabled}
                  onChange={() => setKind(k)}
                />
                <span className="font-medium">{e.kinds[k]}</span>
                <span className="col-start-2 text-caption text-ink-2">{needsLinks ? e.explainsLinks : e.recordedOnly}</span>
              </label>
            );
          })}
        </fieldset>
        {explains && (
          <fieldset className="flex flex-col gap-1" data-evidence-links>
            <legend className="mb-1 text-title">{e.links}</legend>
            {links.length === 0 && <p className="text-body text-ink-3">{e.noLinks}</p>}
            {links.map((l) => (
              <label key={l.link_id} className="flex flex-wrap items-center gap-2 text-body">
                <input type="checkbox" checked={targets.includes(l.link_id)} onChange={() => toggle(l.link_id)} />
                <span className="font-mono text-dense" data-id>
                  {l.customer_a} ~ {l.customer_b}
                </span>
                <span className="text-caption text-ink-3">{l.kinds.map((k) => OPERATOR.linked.kinds[k]).join(', ')}</span>
              </label>
            ))}
            {tried && targets.length === 0 && <p className="text-caption text-danger-fg">{e.linksRequired}</p>}
          </fieldset>
        )}
        <ReasonField value={note} onChange={setNote} showError={tried} label={e.note} hint={e.noteHint} name="note" />
        <ActingAs />
      </div>
    </Sheet>
  );
}

interface DisputeSheetProps {
  open: boolean;
  onOpenChange(open: boolean): void;
  linkId?: string;
  onDone(response: DisputeResponse): void;
  onReload(): void;
  changes: string[] | null;
  staleOnOpen: string | null;
}

export function DisputeLinkSheet({ open, onOpenChange, linkId, onDone, onReload, changes, staleOnOpen }: DisputeSheetProps) {
  const { card, gates } = useCaseUi();
  const persona = usePersona();
  const mutation = useDisputeLink(card.return_id);
  const links = card.linked_accounts?.links ?? [];
  const [chosen, setChosen] = useState<string>(linkId ?? links.find((l) => linkState(l) === 'active')?.link_id ?? '');
  const [reason, setReason] = useState('');
  const [tried, setTried] = useState(false);
  const d = OPERATOR.sheets.dispute;
  const link = links.find((l) => l.link_id === chosen);
  const reasons = [
    ...(!link || linkState(link) !== 'active' ? [d.alreadyDisputed] : []),
    ...(!reasonValid(reason) ? [OPERATOR.sheets.reasonTooShort] : []),
    ...(!gates.dispute.allowed && gates.evidenceNote ? [gates.evidenceNote] : []),
  ];
  const confirm = () => {
    setTried(true);
    if (reasons.length > 0 || mutation.isPending || !link) return;
    mutation.mutate({ linkId: link.link_id, body: disputeBody(card, reason, persona.id) }, { onSuccess: onDone });
  };
  return (
    <Sheet
      name="dispute"
      open={open}
      onOpenChange={(next) => {
        if (!next) mutation.reset();
        onOpenChange(next);
      }}
      title={d.title}
      description={<CaseIdsLine />}
      onConfirm={confirm}
      dirty={reason.trim().length > 0}
      footer={
        <ConfirmFooter
          label={d.submit}
          disabled={reasons.length > 0}
          pending={mutation.isPending}
          reasons={reasons}
          onConfirm={confirm}
          name="dispute"
        />
      }
    >
      <div className="flex flex-col gap-4">
        <StaleOnOpen message={staleOnOpen} />
        {mutation.isError && <ActionError error={mutation.error} onLoadNew={onReload} onShowCurrent={onReload} changes={changes} />}
        <p className="text-body text-ink-1">{d.body}</p>
        <label className="flex flex-col gap-1 text-body">
          <span className="font-medium">{d.link}</span>
          <select
            value={chosen}
            onChange={(e) => setChosen(e.target.value)}
            className="rounded-control border border-border-strong bg-surface px-2 py-1 font-mono"
          >
            {links.map((l) => (
              <option key={l.link_id} value={l.link_id} disabled={linkState(l) !== 'active'}>
                {l.customer_a} ~ {l.customer_b} · {l.kinds.map((k) => OPERATOR.linked.kinds[k]).join(', ')}
                {linkState(l) !== 'active' ? ` (${d.alreadyDisputed})` : ''}
              </option>
            ))}
          </select>
        </label>
        <ReasonField value={reason} onChange={setReason} showError={tried} />
        <ActingAs />
      </div>
    </Sheet>
  );
}
