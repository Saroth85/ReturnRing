// A ref cited by the agent (US-701: a direct link to the order or link cited). Always a real link
// (deep link of docs/ui-design.md 3.2); the panel's activation handler may take over a plain click
// to call the case card's onRefSelect, which scrolls to and pulses the link, account or order.
import { Fragment, type MouseEvent } from 'react';
import { Link } from 'react-router';
import type { EntityRef } from '@/api/models';
import { citationHref, citationLabel, focusesCaseCard } from './citations';
import { AGENT } from './copy';

/** Called on every click of a ref; it may preventDefault to keep the page where it is. */
export type RefActivate = (entity: EntityRef, event: MouseEvent<HTMLAnchorElement>) => void;

interface RefChipProps {
  entity: EntityRef;
  returnId: string;
  onActivate?: RefActivate;
}

/** A link id (`C-MARCO-1~C-MARCO-3`) may wrap after the `~`, never inside an account id. */
function BreakableId({ id }: { id: string }) {
  return id.split('~').map((part, index) => (
    <Fragment key={index}>
      {index > 0 && (
        <>
          ~<wbr />
        </>
      )}
      {part}
    </Fragment>
  ));
}

export function RefChip({ entity, returnId, onActivate }: RefChipProps) {
  const kind = citationLabel(entity.kind);
  const label = focusesCaseCard(entity, returnId) ? AGENT.refs.showOnCard(kind, entity.id) : AGENT.refs.open(kind, entity.id);
  return (
    <Link
      to={citationHref(entity, returnId)}
      onClick={(event) => onActivate?.(entity, event)}
      aria-label={label}
      title={label}
      data-ref-kind={entity.kind}
      data-ref-id={entity.id}
      className="inline-flex max-w-full flex-wrap items-baseline gap-x-1 rounded-chip border border-border bg-surface px-1.5 py-0.5 text-accent hover:border-accent hover:bg-accent-bg"
    >
      <span className="text-micro uppercase text-ink-3">{kind}</span>
      <span className="min-w-0 font-mono text-dense wrap-anywhere underline decoration-1 underline-offset-2" data-id>
        <BreakableId id={entity.id} />
      </span>
    </Link>
  );
}

/** Chips in a wrapping row; nothing when empty. */
export function RefList({ refs, returnId, onActivate }: { refs: EntityRef[]; returnId: string; onActivate?: RefActivate }) {
  if (refs.length === 0) return null;
  return (
    <ul className="flex flex-wrap gap-1.5">
      {refs.map((entity) => (
        <li key={`${entity.kind}:${entity.id}`} className="max-w-full">
          <RefChip entity={entity} returnId={returnId} onActivate={onActivate} />
        </li>
      ))}
    </ul>
  );
}
