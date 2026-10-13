// Demo path /demo (spec 16, US-903; docs/ui-design.md 9.5). Stub: the six beats as links and the
// presenter switch; the preflight checks come next.
import { Link } from 'react-router';
import { setPresent, usePresent } from '@/app/display';
import { useReadyz } from '@/api/queries';
import { DataCheck, ScreenPlaceholder } from '@/components/feedback';
import { Button } from '@/components/ui';
import { PLATFORM } from '../copy';

export default function DemoPage() {
  const readyz = useReadyz();
  const present = usePresent();
  const t = PLATFORM.demo;
  return (
    <ScreenPlaceholder title={PLATFORM.titles.demo} area="platform" stories={['US-903']} spec="docs/ui-design.md 9.5">
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.preflight}</h2>
        <DataCheck label="GET /readyz" result={readyz} />
      </section>
      <section className="flex flex-col gap-2">
        <h2 className="text-h3">{t.beats}</h2>
        <ol className="list-decimal pl-6">
          {t.beatList.map((beat) => (
            <li key={beat.to}>
              <Link to={beat.to} className="text-accent underline underline-offset-2">
                {beat.label}
              </Link>
            </li>
          ))}
        </ol>
      </section>
      <div>
        <Button variant="primary" onClick={() => setPresent(!present)}>
          {present ? t.leavePresenter : t.presenter}
        </Button>
      </div>
    </ScreenPlaceholder>
  );
}
