// AgentStepList (docs/ui-design.md 7): one row per tool_call/tool_result pair: "Step n of at most
// 5", the tool in plain words, its arguments, done or failed, the tool's summary verbatim and the
// refs it returned as links that focus the case card.
import { CircleCheck, CircleX, LoaderCircle } from 'lucide-react';
import { AGENT } from './copy';
import { RefList, type RefActivate } from './RefChip';
import type { StepView } from './stream';

function argumentName(name: string): string {
  return AGENT.argumentNames[name] ?? name.replace(/_/g, ' ');
}

function StepStatus({ status }: { status: StepView['status'] }) {
  if (status === 'running') {
    return (
      <span className="inline-flex items-center gap-1 text-ink-3">
        <LoaderCircle aria-hidden="true" size={14} className="motion-safe:animate-spin" />
        {AGENT.turn.running}
      </span>
    );
  }
  if (status === 'failed') {
    // A tool that failed is the system failing: danger, with the word.
    return (
      <span className="inline-flex items-center gap-1 text-danger-fg">
        <CircleX aria-hidden="true" size={14} />
        {AGENT.turn.failed}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center text-ink-2">
      <CircleCheck aria-hidden="true" size={14} />
      <span className="sr-only">{AGENT.turn.ok}</span>
    </span>
  );
}

interface AgentStepListProps {
  steps: StepView[];
  returnId: string;
  onActivateRef?: RefActivate;
}

export function AgentStepList({ steps, returnId, onActivateRef }: AgentStepListProps) {
  return (
    <ol className="flex flex-col gap-2" data-agent-steps>
      {steps.map((step, index) => (
        <li
          key={`${step.step}:${index}`}
          className="flex flex-col gap-1 rounded-control border border-border bg-surface px-3 py-2"
          data-step-status={step.status}
        >
          <p className="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-caption">
            <span className="text-ink-3">
              <span data-id>{AGENT.turn.step(step.step)}</span>
              {index === 0 && (
                <>
                  {' '}
                  <span data-constant>{AGENT.turn.stepLimit}</span>
                </>
              )}
            </span>
            <span aria-hidden="true" className="text-ink-3">
              ·
            </span>
            <span className="font-medium text-ink-1">{AGENT.tools[step.tool].label}</span>
            <StepStatus status={step.status} />
            {step.arguments.length > 0 && (
              <span className="inline-flex flex-wrap gap-x-2 text-ink-3">
                <span className="sr-only">{AGENT.turn.argumentsLabel}:</span>
                {step.arguments.map(([name, value]) => (
                  <span key={name}>
                    {argumentName(name)}{' '}
                    <span className="font-mono text-ink-2" data-id>
                      {value}
                    </span>
                  </span>
                ))}
              </span>
            )}
          </p>
          {step.summary !== null && (
            <p className="text-body text-ink-1" data-verbatim>
              {step.summary}
            </p>
          )}
          <RefList refs={step.refs} returnId={returnId} onActivate={onActivateRef} />
        </li>
      ))}
    </ol>
  );
}
