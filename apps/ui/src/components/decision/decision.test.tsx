// Positions, not verdicts (docs/ui-design.md 5.3, T-NEUTRAL seed) and the customer's floor first.
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { QuadrantId } from '@/api/models';
import { ApprovalFlag, OutcomeBadge, PathBadge, QuadrantBadge, RefundFloorNotice, ReviewDateChip } from '@/components/decision';

const QUADRANTS: QuadrantId[] = ['Q1', 'Q2', 'Q3', 'Q4'];

describe('QuadrantBadge', () => {
  it('has the same style for Q1 to Q4, apart from the glyph cell', () => {
    const classes = QUADRANTS.map((q) => {
      const { container, unmount } = render(<QuadrantBadge quadrant={q} />);
      const badge = container.querySelector(`[data-quadrant-badge="${q}"]`) as HTMLElement;
      const className = badge.className;
      unmount();
      return className;
    });
    expect(new Set(classes).size).toBe(1);
    for (const className of classes) expect(className).not.toMatch(/attn|danger|ok-/);
  });

  it('fills exactly one cell of the position glyph', () => {
    const { container } = render(<QuadrantBadge quadrant="Q4" />);
    const filled = Array.from(container.querySelectorAll('rect')).filter((r) => r.getAttribute('fill') !== 'none');
    expect(filled).toHaveLength(1);
  });
});

describe('OutcomeBadge: exactly three families', () => {
  it('guarantee path is never scored', () => {
    render(<OutcomeBadge path="guarantee" evidenceQuality="sufficient" quadrant={null} />);
    expect(screen.getByText('Guarantee path · not scored')).toBeInTheDocument();
  });

  it('not enough evidence has no quadrant', () => {
    render(<OutcomeBadge path="withdrawal" evidenceQuality="insufficient" quadrant={null} />);
    expect(screen.getByText('Not enough evidence · standard terms')).toBeInTheDocument();
  });

  it('otherwise the quadrant position', () => {
    const { container } = render(<OutcomeBadge path="withdrawal" evidenceQuality="sufficient" quadrant="Q3" />);
    expect(container.querySelector('[data-quadrant-badge="Q3"]')).not.toBeNull();
  });
});

describe('ApprovalFlag and review date (rule 5)', () => {
  it('shows "Needs approval" only while a restriction is proposed', () => {
    const { rerender, container } = render(<ApprovalFlag requiresApproval state="proposed" />);
    expect(screen.getByText('Needs approval')).toBeInTheDocument();
    expect(container.querySelector('.hatch-attn')).not.toBeNull();
    rerender(<ApprovalFlag requiresApproval state="approved" />);
    expect(screen.queryByText('Needs approval')).toBeNull();
    rerender(<ApprovalFlag requiresApproval={false} state="proposed" />);
    expect(screen.queryByText('Needs approval')).toBeNull();
  });

  it('shows the review date as given, or no date for a non-restriction', () => {
    const { rerender } = render(<ReviewDateChip reviewAt="2026-12-27" />);
    expect(screen.getByText('27 Dec 2026')).toBeInTheDocument();
    rerender(<ReviewDateChip reviewAt={null} />);
    expect(screen.getByText('no review date: not a restriction')).toBeInTheDocument();
  });
});

describe("the customer's floor (rules 3 and 4)", () => {
  it('states the refund deadline and the intact withdrawal right', () => {
    render(<RefundFloorNotice refundDueBy="2026-10-12" path="withdrawal" />);
    expect(screen.getByText(/Refund due by/).textContent).toBe('Refund due by 12 Oct 2026 · withdrawal right intact on every order');
  });

  it('states the free remedy on the guarantee path', () => {
    render(<RefundFloorNotice refundDueBy="2026-10-12" path="guarantee" />);
    expect(screen.getByText(/Guarantee path · free remedy under/)).toBeInTheDocument();
  });

  it('labels the path in words', () => {
    render(<PathBadge path="withdrawal" />);
    expect(screen.getByText('Withdrawal')).toBeInTheDocument();
  });
});
