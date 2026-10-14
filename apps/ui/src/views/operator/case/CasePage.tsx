// Case card /returns/:returnId (US-302..310, US-607; docs/ui-design.md 6.2 to 6.4): the evidence
// column with the case hero on the left, the sticky decision rail on the right (first on narrow
// screens). Deep links: ?action=approve|override|reject|review|evidence, ?link=, ?focus=, ?from=.
import { useCallback, useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { isApiError } from '@/api/errors';
import type { CaseCard, DecisionResult, EntityRef, Lang, LinkDisputeResponse, RecomputeResponse } from '@/api/models';
import { useQueue } from '@/api/queries';
import { useHotkeys } from '@/app/hotkeys';
import { usePresent } from '@/app/display';
import { usePersona } from '@/app/persona';
import { ErrorState, LoadingBlock, READ_ONLY, useRoleGate } from '@/components/feedback';
import { actionPlan, type ActionPlan } from '@/domain/decision';
import { refAnchorId } from '@/domain/refs';
import { CaseAssistant } from '@/views/agent';
import { OPERATOR } from '../copy';
import { cardChanges, roleNote } from '../format';
import { applyOverlay, overlayStore, recordDecision, recordRecompute, useCaseView } from '../state/caseOverlay';
import { usePrefetchCase } from '../state/prefetch';
import { noticeStore, sendLangStore, useAutoAdvance, useSendLang } from '../state/prefs';
import { parseTab, tabQuery, type QueueTab } from '../state/queueParams';
import { approveBody } from '../state/requests';
import { ActionNoticeBar } from './ActionNoticeBar';
import { CaseUiContext, type CaseGates, type CaseUi, type SheetRequest } from './context';
import { CaseBanners } from './header/CaseBanners';
import { CaseHeader, type CaseNav } from './header/CaseHeader';
import { CaseHero } from './hero/CaseHero';
import { DecisionRail } from './rail/DecisionRail';
import { EvidenceColumn } from './sections/EvidenceColumn';
import { ApproveRestrictionSheet, OverrideSheet, RejectSheet, ReviewSheet } from './sheets/DecisionSheets';
import { AddEvidenceSheet, DisputeLinkSheet } from './sheets/EvidenceSheets';
import { RecomputeComparison } from './sheets/RecomputeComparison';
import { useDecisionAction } from './sheets/useDecisionAction';

const DRAFT_POLL_MS = 3_000;
const DRAFT_POLL_LIMIT_MS = 60_000;
const VIEW_REFRESH_MS = 60_000;
const PULSE_MS = 1_600;
const PREFETCH_NEXT = 2;
const PULSE_CLASSES = ['ring-2', 'ring-accent', 'ring-offset-2'];
const DEEP_ACTIONS = new Set(['approve', 'override', 'reject', 'review', 'evidence']);

function pulse(element: HTMLElement | null): void {
  if (!element) return;
  element.scrollIntoView({ block: 'center', behavior: 'smooth' });
  element.classList.add(...PULSE_CLASSES);
  setTimeout(() => element.classList.remove(...PULSE_CLASSES), PULSE_MS);
}

/** The ids of the `?from` view, for the `[` and `]` loop (shares the queue's cache key). */
function useCaseNav(returnId: string, from: QueueTab | null): CaseNav | null {
  const query = from ? tabQuery(from) : {};
  const list = useQueue(query, { enabled: from !== null });
  const prefetch = usePrefetchCase();
  const items = list.data?.items ?? [];
  const index = items.findIndex((i) => i.return_id === returnId);
  const next = index >= 0 ? items.slice(index + 1, index + 1 + PREFETCH_NEXT).map((i) => i.return_id) : [];
  const nextKey = next.join();
  useEffect(() => {
    if (nextKey) nextKey.split(',').forEach(prefetch);
  }, [nextKey, prefetch]);
  if (!from || !list.data || index < 0) return null;
  return {
    tabLabel: OPERATOR.queue.tabs[from] ?? from,
    position: index + 1,
    total: list.data.total,
    prev: items[index - 1]?.return_id ?? null,
    next: items[index + 1]?.return_id ?? null,
    from,
    provenance: list.data.provenance,
  };
}

/** The sheet a `?action=` deep link opens, when the decision state allows that action. */
function deepLinkSheet(action: string | null, plan: ActionPlan): Omit<SheetRequest, 'shown'> | null {
  if (!action || !DEEP_ACTIONS.has(action)) return null;
  if (action === 'evidence') return { kind: 'evidence' };
  if (action === 'approve') {
    if (plan.primary === 'approve_restriction') return { kind: 'approve' };
    if (plan.primary === 'confirm' || plan.primary === 'confirm_guarantee') return { kind: 'confirm' };
    return null;
  }
  if (action === 'review') return plan.primary === 'review' || plan.secondary.includes('lift') ? { kind: 'review' } : null;
  return plan.secondary.includes(action as 'override' | 'reject') ? { kind: action as 'override' | 'reject' } : null;
}

function resultText(result: DecisionResult): string {
  const d = result.decision;
  const t = OPERATOR.result;
  const word =
    d.state === 'approved' ? t.approved : d.state === 'rejected' ? t.rejected : d.revision_trigger === 'review' ? t.lifted : t.overridden;
  return `${word} ${d.approver ?? ''}`;
}

export default function CasePage() {
  const { returnId = '' } = useParams();
  const { query, card, sentMessage } = useCaseView(returnId);

  if (!card) {
    if (query.isError) {
      const notFound = isApiError(query.error) && query.error.code === 'not_found';
      return (
        <div className="mx-auto flex max-w-xl flex-col gap-4 py-8">
          <h1 className="text-page">{OPERATOR.titles.case}</h1>
          <ErrorState
            error={query.error}
            title={notFound ? OPERATOR.case.notFound(returnId) : undefined}
            onRetry={notFound ? undefined : () => void query.refetch()}
          />
          <Link to="/queue" className="text-accent underline underline-offset-2">
            {OPERATOR.case.backToQueue}
          </Link>
        </div>
      );
    }
    return (
      <div className="mx-auto max-w-content">
        <LoadingBlock rows={12} />
      </div>
    );
  }
  return <CaseScreen key={returnId} card={card} sentMessage={sentMessage} refetch={query.refetch} />;
}

interface CaseScreenProps {
  card: CaseCard;
  sentMessage: CaseUi['sentMessage'];
  refetch: ReturnType<typeof useCaseView>['query']['refetch'];
}

function CaseScreen({ card, sentMessage, refetch }: CaseScreenProps) {
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const persona = usePersona();
  const sendLang = useSendLang();
  const autoAdvance = useAutoAdvance();
  const from = parseTab(params.get('from'));
  const nav = useCaseNav(card.return_id, from);

  const plan = actionPlan(card.decision);
  // Every sheet carries the decision it was opened on (`shown`, UI38): its request names that
  // version, and a newer card blocks the confirm until "Load the new version".
  const [sheet, setSheet] = useState<SheetRequest | null>(() => {
    const request = deepLinkSheet(params.get('action'), plan);
    return request ? { ...request, shown: card.decision } : null;
  });
  const [changes, setChanges] = useState<string[] | null>(null);
  const [recompute, setRecompute] = useState<{ response: RecomputeResponse; also: string[] } | null>(null);
  const [selectedLinkId, setSelectedLinkId] = useState<string | null>(() => params.get('link'));
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [seenDecisionId, setSeenDecisionId] = useState(card.decision.decision_id);
  const [draftTimedOut, setDraftTimedOut] = useState(false);
  const cardRef = useRef(card);
  useEffect(() => {
    cardRef.current = card;
  });

  // Role gates: disabled with the reason, never hidden (docs/ui-design.md 5.8).
  const decide = useRoleGate('POST /api/returns/{return_id}/decision');
  const evidence = useRoleGate('POST /api/returns/{return_id}/evidence');
  const dispute = useRoleGate('POST /api/links/{link_id}/dispute');
  const regenerate = useRoleGate('POST /api/returns/{return_id}/regenerate');
  const a = OPERATOR.actions;
  const note = (allowed: boolean, reason: string | null, action: Parameters<typeof roleNote>[0], subject: string) =>
    allowed ? null : READ_ONLY ? reason : roleNote(action, subject, persona.role);
  const gates: CaseGates = {
    decide,
    evidence,
    dispute,
    regenerate,
    decideNote: note(decide.allowed, decide.reason, 'POST /api/returns/{return_id}/decision', a.decisionsSubject),
    evidenceNote: note(evidence.allowed, evidence.reason, 'POST /api/returns/{return_id}/evidence', a.evidenceSubject),
    regenerateNote: note(regenerate.allowed, regenerate.reason, 'POST /api/returns/{return_id}/regenerate', a.regenerateSubject),
  };
  const draftPending = card.draft_status === 'pending';

  // A pending draft polls every 3 s for up to 60 s, then offers Regenerate (6.2.4).
  useEffect(() => {
    if (!draftPending) return;
    const started = Date.now();
    const timer = setInterval(() => {
      if (Date.now() - started > DRAFT_POLL_LIMIT_MS) {
        setDraftTimedOut(true);
        clearInterval(timer);
        return;
      }
      void refetch();
    }, DRAFT_POLL_MS);
    return () => clearInterval(timer);
  }, [draftPending, refetch]);

  // The open card refetches every 60 s (and on focus): a newer version shows a banner (6.4).
  useEffect(() => {
    const timer = setInterval(() => void refetch(), VIEW_REFRESH_MS);
    return () => clearInterval(timer);
  }, [refetch]);

  const fresh = useCallback(async (): Promise<CaseCard | null> => {
    const res = await refetch();
    return res.data ? applyOverlay(res.data, overlayStore.get()[card.return_id]) : null;
  }, [refetch, card.return_id]);

  const openSheet = useCallback(
    (request: SheetRequest) => {
      const shown = request.shown ?? cardRef.current.decision;
      const linkId = request.kind === 'dispute' && !request.linkId ? (selectedLinkId ?? undefined) : request.linkId;
      setSheet({ ...request, linkId, shown });
      setChanges(null);
      // Every sheet first loads the card again: a newer version shows the stale state (6.3), since
      // the sheet compares the card with the decision it was opened on.
      void fresh();
    },
    [fresh, selectedLinkId],
  );

  /** "Load the new version": refetch, list what changed, and move an open sheet onto it (6.4). */
  const reload = useCallback(() => {
    const before = cardRef.current;
    void fresh().then((now) => {
      setChanges(now ? cardChanges(before, now) : []);
      if (!now) return;
      setSeenDecisionId(now.decision.decision_id);
      setSheet((open) => (open ? { ...open, shown: now.decision } : open));
    });
  }, [fresh]);

  const goTo = useCallback((id: string) => void navigate(from ? `/returns/${id}?from=${from}` : `/returns/${id}`), [navigate, from]);

  const onDecisionDone = (result: DecisionResult) => {
    recordDecision(result.decision, result.outbox_message);
    setSeenDecisionId(result.decision.decision_id);
    noticeStore.set({
      returnId: result.decision.return_id,
      text: resultText(result),
      messageId: result.outbox_message.message_id,
      auditEntryIds: [result.audit_entry.entry_id],
    });
    setSheet(null);
    // Auto-advance inside the `?from` view (6.3.3); there is no undo, corrections are new decisions.
    if (from && autoAdvance && nav?.next) goTo(nav.next);
  };
  const confirmAction = useDecisionAction(card, onDecisionDone);

  const showRecompute = (response: RecomputeResponse, also: string[]) => {
    setSheet(null);
    setRecompute({ response, also });
  };
  const onEvidenceDone = (response: RecomputeResponse, explained: string[]) => {
    recordRecompute(response, Object.fromEntries(explained.map((id) => [id, { explained_by_evidence_id: response.evidence_id }])));
    noticeStore.set({
      returnId: response.return_id,
      text: `${OPERATOR.result.evidenceAdded} ${persona.id}`,
      messageId: null,
      auditEntryIds: [...response.audit_entry_ids],
    });
    showRecompute(response, []);
  };
  const onDisputeDone = (response: LinkDisputeResponse) => {
    const also = response.affected_return_ids.filter((id) => id !== card.return_id);
    noticeStore.set({
      returnId: card.return_id,
      text: `${OPERATOR.result.disputed} ${persona.id}`,
      messageId: null,
      auditEntryIds: response.recompute ? [...response.recompute.audit_entry_ids] : [],
    });
    if (response.recompute && response.recompute.return_id === card.return_id) {
      recordRecompute(response.recompute, { [response.link.link_id]: response.link });
      showRecompute(response.recompute, also);
    } else {
      setSheet(null);
    }
  };

  const selectRef = useCallback((ref: Pick<EntityRef, 'kind' | 'id'>) => {
    const shown = cardRef.current;
    // The decision panel is the authority (rule 1): "Go to the decision panel" and a cited decision
    // or return of this card land on the rail; another version or return opens its own page.
    if (ref.kind === 'decision' && ref.id !== shown.decision.decision_id) {
      void navigate(`/returns/${encodeURIComponent(shown.return_id)}/versions/${encodeURIComponent(ref.id)}`);
      return;
    }
    if (ref.kind === 'return' && ref.id !== shown.return_id) {
      void navigate(`/returns/${encodeURIComponent(ref.id)}`);
      return;
    }
    if (ref.kind === 'decision' || ref.kind === 'return') {
      const rail = document.querySelector<HTMLElement>('[data-decision-rail]');
      requestAnimationFrame(() => {
        pulse(rail);
        rail?.focus({ preventScroll: true });
      });
      return;
    }
    if (ref.kind === 'link') setSelectedLinkId(ref.id);
    const target =
      document.getElementById(refAnchorId(ref)) ??
      (ref.kind === 'customer' && cardRef.current.linked_accounts
        ? document.getElementById(refAnchorId({ kind: 'cluster', id: cardRef.current.linked_accounts.cluster_id }))
        : null) ??
      (ref.kind === 'variant' || ref.kind === 'anomaly' ? document.querySelector<HTMLElement>('[data-product-anomaly]') : null);
    // The link row renders after the selection: pulse on the next frame.
    requestAnimationFrame(() => pulse(document.getElementById(refAnchorId(ref)) ?? target));
  }, [navigate]);

  const ui: CaseUi = {
    card,
    openSheet,
    selectedLinkId,
    selectLink: setSelectedLinkId,
    selectRef,
    sendLang,
    setSendLang: (lang: Lang) => sendLangStore.set(lang),
    gates,
    sentMessage,
    draftPending,
  };

  // Presenter mode on a wide screen opens a case with the grid under the banner: the sticky rail
  // then has the whole viewport, so the approval and the terms table are on screen (UI22).
  const present = usePresent();
  const presented = useRef(false);
  useEffect(() => {
    if (!present || presented.current || params.has('focus') || params.has('link')) return;
    presented.current = true;
    if (!window.matchMedia?.('(min-width: 1280px)').matches) return;
    requestAnimationFrame(() => {
      const rail = document.querySelector<HTMLElement>('[data-decision-rail]');
      if (!rail) return;
      const stickyTop = Number.parseFloat(getComputedStyle(rail).top) || 0;
      window.scrollTo({ top: Math.max(0, rail.getBoundingClientRect().top + window.scrollY - stickyTop) });
    });
  }, [present, params]);

  // Deep links (docs/ui-design.md 3.2): ?action= opened the sheet at first render (initial state),
  // ?focus= and ?link= scroll to a section or a link; the action parameter is then dropped.
  const deepLinked = useRef(false);
  useEffect(() => {
    if (deepLinked.current) return;
    deepLinked.current = true;
    const focus = params.get('focus');
    const link = params.get('link');
    if (focus)
      requestAnimationFrame(() =>
        pulse(document.getElementById(`section-${focus}`) ?? document.querySelector(`[data-section="${focus}"]`)),
      );
    // The selected link's detail panel (with "Explain with evidence…") is what the link deep link
    // is for: it comes into view, under the banner (UI33).
    if (link)
      requestAnimationFrame(() => {
        pulse(document.getElementById(refAnchorId({ kind: 'link', id: link })));
        requestAnimationFrame(() =>
          Array.from(document.querySelectorAll<HTMLElement>('[data-link-detail]'))
            .find((el) => el.dataset.linkDetail === link)
            ?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }),
        );
      });
    if (params.has('action')) {
      // A deep-linked sheet loads the card again like any other (UI38).
      void fresh();
      const next = new URLSearchParams(params);
      next.delete('action');
      setParams(next, { replace: true });
    }
  }, [params, setParams, fresh]);

  // A navigation is a transition: the previous card can still be on screen for a moment after
  // the URL changed. Its shortcuts must not act on a case that is no longer the URL's.
  const stale = () => !window.location.pathname.endsWith(`/returns/${card.return_id}`);
  const busy = () => sheet !== null || assistantOpen || recompute !== null || stale();
  const can = (allowed: boolean) => allowed && !busy();
  useHotkeys({
    '[': () => !busy() && nav?.prev && goTo(nav.prev),
    ']': () => !busy() && nav?.next && goTo(nav.next),
    a: () => {
      if (!can(decide.allowed) || draftPending) return;
      if (plan.primary === 'approve_restriction') openSheet({ kind: 'approve' });
      else if (plan.primary === 'confirm' || plan.primary === 'confirm_guarantee') {
        setSheet({ kind: 'confirm', shown: card.decision });
        void fresh();
      }
      else if (plan.primary === 'review') openSheet({ kind: 'review' });
    },
    o: () => can(decide.allowed) && !draftPending && plan.secondary.includes('override') && openSheet({ kind: 'override' }),
    r: () => can(decide.allowed) && !draftPending && plan.secondary.includes('reject') && openSheet({ kind: 'reject' }),
    l: () =>
      can(decide.allowed) &&
      !draftPending &&
      (plan.primary === 'review' || plan.secondary.includes('lift')) &&
      openSheet({ kind: 'review' }),
    e: () => can(evidence.allowed) && openSheet({ kind: 'evidence' }),
    d: () => can(dispute.allowed) && card.linked_accounts && openSheet({ kind: 'dispute' }),
    i: () => !busy() && setAssistantOpen(true),
    m: () => !busy() && sendLangStore.set(sendLang === 'it' ? 'en' : 'it'),
  });

  const shown = sheet?.shown ?? card.decision;
  const sheetProps = {
    onOpenChange: (open: boolean) => !open && setSheet(null),
    onDone: onDecisionDone,
    onReload: reload,
    changes,
    shown,
  };
  const confirmNewer = sheet?.kind === 'confirm' && shown.decision_id !== card.decision.decision_id ? shown : null;
  const changedTo = card.decision.decision_id !== seenDecisionId ? card.decision.decision_id : null;

  return (
    <CaseUiContext.Provider value={ui}>
      <div className="mx-auto flex max-w-content flex-col gap-4" data-page="case" data-return={card.return_id}>
        <ActionNoticeBar />
        <CaseHeader nav={nav} onAsk={() => setAssistantOpen(true)} />
        <CaseBanners
          changedTo={changedTo}
          onAcknowledge={() => setSeenDecisionId(card.decision.decision_id)}
          draftTimedOut={draftTimedOut}
        />
        <div className="grid gap-6 xl:grid-cols-12">
          <div className="order-2 flex min-w-0 flex-col gap-4 xl:order-1 xl:col-span-7">
            <CaseHero card={card} />
            <EvidenceColumn card={card} draftTimedOut={draftTimedOut} onReload={reload} />
          </div>
          <div className="order-1 min-w-0 xl:order-2 xl:col-span-5">
            <DecisionRail
              plan={plan}
              confirmOpen={sheet?.kind === 'confirm'}
              onConfirmOpenChange={(open) => {
                if (!open) confirmAction.reset();
                setSheet(open ? { kind: 'confirm', shown: cardRef.current.decision } : null);
                if (open) void fresh();
              }}
              onConfirm={() => {
                if (confirmNewer) return;
                confirmAction.submit(approveBody(shown, sendLang, persona.id));
              }}
              confirmStale={confirmNewer}
              confirming={confirmAction.pending}
              confirmError={confirmAction.error}
              confirmMismatch={confirmAction.mismatch}
              onReload={reload}
            />
          </div>
        </div>
        {sheet?.kind === 'approve' && <ApproveRestrictionSheet open {...sheetProps} />}
        {sheet?.kind === 'override' && <OverrideSheet open {...sheetProps} prefill={sheet.prefill} />}
        {sheet?.kind === 'reject' && <RejectSheet open {...sheetProps} />}
        {sheet?.kind === 'review' && <ReviewSheet open {...sheetProps} />}
        {sheet?.kind === 'evidence' && <AddEvidenceSheet open {...sheetProps} linkId={sheet.linkId} onDone={onEvidenceDone} />}
        {sheet?.kind === 'dispute' && <DisputeLinkSheet open {...sheetProps} linkId={sheet.linkId} onDone={onDisputeDone} />}
        {recompute && (
          <RecomputeComparison
            recompute={recompute.response}
            alsoRecomputed={recompute.also}
            versions={card.revisions.length}
            onOpenVersion={() => {
              setSeenDecisionId(recompute.response.decision.decision_id);
              setRecompute(null);
              window.scrollTo({ top: 0 });
            }}
          />
        )}
        <CaseAssistant returnId={card.return_id} open={assistantOpen} onOpenChange={setAssistantOpen} onRefSelect={selectRef} />
      </div>
    </CaseUiContext.Provider>
  );
}
