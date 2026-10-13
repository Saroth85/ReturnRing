// Explanation and message (US-304, US-305; docs/ui-design.md 6.2.3 item 6, 6.3.6): generated text
// sits apart from the decision and says it cannot change it. The draft must echo the engine
// decision or it is withheld (T-ECHO). Statements are labelled observed, score contribution or to
// verify, each citing its entity, metric, value and period. The customer message shows its three
// layers (connective text, template fields, fixed legal clause) in Italian and English.
import * as Popover from '@radix-ui/react-popover';
import clsx from 'clsx';
import { CircleCheck, OctagonAlert, RefreshCw } from 'lucide-react';
import { useState } from 'react';
import { Link } from 'react-router';
import { DRAFT_TONES, MESSAGE_LANGS } from '@/api/contract.gen';
import type { CaseCard, Lang } from '@/api/models';
import { useRegenerate } from '@/api/mutations';
import { usePersona } from '@/app/persona';
import { StatementLabelChip } from '@/components/decision';
import { InlineNotice, Skeleton } from '@/components/feedback';
import { GenerationBadge, MessagePreview } from '@/components/message';
import { IdText, NatureMark, displayNature } from '@/components/provenance';
import { Button } from '@/components/ui';
import { NATURE_WORD } from '@/copy/common';
import { echoMatches, engineEcho } from '@/domain/decision';
import type { RefKind } from '@/domain/refs';
import { OPERATOR } from '../../copy';
import { recordDraft } from '../../state/caseOverlay';
import { noticeStore } from '../../state/prefs';
import { regenerateBody, type Tone } from '../../state/requests';
import { useCaseUi } from '../context';
import { Section } from '../../ui/Section';
import { ActionError } from '../sheets/ActionError';

type Draft = NonNullable<CaseCard['draft']>;
type Statement = Draft['explanation'][number];
const LABEL_ORDER: Statement['label'][] = ['OBSERVED', 'SCORE', 'HYPOTHESIS'];

/** The kind of an entity named in a statement, from the shape of its id. */
function entityKind(entity: string): RefKind | null {
  if (entity.includes('~')) return 'link';
  if (entity.startsWith('CL-')) return 'cluster';
  if (entity.startsWith('RET-')) return 'return';
  if (entity.startsWith('ORD-')) return 'order';
  if (entity.startsWith('V-')) return 'variant';
  if (entity.startsWith('C-')) return 'customer';
  return null;
}

function FactCitation({ statement, card }: { statement: Statement; card: CaseCard }) {
  const { selectRef } = useCaseUi();
  const t = OPERATOR.explanation;
  const nature = displayNature(card.provenance);
  const kind = statement.entity ? entityKind(statement.entity) : null;
  return (
    <p className="flex flex-wrap items-center gap-x-1 text-caption text-ink-3" data-fact-citation>
      <span>{t.fact}:</span>
      {statement.entity &&
        (kind ? (
          <button
            type="button"
            className="rounded-chip font-mono text-accent hover:underline"
            onClick={() => selectRef({ kind, id: statement.entity ?? '' })}
            data-id
          >
            {statement.entity}
          </button>
        ) : (
          <IdText id={statement.entity} />
        ))}
      {statement.metric ? (
        <span data-verbatim>
          · {statement.metric} · {statement.value} · {statement.period}
        </span>
      ) : (
        <span>· {t.noFact}</span>
      )}
      <span className="inline-flex items-center gap-1">
        · <NatureMark nature={nature} decorative /> {NATURE_WORD[nature]}
      </span>
    </p>
  );
}

function ExplanationList({ card, draft }: { card: CaseCard; draft: Draft }) {
  const t = OPERATOR.explanation;
  const groups = LABEL_ORDER.map((label) => ({ label, statements: draft.explanation.filter((s) => s.label === label) })).filter(
    (g) => g.statements.length > 0,
  );
  return (
    <div className="flex flex-col gap-3">
      <h3 className="text-title">{t.explanationTitle}</h3>
      {groups.map((group) => (
        <div key={group.label} className="flex flex-col gap-2" data-statement-group={group.label}>
          {group.statements.map((s, i) =>
            s.label === 'SCORE' && card.path === 'guarantee' ? (
              <InlineNotice key={i} tone="attention">
                {t.scoreOnGuarantee}
              </InlineNotice>
            ) : (
              <div key={i} className="flex flex-col gap-1 border-l-2 border-border pl-3">
                <span className="flex flex-wrap items-baseline gap-2">
                  <StatementLabelChip label={s.label} />
                  <span data-generated data-verbatim className={clsx(s.label === 'HYPOTHESIS' && 'italic')}>
                    {s.text}
                  </span>
                </span>
                <FactCitation statement={s} card={card} />
              </div>
            ),
          )}
        </div>
      ))}
      <GenerationBadge generation={draft.generation} />
    </div>
  );
}

function EchoCheck({ card, draft }: { card: CaseCard; draft: Draft }) {
  const t = OPERATOR.explanation;
  const engine = engineEcho(card.decision);
  if (!echoMatches(draft.decision_echo, engine)) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-1 text-caption text-ink-2" data-echo="match">
      <CircleCheck aria-hidden="true" size={14} />
      {t.echoOk} {OPERATOR.queue.paths[engine.path]?.toLowerCase()} · <span data-id>{engine.quadrant ?? t.noQuadrant}</span> ·{' '}
      <span data-constant>{t.echoLevers(String(engine.lever_ids.length))}</span>
    </span>
  );
}

type View = Lang | 'both';

function MessagePanel({ draft }: { draft: Draft }) {
  const { sendLang } = useCaseUi();
  const t = OPERATOR.explanation;
  const [chosen, setView] = useState<View | null>(null);
  const view: View = chosen ?? sendLang;
  const shown = draft.messages.filter((m) => view === 'both' || m.lang === view);
  const views: View[] = [...MESSAGE_LANGS.filter((l) => draft.messages.some((m) => m.lang === l)), 'both'];
  return (
    <div className="flex flex-col gap-2" data-message-panel>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t.messageTitle}>
        <span className="text-title">{t.messageTitle}</span>
        {views.map((v) => (
          <button
            key={v}
            type="button"
            aria-pressed={view === v}
            onClick={() => setView(v)}
            className={clsx(
              'inline-flex min-h-8 items-center gap-1 rounded-control border px-2 text-body',
              view === v ? 'border-accent bg-accent-bg text-ink-1' : 'border-border-strong bg-surface text-ink-2',
            )}
            data-message-view={v}
          >
            {v === 'both' ? t.both : OPERATOR.sheets.langs[v]}
            {v === sendLang && <span className="text-caption text-ink-3">· {t.willBeSent}</span>}
          </button>
        ))}
      </div>
      {shown.length === 0 && <p className="text-body text-ink-3">{OPERATOR.railCopy.noDraft}</p>}
      {shown.map((m) => (
        <MessagePreview key={m.lang} message={m} stored={m.rendered_text} />
      ))}
    </div>
  );
}

function RegenerateMenu({ onSubmit, disabled, title }: { onSubmit(langs: Lang[], tone: Tone): void; disabled: boolean; title?: string }) {
  const t = OPERATOR.explanation;
  const [open, setOpen] = useState(false);
  const [langs, setLangs] = useState<Lang[]>(['it', 'en']);
  const [tone, setTone] = useState<Tone>('warm');
  const toggle = (lang: Lang) => setLangs((l) => (l.includes(lang) ? l.filter((x) => x !== lang) : [...l, lang]));
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger asChild>
        <Button disabled={disabled} title={title} data-regenerate>
          <RefreshCw aria-hidden="true" size={14} />
          {t.regenerate}
        </Button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          align="end"
          sideOffset={6}
          collisionPadding={16}
          className="z-50 flex w-72 flex-col gap-3 rounded-card border border-border bg-surface p-3 text-body shadow-overlay"
          data-regenerate-menu
        >
          <p className="font-medium">{t.regenerateTitle}</p>
          <fieldset className="flex flex-wrap gap-3">
            <legend className="mb-1 text-caption text-ink-3">{t.languages}</legend>
            {MESSAGE_LANGS.map((lang) => (
              <label key={lang} className="inline-flex items-center gap-1">
                <input type="checkbox" checked={langs.includes(lang)} onChange={() => toggle(lang)} />
                <span lang={lang}>{OPERATOR.sheets.langs[lang]}</span>
              </label>
            ))}
          </fieldset>
          <fieldset className="flex flex-wrap gap-3">
            <legend className="mb-1 text-caption text-ink-3">{t.tone}</legend>
            {DRAFT_TONES.map((value) => (
              <label key={value} className="inline-flex items-center gap-1">
                <input type="radio" name="tone" checked={tone === value} onChange={() => setTone(value)} />
                {t.tones[value]}
              </label>
            ))}
          </fieldset>
          <p className="text-caption text-ink-3">{t.noFreeText}</p>
          {langs.length === 0 && <p className="text-caption text-attn-fg">{t.pickLanguage}</p>}
          <Button
            variant="primary"
            disabled={langs.length === 0}
            onClick={() => {
              setOpen(false);
              onSubmit(langs, tone);
            }}
          >
            {t.regenerateSubmit}
          </Button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function ExplanationSection({ card, draftTimedOut, onReload }: { card: CaseCard; draftTimedOut: boolean; onReload(): void }) {
  const { gates, sentMessage } = useCaseUi();
  const persona = usePersona();
  const regenerate = useRegenerate(card.return_id);
  const [mismatch, setMismatch] = useState<string | null>(null);
  const t = OPERATOR.explanation;
  const draft = card.draft;
  const echoOk = draft ? echoMatches(draft.decision_echo, engineEcho(card.decision)) : true;
  const decided = card.decision.state !== 'proposed';
  const sentHere = sentMessage && sentMessage.decision_id === card.decision.decision_id ? sentMessage : null;

  const submit = (langs: Lang[], tone: Tone) => {
    setMismatch(null);
    regenerate.mutate(regenerateBody(card.decision, langs, tone, persona.id), {
      onSuccess: (result) => {
        if (result.return_id !== card.return_id || result.decision_id !== card.decision.decision_id) {
          setMismatch(OPERATOR.sheets.mismatch(result.decision_id, card.decision.decision_id));
          return;
        }
        recordDraft(card.return_id, result.decision_id, result.draft);
        noticeStore.set({ returnId: card.return_id, text: OPERATOR.result.regenerated, messageId: null, auditEntryIds: [] });
      },
    });
  };

  return (
    <Section
      id="message"
      title={t.title}
      aside={
        <>
          {draft && <EchoCheck card={card} draft={draft} />}
          {!decided && (
            <RegenerateMenu
              onSubmit={submit}
              disabled={!gates.regenerate.allowed || regenerate.isPending}
              title={gates.regenerate.reason ?? undefined}
            />
          )}
        </>
      }
    >
      <div className="flex flex-col gap-4">
        {gates.regenerateNote && !decided && <p className="text-caption text-ink-3">{gates.regenerateNote}</p>}
        {regenerate.isError && <ActionError error={regenerate.error} onRetry={() => regenerate.reset()} onLoadNew={onReload} />}
        {mismatch && <InlineNotice tone="attention">{mismatch}</InlineNotice>}
        {sentHere && (
          <p className="flex flex-wrap items-center gap-2 text-body" data-sent-message>
            <CircleCheck aria-hidden="true" size={16} className="text-ok-fg" />
            {t.sent}: <IdText id={sentHere.message_id} /> · {OPERATOR.sheets.langs[sentHere.lang]} ·{' '}
            <Link to={`/outbox?return_id=${card.return_id}`} className="text-accent underline underline-offset-2">
              {t.openOutbox}
            </Link>
          </p>
        )}
        {card.draft_status === 'pending' && !draft && (
          <div role="status" aria-busy={!draftTimedOut} className="flex flex-col gap-2">
            <p className="text-body text-ink-2">{draftTimedOut ? OPERATOR.banners.draftTimedOut : OPERATOR.banners.draftWait}</p>
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-2/3" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}
        {card.draft_status === 'ready' && !draft && <p className="text-body text-ink-2">{t.noDraftForVersion}</p>}
        {draft && !echoOk && (
          <div
            role="alert"
            className="flex flex-col gap-1 rounded-control border border-danger-fg bg-danger-bg px-3 py-2 text-body text-danger-fg"
            data-draft-withheld
          >
            <p className="flex items-center gap-2 font-medium">
              <OctagonAlert aria-hidden="true" size={16} />
              {t.withheld}
            </p>
            <p className="text-ink-1">{t.withheldBody}</p>
            <span className="text-caption text-ink-2">
              <IdText id={draft.generation.generation_id} />
            </span>
          </div>
        )}
        {draft && echoOk && (
          <div className={clsx('flex flex-col gap-4', regenerate.isPending && 'opacity-50')} aria-busy={regenerate.isPending}>
            {regenerate.isPending && (
              <p role="status" className="text-body text-ink-1">
                {t.regenerating}
              </p>
            )}
            {decided && !sentHere && <p className="text-caption text-ink-3">{t.draftFor}</p>}
            <ExplanationList card={card} draft={draft} />
            <MessagePanel key={draft.generation.generation_id} draft={draft} />
          </div>
        )}
      </div>
    </Section>
  );
}
