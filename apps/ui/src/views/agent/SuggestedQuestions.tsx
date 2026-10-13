// SuggestedQuestions (US-702, docs/ui-design.md 7): the three precomputed questions.
import { Button } from '@/components/ui';
import { AGENT } from './copy';

export function SuggestedQuestions({ onAsk, disabled = false }: { onAsk(question: string): void; disabled?: boolean }) {
  return (
    <ul className="flex flex-col gap-2">
      {AGENT.suggested.map((question) => (
        <li key={question}>
          <Button onClick={() => onAsk(question)} disabled={disabled} className="h-auto py-1 text-left">
            {question}
          </Button>
        </li>
      ))}
    </ul>
  );
}
