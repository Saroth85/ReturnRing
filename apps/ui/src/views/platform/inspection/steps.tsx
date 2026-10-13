// The steps of an inspection (US-501..503, docs/ui-design.md 9.2): photo, checklist, proposal and
// the confirmed result. Tablet first (48 px targets). The model's suggestions are ghost hints with
// their declared confidence as muted text, never a bar; Ahmed's answers start empty.
import clsx from 'clsx';
import { Camera, CircleCheck, ImageUp, OctagonAlert, TriangleAlert } from 'lucide-react';
import { useId, useState, type DragEvent, type KeyboardEvent, type ReactNode } from 'react';
import { Link } from 'react-router';
import type { InspectionView } from '@/api/models';
import { GenerationBadge, MessagePreview } from '@/components/message';
import { DateText, Figure, IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Kbd } from '@/components/ui';
import { PHOTO_TYPES } from '../constants';
import { INSPECTION } from './copy';
import {
  CHECKLIST_ITEMS,
  OBSERVATIONS,
  proposalStatus,
  type AnswerProblem,
  type Answers,
  type ChecklistItem,
  type ConfirmResult,
  type Observation,
} from './inspection';

const t = INSPECTION;

export function StepPanel({
  step,
  title,
  state,
  children,
}: {
  step: keyof typeof INSPECTION.steps;
  title: string;
  state: 'waiting' | 'current' | 'done';
  children?: ReactNode;
}) {
  return (
    <section
      aria-labelledby={`step-${step}`}
      data-step={step}
      data-step-state={state}
      className={clsx(
        'flex flex-col gap-3 rounded-card border bg-surface p-4',
        state === 'current' ? 'border-accent' : 'border-border',
        state === 'waiting' && 'opacity-70',
      )}
    >
      <h2 id={`step-${step}`} className="flex items-center gap-2 text-h3">
        {state === 'done' && <CircleCheck aria-hidden="true" size={18} className="text-ink-2" />}
        {title}
      </h2>
      {children}
    </section>
  );
}

export function ProblemNotice({ problem, mock }: { problem: AnswerProblem; mock: boolean }) {
  return (
    <div role="alert" className="flex flex-col gap-1 rounded-control border border-danger-fg bg-danger-bg px-3 py-2 text-danger-fg" data-answer-problem={problem.kind}>
      <p className="flex items-center gap-2 font-medium">
        <OctagonAlert aria-hidden="true" size={16} />
        {t.problem[problem.kind](problem.got)}
      </p>
      {mock && <p className="text-body text-ink-1">{t.problem.mockHint}</p>}
    </div>
  );
}

/** The chosen photo; a file the browser cannot decode gets a caption instead of a broken image. */
function PhotoPreview({ url }: { url: string }) {
  const [broken, setBroken] = useState(false);
  if (broken) return <p className="text-caption text-ink-3">{t.photo.noPreview}</p>;
  return (
    <img
      src={url}
      alt={t.photo.preview}
      onError={() => setBroken(true)}
      className="max-h-64 w-auto max-w-full self-start rounded-control border border-border object-contain"
    />
  );
}

interface PhotoStepProps {
  allowed: boolean;
  locked: boolean;
  previewUrl: string | null;
  error: string | null;
  created: InspectionView | undefined;
  onFile(file: File): void;
  onReset(): void;
}

export function PhotoStep(props: PhotoStepProps) {
  const fileId = useId();
  const cameraId = useId();
  const [over, setOver] = useState(false);
  const disabled = !props.allowed || props.locked || Boolean(props.created);
  const pick = (files: FileList | null) => {
    const file = files?.[0];
    if (file) props.onFile(file);
  };
  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setOver(false);
    if (!disabled) pick(event.dataTransfer.files);
  };
  const button =
    'inline-flex min-h-12 cursor-pointer items-center gap-2 rounded-control border border-border-strong bg-surface px-4 text-body font-medium hover:bg-surface-2';
  return (
    <div className="flex flex-col gap-3">
      {!props.created && (
        <div
          onDragOver={(e) => {
            e.preventDefault();
            if (!disabled) setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={onDrop}
          className={clsx(
            'flex flex-col items-center gap-3 rounded-card border-2 border-dashed px-4 py-6 text-center',
            over ? 'border-accent bg-accent-bg' : 'border-border-strong',
            disabled && 'opacity-60',
          )}
          data-photo-drop
        >
          <p className="text-body text-ink-2">{t.photo.drop}</p>
          <div className="flex flex-wrap justify-center gap-2">
            <label htmlFor={fileId} className={clsx(button, disabled && 'pointer-events-none')}>
              <ImageUp aria-hidden="true" size={18} />
              {t.photo.choose}
            </label>
            <input
              id={fileId}
              type="file"
              accept={PHOTO_TYPES.join(',')}
              className="sr-only"
              disabled={disabled}
              onChange={(e) => {
                pick(e.target.files);
                e.target.value = '';
              }}
              data-photo-input
            />
            <label htmlFor={cameraId} className={clsx(button, disabled && 'pointer-events-none')}>
              <Camera aria-hidden="true" size={18} />
              {t.photo.camera}
            </label>
            <input
              id={cameraId}
              type="file"
              accept={PHOTO_TYPES.join(',')}
              capture="environment"
              className="sr-only"
              disabled={disabled}
              onChange={(e) => {
                pick(e.target.files);
                e.target.value = '';
              }}
            />
          </div>
          <p className="text-caption text-ink-3" data-constant>
            {t.photo.types}
          </p>
        </div>
      )}
      {props.error && (
        <p role="alert" className="flex items-center gap-2 text-body text-danger-fg" data-photo-error>
          <TriangleAlert aria-hidden="true" size={16} />
          {props.error}
        </p>
      )}
      {props.previewUrl && <PhotoPreview key={props.previewUrl} url={props.previewUrl} />}
      {props.created ? (
        <div className="flex flex-col gap-2" data-photo-sent>
          <p className="flex flex-wrap items-center gap-x-1 text-body">
            {t.photo.sent} <IdText id={props.created.photo_id} /> · <IdText id={props.created.inspection_id} /> ·{' '}
            <DateText value={props.created.created_at} className="text-ink-2" />
          </p>
          <GenerationBadge generation={props.created.generation} />
          {!props.locked && props.allowed && (
            <div>
              <button type="button" onClick={props.onReset} className="min-h-12 text-body text-accent underline underline-offset-2">
                {t.photo.replace}
              </button>
            </div>
          )}
        </div>
      ) : null}
    </div>
  );
}

function SuggestionHint({ suggestion }: { suggestion: InspectionView['checklist'][number] | undefined }) {
  if (!suggestion) return null;
  return (
    <div className="flex flex-col gap-0.5" data-suggestion={suggestion.suggestion}>
      <p className="text-body text-ink-3 italic">
        {t.checklist.suggested} {t.checklist.answers[suggestion.suggestion].toLowerCase()}
        {suggestion.note && (
          <>
            {' · '}
            <span data-verbatim>{suggestion.note}</span>
          </>
        )}
      </p>
      <p className="text-caption text-ink-3" data-confidence>
        {t.checklist.confidence} <PlainNumber value={suggestion.model_confidence} unit="probability" /> · {t.checklist.notCalibrated}
      </p>
    </div>
  );
}

const KEY_TO_ANSWER: Record<string, Observation> = { y: 'yes', n: 'no', u: 'not_observable' };

interface ChecklistStepProps {
  inspection: InspectionView;
  answers: Answers;
  onAnswer(item: ChecklistItem, answer: Observation): void;
  disabled: boolean;
}

export function ChecklistStep({ inspection, answers, onAnswer, disabled }: ChecklistStepProps) {
  const missing = CHECKLIST_ITEMS.filter((item) => answers[item] === null);
  const onKey = (item: ChecklistItem) => (event: KeyboardEvent<HTMLInputElement>) => {
    if (disabled || event.ctrlKey || event.metaKey || event.altKey) return;
    const answer = KEY_TO_ANSWER[event.key.toLowerCase()];
    if (!answer) return;
    event.preventDefault();
    onAnswer(item, answer);
  };
  return (
    <ProvenanceScope provenance={inspection.provenance} covers="the declared model confidences of the checklist" className="flex flex-col gap-3">
      {inspection.state === 'manual_review' && (
        <div role="status" className="flex flex-col gap-1 rounded-control border border-attn-border bg-attn-bg px-3 py-2 text-attn-fg" data-manual-review>
          <p className="flex items-center gap-2 font-medium">
            <TriangleAlert aria-hidden="true" size={16} />
            {t.manual.title}
          </p>
          {inspection.manual_review_reason && (
            <p className="text-body text-ink-1" data-verbatim>
              {inspection.manual_review_reason}
            </p>
          )}
          <p className="text-body text-ink-1">{t.manual.body}</p>
        </div>
      )}
      <p className="text-body text-ink-2">{t.checklist.intro}</p>
      {CHECKLIST_ITEMS.map((item) => {
        const suggestion = inspection.checklist.find((s) => s.item === item);
        return (
          <fieldset
            key={item}
            disabled={disabled}
            className="flex flex-col gap-2 rounded-control border border-border p-3"
            data-checklist-row={item}
            data-answer={answers[item] ?? 'none'}
          >
            <legend className="px-1 text-title">{t.checklist.items[item]}</legend>
            <SuggestionHint suggestion={suggestion} />
            <div className="flex flex-wrap gap-2" role="presentation">
              {OBSERVATIONS.map((answer) => {
                const checked = answers[item] === answer;
                return (
                  <label
                    key={answer}
                    className={clsx(
                      'inline-flex min-h-12 min-w-24 cursor-pointer items-center justify-center gap-2 rounded-control border px-4 text-body font-medium',
                      'has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus',
                      checked ? 'border-ink-1 bg-ink-1 text-surface' : 'border-border-strong bg-surface text-ink-1 hover:bg-surface-2',
                      disabled && 'cursor-not-allowed opacity-60',
                    )}
                  >
                    <input
                      type="radio"
                      name={`answer-${item}`}
                      value={answer}
                      checked={checked}
                      onChange={() => onAnswer(item, answer)}
                      onKeyDown={onKey(item)}
                      className="sr-only"
                    />
                    {t.checklist.answers[answer]}
                  </label>
                );
              })}
            </div>
          </fieldset>
        );
      })}
      <p className="text-caption text-ink-3">
        <Kbd>Y</Kbd> <Kbd>N</Kbd> <Kbd>U</Kbd> {t.checklist.keys}
      </p>
      <p className="text-body text-ink-2" data-checklist-missing={missing.length}>
        {missing.length === 0 ? t.checklist.allAnswered : `${t.checklist.stillOpen} ${missing.map((i) => t.checklist.items[i]).join(', ')}`}
      </p>
      <ProvenanceFooter />
    </ProvenanceScope>
  );
}

interface ProposalStepProps {
  inspection: InspectionView;
  answers: Answers;
  apply: boolean;
  onApply(apply: boolean): void;
  disabled: boolean;
}

export function ProposalStep({ inspection, answers, apply, onApply, disabled }: ProposalStepProps) {
  const applyId = useId();
  const proposal = inspection.proposal;
  const status = proposalStatus(proposal, answers);
  if (!proposal || status === 'none') {
    return (
      <p className="text-body text-ink-2" data-proposal="none">
        {t.proposal.none}
      </p>
    );
  }
  const basis = proposal.basis.map((item) => t.checklist.items[item]).join(', ');
  const unconfirmed = proposal.basis.filter((item) => answers[item] !== 'yes').map((item) => t.checklist.items[item].toLowerCase());
  return (
    <div className="flex flex-col gap-3" data-proposal={status}>
      <div
        className={clsx(
          'flex flex-col gap-1 rounded-control border px-3 py-2',
          status === 'holds' ? 'border-border-strong bg-surface' : 'border-dashed border-border bg-surface-2 text-ink-3',
        )}
      >
        <p className="flex flex-wrap items-baseline gap-x-2">
          <span className="text-caption text-ink-3">{t.proposal.claim}</span>
          <span className={clsx('text-title', status === 'contradicted' && 'line-through decoration-1')}>
            <Figure metric={proposal.amount} label={t.proposal.claim} />
          </span>
          <span className="text-caption text-ink-3">
            {t.proposal.rule} <span data-id>{proposal.rule_id}</span>
          </span>
        </p>
        <p className="text-body" data-verbatim>
          {proposal.text}
        </p>
        <p className="text-caption text-ink-3">
          {t.proposal.rests} {basis}
        </p>
      </div>
      {status === 'pending' && <p className="text-body text-ink-2">{t.proposal.pending}</p>}
      {status === 'contradicted' && (
        <p className="text-body font-medium text-ink-2" data-proposal-contradicted>
          {t.proposal.contradicted(unconfirmed.join(', '))}
        </p>
      )}
      {status === 'holds' && (
        <div className="flex flex-col gap-1">
          <label htmlFor={applyId} className="inline-flex min-h-12 cursor-pointer items-center gap-3 text-body font-medium">
            <input
              id={applyId}
              type="checkbox"
              checked={apply}
              disabled={disabled}
              onChange={(e) => onApply(e.target.checked)}
              className="h-5 w-5 accent-[var(--accent)]"
              data-apply-compensation
            />
            {t.proposal.apply}
          </label>
          <p className="text-caption text-ink-3">{t.proposal.applyHint}</p>
        </div>
      )}
    </div>
  );
}

export function InspectionResult({ result }: { result: ConfirmResult }) {
  const r = t.result;
  const { claim, decision, outbox_message: message, inspection } = result;
  return (
    <div className="flex flex-col gap-3" data-inspection-result={claim ? 'claim' : 'no_claim'}>
      {claim ? (
        <p className="flex flex-wrap items-baseline gap-x-1 text-body" data-claim>
          <span className="font-medium">{r.claim}</span>
          <Figure metric={claim.amount} label={r.claim} />
          {r.attachedTo} <IdText id={decision.decision_id} /> · {r.confirmedBy} <strong>{claim.confirmed_by}</strong>
          {message && (
            <>
              {' '}
              · {r.message} <IdText id={message.message_id} />
            </>
          )}
        </p>
      ) : (
        <p className="text-body font-medium">{r.noClaim}</p>
      )}
      {inspection.confirmed_observations && (
        <div className="flex flex-col gap-1">
          <h3 className="text-title">{r.observations}</h3>
          <ul className="flex flex-wrap gap-2">
            {inspection.confirmed_observations.map((o) => (
              <li key={o.item} className="rounded-chip border border-border px-2 py-1 text-dense" data-confirmed={`${o.item}:${o.observation}`}>
                {t.checklist.items[o.item]}: <strong>{t.checklist.answers[o.observation]}</strong>
              </li>
            ))}
          </ul>
          {inspection.confirmed_by && inspection.confirmed_at && (
            <p className="text-caption text-ink-3">
              {r.confirmedBy} {inspection.confirmed_by} · <DateText value={inspection.confirmed_at} />
            </p>
          )}
        </div>
      )}
      {message ? (
        <MessagePreview message={message} stored={message.body} title={r.messageTitle} />
      ) : (
        <p className="text-body text-ink-3">{r.noMessage}</p>
      )}
      <div>
        <Link to={`/returns/${encodeURIComponent(decision.return_id)}`} className="text-body text-accent underline underline-offset-2">
          {r.openCase}
        </Link>
      </div>
    </div>
  );
}
