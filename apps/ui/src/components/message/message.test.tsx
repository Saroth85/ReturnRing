// Three visible layers in a customer message (rule 10, docs/ui-design.md 5.4).
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { messageFor } from '@/api/adapters/messageSegments';
import type { CaseCard } from '@/api/models';
import { CompactMessage, MessagePreview } from '@/components/message';
import { readExample } from '@/test/examples';

const marco = readExample<CaseCard>('case_card.marco.json');
const italian = messageFor(marco.draft, 'it');
if (!italian) throw new Error('case_card.marco.json has no Italian message');

describe('MessagePreview', () => {
  it('shows template fields and the legal clause apart from the connective text', () => {
    const { container } = render(<MessagePreview message={italian} />);
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
    const { container } = render(<MessagePreview message={italian} />);
    fireEvent.click(screen.getByLabelText('Preview as the customer'));
    expect(screen.queryByText('DEADLINE')).toBeNull();
    const text = Array.from(container.querySelectorAll('p[lang="it"]'))
      .map((p) => p.textContent)
      .join('\n\n');
    expect(text).toBe(italian.text);
  });

  it('shows a forged text plain with an attention note', () => {
    const forged = italian.text.replace(italian.fields.terms as string, 'condizioni inventate');
    const { container } = render(<MessagePreview message={{ ...italian, text: forged }} />);
    expect(screen.getByText('Message layers could not be verified; showing the text as stored.')).toBeInTheDocument();
    expect(container.querySelector('[data-template-field]')).toBeNull();
  });

  it('renders customer text literally, never as HTML (T-NO-HTML)', () => {
    const message = {
      ...italian,
      fields: { ...italian.fields, terms: null },
      text: `Ciao <b>tutti</b>, entro ${italian.fields.deadline}.`,
      legal_clause_kind: null,
    };
    const { container } = render(<MessagePreview message={message} />);
    expect(container.querySelector('b')).toBeNull();
    expect(container.textContent).toContain('<b>tutti</b>');
    expect(screen.getByText('No legal clause: terms are not restrictive.')).toBeInTheDocument();
  });
});

describe('CompactMessage (UI15)', () => {
  it('keeps every field and the full legal clause of the guarantee message, scrolling instead of cutting', () => {
    const elena = messageFor(readExample<CaseCard>('case_card.elena.json').draft, 'it');
    if (!elena) throw new Error('case_card.elena.json has no Italian message');
    const { container } = render(<CompactMessage message={elena} />);
    const fields = Array.from(container.querySelectorAll('[data-template-field]'));
    expect(fields.length).toBe(Object.values(elena.fields).filter((v) => v !== null).length);
    const clause = container.querySelector('[data-legal-clause="guarantee"]') as HTMLElement;
    expect(clause).not.toBeNull();
    expect(elena.text.endsWith(clause.querySelector('p[lang="it"]')?.textContent ?? '∅')).toBe(true);
    expect(container.textContent).not.toContain('…');
  });
});
