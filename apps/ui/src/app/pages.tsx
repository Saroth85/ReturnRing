import { Compass } from 'lucide-react';
import { Link, Navigate, useRouteError } from 'react-router';
import { EmptyState, ErrorState } from '@/components/feedback';
import { FEEDBACK } from '@/copy/common';
import { LANDING, usePersona } from './persona';

/** `/` leads to the persona's landing page (docs/ui-design.md 3.1). */
export function RootRedirect() {
  const persona = usePersona();
  return <Navigate to={LANDING[persona.role]} replace />;
}

export function NotFound() {
  return (
    <div className="mx-auto max-w-xl py-12">
      <h1 className="mb-4 text-page">{FEEDBACK.notFoundTitle}</h1>
      <EmptyState
        icon={Compass}
        message={FEEDBACK.notFoundBody}
        action={
          <Link to="/" className="text-accent underline underline-offset-2">
            {FEEDBACK.backToStart}
          </Link>
        }
      />
    </div>
  );
}

/** A route that failed to render: the error inline, the shell around it keeps working. */
export function RouteError() {
  const error = useRouteError();
  return (
    <div className="mx-auto max-w-xl py-12">
      <ErrorState error={error} onRetry={() => window.location.reload()} />
    </div>
  );
}
