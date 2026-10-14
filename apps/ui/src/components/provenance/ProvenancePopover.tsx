import * as Popover from '@radix-ui/react-popover';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Link, useInRouterContext } from 'react-router';
import type { DisplayNature, Provenance } from '@/api/models';
import { NATURE_MEANING, NATURE_WORD, PROVENANCE } from '@/copy/common';
import { formatPeriod } from '@/format';
import { displayNature } from './nature';
import { NatureMark } from './NatureMark';

interface ProvenancePopoverProps {
  provenance: Provenance;
  /** What the figure is, e.g. "Risk score". */
  label?: string;
  /** The formatted value, repeated in the popover. */
  valueText?: string;
  nature?: DisplayNature;
  /** The trigger: a focusable element (Figure renders a button). */
  children: ReactNode;
}

const OPEN_DELAY_MS = 300;
const CLOSE_DELAY_MS = 200;

function AboutLink() {
  const inRouter = useInRouterContext();
  const className = 'text-accent underline underline-offset-2 hover:text-accent-hover';
  return inRouter ? (
    <Link to="/about#natures" className={className}>
      {PROVENANCE.whatNaturesMean}
    </Link>
  ) : (
    <a href="/about#natures" className={className}>
      {PROVENANCE.whatNaturesMean}
    </a>
  );
}

/**
 * The full provenance of a figure (docs/ui-design.md 5.1), on hover, focus or Enter/Space:
 * value and label, the nature with its meaning, the origin verbatim (the "Contract example:"
 * prefix is never stripped), the period, and links.
 */
export function ProvenancePopover({ provenance, label, valueText, nature, children }: ProvenancePopoverProps) {
  const [open, setOpen] = useState(false);
  const [byHover, setByHover] = useState(false);
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Whether the open popover came from a hover: closing it then never moves focus. Radix returns
  // focus to the trigger, which scrolled the page back to a figure the pointer merely crossed
  // while the page scrolled (a cited ref on a phone).
  const hovered = useRef(false);
  const shown = nature ?? displayNature(provenance);
  const period = formatPeriod(provenance.period_start, provenance.period_end);

  useEffect(
    () => () => {
      if (timer.current) clearTimeout(timer.current);
    },
    [],
  );

  const schedule = (next: boolean, delay: number) => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (next) hovered.current = true;
      setByHover(next);
      setOpen(next);
    }, delay);
  };
  const keep = () => {
    if (timer.current) clearTimeout(timer.current);
  };

  const copy = async () => {
    const text = `${label ? `${label}: ` : ''}${valueText ?? ''} · ${NATURE_WORD[shown]} · ${provenance.origin} · ${period}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  };

  return (
    <Popover.Root
      open={open}
      onOpenChange={(next) => {
        if (next) hovered.current = false;
        setByHover(false);
        setOpen(next);
        if (!next) setCopied(false);
      }}
    >
      <Popover.Trigger asChild onPointerEnter={() => schedule(true, OPEN_DELAY_MS)} onPointerLeave={() => schedule(false, CLOSE_DELAY_MS)}>
        {children}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          data-provenance-detail
          side="top"
          align="start"
          sideOffset={6}
          collisionPadding={16}
          onOpenAutoFocus={(event) => {
            if (byHover) event.preventDefault();
          }}
          onCloseAutoFocus={(event) => {
            if (hovered.current) event.preventDefault();
            hovered.current = false;
          }}
          onPointerEnter={keep}
          onPointerLeave={() => schedule(false, CLOSE_DELAY_MS)}
          className="z-50 w-80 max-w-[calc(100vw-32px)] rounded-card border border-border bg-surface p-3 text-body text-ink-1 shadow-overlay"
        >
          {label && <p className="text-caption text-ink-3">{label}</p>}
          {valueText && <p className="text-title tabular-nums">{valueText}</p>}
          <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-caption">
            <dt className="text-ink-3">{PROVENANCE.nature}</dt>
            <dd>
              <span className="inline-flex items-center gap-1 font-medium">
                <NatureMark nature={shown} decorative />
                {NATURE_WORD[shown]}
              </span>{' '}
              <span className="text-ink-2">{NATURE_MEANING[shown]}</span>
            </dd>
            <dt className="text-ink-3">{PROVENANCE.origin}</dt>
            <dd className="break-words">{provenance.origin}</dd>
            <dt className="text-ink-3">{PROVENANCE.period}</dt>
            <dd>{period}</dd>
          </dl>
          <div className="mt-3 flex flex-wrap gap-3 text-caption">
            <AboutLink />
            <button type="button" onClick={copy} className="text-accent underline underline-offset-2 hover:text-accent-hover">
              {copied ? PROVENANCE.copied : PROVENANCE.copy}
            </button>
          </div>
          <Popover.Arrow className="fill-surface" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
