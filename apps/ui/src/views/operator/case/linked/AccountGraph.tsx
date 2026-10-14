// The linked-accounts graph (docs/ui-design.md 4.8, 5.5) with Cytoscape.js (MIT, core layouts
// only): deterministic layouts, no physics, no wheel zoom, colours from the tokens. The canvas is
// supplementary: LinkTable under it is the accessible primary view, and selection stays in sync.
// Cytoscape loads on demand, so the case card does not carry it in its first chunk.
import type { Core, ElementDefinition, StylesheetJson } from 'cytoscape';
import { Maximize2, ZoomIn, ZoomOut } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { LinkedAccountsView } from '@/api/models';
import { usePresent } from '@/app/display';
import { Button } from '@/components/ui';
import { OPERATOR } from '../../copy';
import { accountsLabel } from '../../format';
import { graphLayout, linkState, matchesKinds, orderedAccounts, strongestKind, type IdentifierKind } from './linkModel';

const GRAPH_HEIGHT_PX = 300;
/** Presenter mode: the graph is the centre of beat 2, read on a projector (UI25). */
const GRAPH_HEIGHT_PRESENT_PX = 440;
const ZOOM_STEP = 1.25;
const FIT_PADDING = 28;
/** The typography floor (docs/ui-design.md 4.4): labels never render below it, whatever the zoom. */
const LABEL_FLOOR_PX = 11;
const LABEL_FLOOR_PRESENT_PX = 14;

/** Root font size: presenter mode raises it, and the canvas follows (UI25). */
function remScale(): number {
  const size = Number.parseFloat(getComputedStyle(document.documentElement).fontSize);
  return Number.isFinite(size) && size > 0 ? size / 16 : 1;
}

/** Label sizes so that, at the current zoom, they render at least at the floor (UI25). */
function fitLabels(cy: Core, floorPx: number): void {
  const size = floorPx / Math.min(1, cy.zoom());
  cy.style().selector('node').style('font-size', size).selector('edge').style('font-size', size).update();
}

function canvasSupported(): boolean {
  try {
    const canvas = document.createElement('canvas');
    return Boolean(canvas.getContext && canvas.getContext('2d'));
  } catch {
    return false;
  }
}

function token(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim() || '#888';
}

/** Line style by the strongest identifier kind (docs/ui-design.md 4.8). */
const KIND_STYLE: Record<IdentifierKind, { lineStyle: 'solid' | 'dashed' | 'dotted'; dash?: number[]; thin?: boolean }> = {
  card: { lineStyle: 'solid' },
  email: { lineStyle: 'solid', thin: true },
  device: { lineStyle: 'dashed', dash: [12, 4] },
  address: { lineStyle: 'dashed', dash: [6, 4] },
  phone: { lineStyle: 'dashed', dash: [8, 3, 2, 3] },
  ip_prefix: { lineStyle: 'dotted' },
};

function stylesheet(): StylesheetJson {
  const ink1 = token('ink-1');
  const ink2 = token('ink-2');
  const ink3 = token('ink-3');
  const surface = token('surface');
  const accent = token('accent');
  const strong = token('border-strong');
  const kindRules = (Object.keys(KIND_STYLE) as IdentifierKind[]).map((kind) => ({
    selector: `edge[kind = "${kind}"]`,
    style: {
      'line-style': KIND_STYLE[kind].lineStyle,
      ...(KIND_STYLE[kind].dash ? { 'line-dash-pattern': KIND_STYLE[kind].dash } : {}),
      ...(KIND_STYLE[kind].thin ? { width: 'mapData(weight, 0, 1, 1, 3)' } : {}),
    },
  }));
  return [
    {
      selector: 'node',
      style: {
        width: 44,
        height: 44,
        'background-color': surface,
        'border-width': 2,
        'border-color': ink2,
        label: 'data(label)',
        'text-wrap': 'wrap',
        'text-valign': 'bottom',
        'text-margin-y': 4,
        'font-size': 11,
        color: ink2,
        'text-background-color': surface,
        'text-background-opacity': 0.85,
        'text-background-padding': '1px',
      },
    },
    { selector: 'node.case', style: { width: 60, height: 60, 'border-width': 4, 'border-color': accent, 'font-weight': 600, color: ink1 } },
    { selector: 'node.picked', style: { 'border-color': accent, 'border-width': 4 } },
    {
      selector: 'edge',
      style: {
        width: 'mapData(weight, 0, 1, 1, 6)',
        'line-color': strong,
        'curve-style': 'bezier',
        'font-size': 10,
        color: ink3,
        'text-background-color': surface,
        'text-background-opacity': 0.9,
      },
    },
    ...kindRules,
    { selector: 'edge.match', style: { 'line-color': accent, 'z-index': 10 } },
    { selector: '.faded', style: { opacity: 0.15 } },
    { selector: 'edge.disputed', style: { 'line-style': 'dashed', 'line-dash-pattern': [6, 4], label: OPERATOR.linked.edgeDisputed } },
    { selector: 'edge.explained', style: { 'line-style': 'dotted', opacity: 0.4, label: OPERATOR.linked.edgeExplained } },
    { selector: 'edge.picked', style: { 'line-color': accent, width: 6, 'z-index': 20 } },
  ] as StylesheetJson;
}

function elements(view: LinkedAccountsView): ElementDefinition[] {
  const nodes = orderedAccounts(view).map((a) => ({
    group: 'nodes' as const,
    data: { id: a.customer_id, label: `${a.customer_id}\n${a.display_name ?? ''}`, isCase: a.is_case_customer ? 1 : 0 },
    classes: a.is_case_customer ? 'case' : '',
  }));
  const edges = view.links.map((l) => ({
    group: 'edges' as const,
    data: { id: l.link_id, source: l.customer_a, target: l.customer_b, weight: l.weight_decayed, kind: strongestKind(l) },
    classes: linkState(l) === 'active' ? '' : linkState(l),
  }));
  return [...nodes, ...edges];
}

function layoutOptions(view: LinkedAccountsView) {
  const kind = graphLayout(view.accounts.length);
  const common = { animate: false, fit: true, padding: FIT_PADDING };
  if (kind === 'circle') return { name: 'circle', startAngle: (3 / 2) * Math.PI, clockwise: true, ...common };
  if (kind === 'concentric') {
    return {
      name: 'concentric',
      startAngle: (3 / 2) * Math.PI,
      clockwise: true,
      minNodeSpacing: 36,
      concentric: (node: { data(key: string): number }) => node.data('isCase') + 1,
      levelWidth: () => 1,
      ...common,
    };
  }
  return { name: 'cose', randomize: false, ...common };
}

interface AccountGraphProps {
  view: LinkedAccountsView;
  selectedLinkId: string | null;
  selectedAccountId: string | null;
  filterKinds: ReadonlySet<IdentifierKind>;
  onSelectLink(linkId: string): void;
  onSelectAccount(customerId: string): void;
}

export function AccountGraph({ view, selectedLinkId, selectedAccountId, filterKinds, onSelectLink, onSelectAccount }: AccountGraphProps) {
  const container = useRef<HTMLDivElement>(null);
  const cyRef = useRef<Core | null>(null);
  const handlers = useRef({ onSelectLink, onSelectAccount });
  const present = usePresent();
  const labelFloor = Math.round((present ? LABEL_FLOOR_PRESENT_PX : LABEL_FLOOR_PX) * remScale());
  const floor = useRef(labelFloor);
  const height = Math.round((present ? GRAPH_HEIGHT_PRESENT_PX : GRAPH_HEIGHT_PX) * remScale());
  const [status, setStatus] = useState<'loading' | 'ready' | 'unavailable'>(() => (canvasSupported() ? 'loading' : 'unavailable'));

  useEffect(() => {
    handlers.current = { onSelectLink, onSelectAccount };
    floor.current = labelFloor;
  });

  useEffect(() => {
    if (!canvasSupported()) return;
    let cancelled = false;
    let cy: Core | null = null;
    import('cytoscape')
      .then(({ default: cytoscape }) => {
        if (cancelled || !container.current) return;
        cy = cytoscape({
          container: container.current,
          elements: elements(view),
          style: stylesheet(),
          layout: layoutOptions(view) as never,
          userZoomingEnabled: false,
          userPanningEnabled: true,
          boxSelectionEnabled: false,
          autoungrabify: true,
          autounselectify: true,
          minZoom: 0.3,
          maxZoom: 3,
        });
        cy.on('tap', 'edge', (event) => handlers.current.onSelectLink(event.target.id() as string));
        cy.on('tap', 'node', (event) => handlers.current.onSelectAccount(event.target.id() as string));
        const current = cy;
        cy.on('zoom', () => fitLabels(current, floor.current));
        fitLabels(cy, floor.current);
        cyRef.current = cy;
        setStatus('ready');
      })
      .catch(() => {
        if (!cancelled) setStatus('unavailable');
      });
    // Re-style on a theme change (data-theme on <html> or the system preference).
    const restyle = () => {
      if (!cy) return;
      cy.style(stylesheet());
      fitLabels(cy, floor.current);
    };
    const observer = new MutationObserver(restyle);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    media?.addEventListener?.('change', restyle);
    return () => {
      cancelled = true;
      observer.disconnect();
      media?.removeEventListener?.('change', restyle);
      cy?.destroy();
      cyRef.current = null;
    };
  }, [view]);

  // A new height (presenter mode on or off) fits the graph again, with labels at the floor.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || status !== 'ready') return;
    cy.resize();
    cy.fit(undefined, FIT_PADDING);
    fitLabels(cy, labelFloor);
  }, [height, labelFloor, status]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || status !== 'ready') return;
    cy.batch(() => {
      cy.elements().removeClass('match faded picked');
      if (filterKinds.size > 0) {
        for (const link of view.links) {
          cy.getElementById(link.link_id).addClass(matchesKinds(link, filterKinds) ? 'match' : 'faded');
        }
      }
      if (selectedLinkId) cy.getElementById(selectedLinkId).addClass('picked');
      if (selectedAccountId) cy.getElementById(selectedAccountId).addClass('picked');
    });
  }, [status, view, filterKinds, selectedLinkId, selectedAccountId]);

  const zoom = (factor: number) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.zoom({ level: cy.zoom() * factor, renderedPosition: { x: cy.width() / 2, y: cy.height() / 2 } });
  };
  const t = OPERATOR.linked;

  if (status === 'unavailable') {
    return (
      <p className="rounded-control border border-dashed border-border-strong p-3 text-caption text-ink-3" data-graph="unavailable">
        {t.graphUnavailable}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-2">
      <div
        ref={container}
        role="img"
        aria-label={t.graphLabel(accountsLabel(view.accounts.length))}
        data-graph={status}
        style={{ height }}
        data-label-floor={labelFloor}
        className="relative w-full overflow-hidden rounded-control border border-border bg-surface"
      />
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="quiet" onClick={() => zoom(ZOOM_STEP)} disabled={status !== 'ready'}>
          <ZoomIn aria-hidden="true" size={14} />
          {t.zoomIn}
        </Button>
        <Button variant="quiet" onClick={() => zoom(1 / ZOOM_STEP)} disabled={status !== 'ready'}>
          <ZoomOut aria-hidden="true" size={14} />
          {t.zoomOut}
        </Button>
        <Button variant="quiet" onClick={() => cyRef.current?.fit(undefined, FIT_PADDING)} disabled={status !== 'ready'}>
          <Maximize2 aria-hidden="true" size={14} />
          {t.fit}
        </Button>
        <span className="text-caption text-ink-3">{t.legend}</span>
      </div>
    </div>
  );
}
