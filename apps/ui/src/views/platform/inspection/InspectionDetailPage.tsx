// Inspect a return /inspection/:returnId (US-501..503, Could; docs/ui-design.md 9.2, spec 8.8).
// A stepper: the return, a photo (POST /api/inspections, 201), the suggested checklist that Ahmed
// answers himself, the compensation proposal (only while his answers confirm its basis, applied
// only when he ticks it) and the confirmation (POST /api/inspections/{id}/confirm, Ctrl+Enter).
// When the system is unsure it stops (manual review): no proposal, observations only.
import clsx from 'clsx';
import { ArrowLeft, Check } from 'lucide-react';
import { useEffect, useId, useRef, useState } from 'react';
import { Link, useParams } from 'react-router';
import { API_MODE } from '@/api/adapters';
import { useConfirmInspection, useCreateInspection } from '@/api/mutations';
import { useCase, useReadyz } from '@/api/queries';
import { usePersona } from '@/app/persona';
import { PathBadge } from '@/components/decision';
import { ErrorState, InlineNotice, LoadingBlock, useReadOnly } from '@/components/feedback';
import { IdText, PlainNumber, ProvenanceFooter, ProvenanceScope } from '@/components/provenance';
import { Button, Kbd } from '@/components/ui';
import { PLATFORM } from '../copy';
import { useWriteGate } from '../shared/gate';
import { PageHeader } from '../shared/layout';
import { INSPECTION } from './copy';
import {
  answersComplete,
  checkPhoto,
  confirmProblem,
  confirmRequest,
  createProblem,
  emptyAnswers,
  stripDataUrl,
  type Answers,
  type PhotoType,
} from './inspection';
import { ChecklistStep, InspectionResult, PhotoStep, ProblemNotice, ProposalStep, StepPanel } from './steps';

const t = INSPECTION;
type StepKey = keyof typeof INSPECTION.steps;
type StepState = 'waiting' | 'current' | 'done';

function StepIndicator({ states }: { states: Record<StepKey, StepState> }) {
  return (
    <ol className="flex list-none flex-wrap gap-2" aria-label={t.stepOf} data-stepper>
      {(Object.keys(t.steps) as StepKey[]).map((key) => (
        <li
          key={key}
          aria-current={states[key] === 'current' ? 'step' : undefined}
          className={clsx(
            'inline-flex min-h-9 items-center gap-1 rounded-full border px-3 text-dense',
            states[key] === 'current' && 'border-accent bg-accent-bg font-medium text-ink-1',
            states[key] === 'done' && 'border-border bg-surface text-ink-2',
            states[key] === 'waiting' && 'border-dashed border-border text-ink-3',
          )}
          data-step-indicator={key}
          data-state={states[key]}
        >
          {states[key] === 'done' && <Check aria-hidden="true" size={14} />}
          {t.steps[key]}
        </li>
      ))}
    </ol>
  );
}

function ReturnSummary({ returnId }: { returnId: string }) {
  const card = useCase(returnId);
  if (card.isPending) return <LoadingBlock rows={2} />;
  if (card.isError) return <ErrorState error={card.error} onRetry={() => void card.refetch()} />;
  const c = card.data;
  return (
    <ProvenanceScope provenance={c.provenance} covers="the item and the request" className="flex flex-col gap-2 rounded-card border border-border bg-surface p-4">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <IdText id={c.return_id} className="font-medium" />
        <span className="font-medium">{c.display_name ?? c.customer_id}</span>
        <IdText id={c.customer_id} className="text-ink-3" />
        <PathBadge path={c.path} />
        <span className="text-body text-ink-2">{t.reasons[c.request.reason] ?? c.request.reason}</span>
      </p>
      <p className="flex flex-wrap items-center gap-x-1 text-body text-ink-2">
        <span className="text-caption text-ink-3">{t.item}</span> {c.item.category} · <IdText id={c.item.variant_id} /> · {c.item.size} ·{' '}
        <PlainNumber value={c.item.price_eur} unit="eur" /> <ProvenanceFooter compact />
      </p>
      {c.path === 'guarantee' && (
        <p className="text-body text-ink-1" data-guarantee-note>
          <span data-legal-ref>{t.guaranteeNote}</span>
        </p>
      )}
    </ProvenanceScope>
  );
}

function InspectionFlow({ returnId }: { returnId: string }) {
  const persona = usePersona();
  // The role and the read-only public demo (/readyz public_read_only) gate both writes.
  const createGate = useWriteGate('POST /api/inspections');
  const confirmGate = useWriteGate('POST /api/inspections/{inspection_id}/confirm');
  const [photo, setPhoto] = useState<{ mediaType: PhotoType; base64: string } | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [photoError, setPhotoError] = useState<string | null>(null);
  const [answers, setAnswers] = useState<Answers>(emptyAnswers);
  const [apply, setApply] = useState(false);
  const [note, setNote] = useState('');
  const noteId = useId();

  const create = useCreateInspection();
  const created = create.data;
  const problem = created ? createProblem(returnId, created) : null;
  const inspection = created && !problem ? created : undefined;
  const confirm = useConfirmInspection(inspection?.inspection_id ?? '');
  const result = confirm.data;
  const sentApply = confirm.variables?.apply_compensation ?? false;
  const resultProblem = result && inspection ? confirmProblem(inspection.inspection_id, sentApply, result) : null;
  const locked = Boolean(result);
  const complete = answersComplete(answers);
  const canConfirm = confirmGate.allowed && Boolean(inspection) && complete && !confirm.isPending && !result;

  useEffect(
    () => () => {
      if (previewUrl) URL.revokeObjectURL(previewUrl);
    },
    [previewUrl],
  );

  const onFile = (file: File) => {
    const check = checkPhoto(file);
    if (!check.ok) {
      setPhotoError(check.reason === 'type' ? t.photo.refusedType : t.photo.refusedSize);
      return;
    }
    setPhotoError(null);
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result !== 'string') {
        setPhotoError(t.photo.unreadable);
        return;
      }
      setPhoto({ mediaType: check.mediaType, base64: stripDataUrl(reader.result) });
      try {
        setPreviewUrl(URL.createObjectURL(file));
      } catch {
        setPreviewUrl(null);
      }
    };
    reader.onerror = () => setPhotoError(t.photo.unreadable);
    reader.readAsDataURL(file);
  };

  const send = () => {
    if (!photo) return;
    create.mutate({ return_id: returnId, inspector: persona.id, photo: { media_type: photo.mediaType, data_base64: photo.base64 } });
  };

  const reset = () => {
    create.reset();
    confirm.reset();
    setPhoto(null);
    setPreviewUrl(null);
    setAnswers(emptyAnswers());
    setApply(false);
  };

  const doConfirm = () => {
    if (!canConfirm || !inspection) return;
    confirm.mutate(confirmRequest(persona.id, answers, { proposal: inspection.proposal, apply, note }));
  };

  // Ctrl/Cmd+Enter confirms; a bare Enter never does (docs/ui-design.md 3.5).
  const confirmRef = useRef(doConfirm);
  useEffect(() => {
    confirmRef.current = doConfirm;
  });
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        confirmRef.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  const states: Record<StepKey, StepState> = {
    find: 'done',
    photo: inspection ? 'done' : 'current',
    checklist: !inspection ? 'waiting' : complete ? 'done' : 'current',
    proposal: !inspection ? 'waiting' : result ? 'done' : complete ? 'current' : 'waiting',
    confirm: !inspection || !complete ? 'waiting' : result ? 'done' : 'current',
  };
  const disabledReason = !inspection ? t.confirm.needPhoto : !complete ? t.confirm.needAnswers : null;

  return (
    <div className="flex flex-col gap-4" data-inspection-flow={inspection?.state ?? 'new'}>
      <StepIndicator states={states} />
      <StepPanel step="photo" title={t.steps.photo} state={states.photo}>
        <PhotoStep
          allowed={createGate.allowed}
          locked={locked}
          previewUrl={previewUrl}
          error={photoError}
          created={inspection}
          onFile={onFile}
          onReset={reset}
        />
        {!created && (
          <div className="flex flex-col gap-1">
            <Button
              variant="primary"
              className="min-h-12 self-start px-5"
              disabled={!createGate.allowed || !photo || create.isPending}
              onClick={send}
              data-send-photo
            >
              {create.isPending ? t.photo.sending : t.photo.send}
            </Button>
            {createGate.reason && (
              <p className="text-caption text-ink-3" data-role-reason>
                {createGate.reason}
              </p>
            )}
          </div>
        )}
        {create.isError && <ErrorState error={create.error} onRetry={send} />}
        {problem && (
          <>
            <ProblemNotice problem={problem} mock={API_MODE === 'mock'} />
            <div>
              <Button onClick={reset}>{t.photo.replace}</Button>
            </div>
          </>
        )}
      </StepPanel>

      <StepPanel step="checklist" title={t.steps.checklist} state={states.checklist}>
        {!inspection && <p className="text-body text-ink-3">{t.waiting.checklist}</p>}
        {inspection && (
          <ChecklistStep
            inspection={inspection}
            answers={answers}
            disabled={!confirmGate.allowed || locked}
            onAnswer={(item, answer) => setAnswers((prev) => ({ ...prev, [item]: answer }))}
          />
        )}
      </StepPanel>

      <StepPanel step="proposal" title={t.steps.proposal} state={states.proposal}>
        {!inspection && <p className="text-body text-ink-3">{t.waiting.proposal}</p>}
        {inspection && (
          <ProposalStep inspection={inspection} answers={answers} apply={apply} onApply={setApply} disabled={!confirmGate.allowed || locked} />
        )}
      </StepPanel>

      <StepPanel step="confirm" title={t.steps.confirm} state={states.confirm}>
        {result ? (
          resultProblem ? (
            <ProblemNotice problem={resultProblem} mock={API_MODE === 'mock'} />
          ) : (
            <>
              <p className="sr-only" role="status">
                {t.result.title}
              </p>
              <InspectionResult result={result} />
            </>
          )
        ) : (
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1">
              <label htmlFor={noteId} className="text-caption text-ink-2">
                {t.confirm.note}
              </label>
              <textarea
                id={noteId}
                value={note}
                onChange={(e) => setNote(e.target.value)}
                disabled={!confirmGate.allowed || !inspection}
                rows={2}
                className="w-full max-w-xl rounded-control border border-border-strong bg-surface px-3 py-2 text-body disabled:opacity-60"
              />
            </div>
            <div className="flex flex-wrap items-center gap-3">
              <Button variant="primary" className="min-h-12 px-5" disabled={!canConfirm} onClick={doConfirm} data-confirm-inspection>
                {confirm.isPending ? t.confirm.sending : t.confirm.button}
                <Kbd>{t.confirm.keys}</Kbd>
              </Button>
            </div>
            {confirmGate.reason ? (
              <p className="text-caption text-ink-3" data-role-reason>
                {confirmGate.reason}
              </p>
            ) : (
              disabledReason && <p className="text-caption text-ink-3">{disabledReason}</p>
            )}
            {confirm.isError && <ErrorState error={confirm.error} onRetry={doConfirm} />}
          </div>
        )}
      </StepPanel>
    </div>
  );
}

export default function InspectionDetailPage() {
  const { returnId = '' } = useParams();
  const persona = usePersona();
  const readOnly = useReadOnly();
  const cached = useReadyz().data?.mode === 'cached';
  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-4" data-page="inspection-detail">
      <PageHeader
        title={PLATFORM.titles.inspectionDetail}
        stories={t.stories}
        aside={
          <Link to="/inspection" className="inline-flex min-h-12 items-center gap-1 text-body text-accent underline underline-offset-2">
            <ArrowLeft aria-hidden="true" size={16} />
            {t.back}
          </Link>
        }
      />
      <ReturnSummary returnId={returnId} />
      <p className="rounded-control border border-border bg-surface-2 px-3 py-2 text-body text-ink-1" data-inspection-note>
        {t.note}
      </p>
      {readOnly ? (
        <InlineNotice>
          <p data-inspection-read-only>{t.publicReadOnly}</p>
        </InlineNotice>
      ) : (
        persona.role !== 'warehouse' && (
          <InlineNotice>
            <p>{t.readOnly(`${persona.id} (${persona.role})`)}</p>
          </InlineNotice>
        )
      )}
      {cached && (
        <div data-inspection-cached>
          <InlineNotice tone="attention">
            <p>{t.cached}</p>
          </InlineNotice>
        </div>
      )}
      <InspectionFlow key={returnId} returnId={returnId} />
    </div>
  );
}
