// Three visible layers in a customer message (rule 10, docs/ui-design.md 5.4).
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { messageFor } from '@/api/adapters/messageSegments';
import type { CaseCard, GenerationInfo } from '@/api/models';
import { CompactMessage, GenerationBadge, MessagePreview, generationLabel } from '@/components/message';
import { LLM_NAME } from '@/copy/constants';
import { renderWithProviders } from '@/test/render';
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

describe('GenerationBadge: the model from the generation record (rules 11, 13)', () => {
  const base = marco.draft?.generation;
  if (!base) throw new Error('case_card.marco.json has no draft');
  const g = (over: Partial<GenerationInfo>): GenerationInfo => ({ ...base, ...over });

  it('a mock text names the mock language model, never the planned model', () => {
    const { container } = renderWithProviders(<GenerationBadge generation={g({ generation_path: 'model', llm_mode: 'mock', model: 'mock' })} />);
    const badge = container.querySelector('[data-generation-path]') as HTMLElement;
    expect(badge).toHaveTextContent('Mock language model (hand-written stand-in) · validated on the first call');
    expect(badge).not.toHaveTextContent(LLM_NAME);
  });

  it('a vLLM text names the served model id, on the first or the second call', () => {
    const served = { llm_mode: 'vllm' as const, model: 'google/gemma-4-31b-it' };
    expect(generationLabel(g({ ...served, generation_path: 'model' }))).toBe('google/gemma-4-31b-it · validated on the first call');
    expect(generationLabel(g({ ...served, generation_path: 'regenerated' }))).toBe('google/gemma-4-31b-it · validated on the second call');
    // A cached package made by another model (ADR-15 section 17) is labelled with that model.
    expect(generationLabel(g({ llm_mode: 'vllm', model: 'plan-b/other-model', generation_path: 'model' }))).toMatch(/^plan-b\/other-model · /);
  });

  it('the template names no model', () => {
    expect(generationLabel(g({ generation_path: 'template', llm_mode: 'vllm', model: 'template' }))).toBe('Reviewed template, no model text');
  });
});
