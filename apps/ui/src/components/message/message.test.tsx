// Three visible layers in a customer message (rule 10, docs/ui-design.md 5.4).
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { CaseCard } from '@/api/models';
import { MessagePreview } from '@/components/message';
import { readExample } from '@/test/examples';

const marco = readExample<CaseCard>('case_card.marco.json');
const italian = marco.draft?.messages.find((m) => m.lang === 'it');
if (!italian) throw new Error('case_card.marco.json has no Italian message');

describe('MessagePreview', () => {
  it('shows template fields and the legal clause apart from the connective text', () => {
    const { container } = render(<MessagePreview message={italian} stored={italian.rendered_text} />);
    const deadline = container.querySelector('[data-template-field="deadline"]') as HTMLElement;
    expect(deadline.textContent).toContain(italian.fields.deadline);
    expect(within(deadline).getByText('DEADLINE')).toBeInTheDocument();
    const terms = container.querySelector('[data-template-field="terms"]') as HTMLElement;
    expect(terms.getAttribute('title')).toMatch(/never written by the language model/);
    const clause = container.querySelector('[data-legal-clause="withdrawal"]') as HTMLElement;
    expect(clause).not.toBeNull();
    expect(within(clause).getByText('Fixed legal clause · withdrawal')).toBeInTheDocument();
    expect(container.querySelector('[lang="it"]')).not.toBeNull();
    // Nothing is editable.
    expect(container.querySelector('input[type="text"], textarea, [contenteditable="true"]')).toBeNull();
  });

  it('previews the text as the customer receives it', () => {
    const { container } = render(<MessagePreview message={italian} stored={italian.rendered_text} />);
    fireEvent.click(screen.getByLabelText('Preview as the customer'));
    expect(screen.queryByText('DEADLINE')).toBeNull();
    const text = Array.from(container.querySelectorAll('p[lang="it"]'))
      .map((p) => p.textContent)
      .join('\n\n');
    expect(text).toBe(italian.rendered_text);
  });

  it('shows a forged text plain with an attention note', () => {
    const forged = italian.rendered_text.replace(italian.fields.terms, 'condizioni inventate');
    const { container } = render(<MessagePreview message={italian} stored={forged} />);
    expect(screen.getByText('Message layers could not be verified; showing the text as stored.')).toBeInTheDocument();
    expect(container.querySelector('[data-template-field]')).toBeNull();
  });

  it('renders customer text literally, never as HTML (T-NO-HTML)', () => {
    const message = { ...italian, connective_text: 'Ciao <b>tutti</b>, entro il {deadline}.', legal_clause_kind: null };
    const stored = `Ciao <b>tutti</b>, entro il ${italian.fields.deadline}.`;
    const { container } = render(<MessagePreview message={message} stored={stored} />);
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toContain('<b>tutti</b>');
    expect(screen.getByText('No legal clause: terms are not restrictive.')).toBeInTheDocument();
  });
});
