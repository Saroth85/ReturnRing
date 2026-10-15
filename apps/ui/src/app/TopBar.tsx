import { useId } from 'react';
import { Link } from 'react-router';
import { DEMO_USERS } from '@/api/contract.gen';
import { useDemoClock } from '@/api/clock';
import { useApiMeta } from '@/api/meta';
import type { DemoUserId } from '@/api/models';
import { LlmStatusPill } from '@/components/mode';
import { DateText, toggleProvenanceMode, useProvenanceMode } from '@/components/provenance';
import { Kbd } from '@/components/ui';
import { APP_NAME, PROVENANCE, SHELL, SHOP_LINE } from '@/copy/common';
import { setTheme, useTheme, type ThemeSetting } from './display';
import { personaName, setPersona, usePersona } from './persona';

function PersonaSwitcher() {
  const persona = usePersona();
  const id = useId();
  return (
    <span className="inline-flex items-center gap-1 text-caption">
      <label htmlFor={id} className="text-ink-3">
        {SHELL.actingAs}
      </label>
      <select
        id={id}
        data-persona-switcher
        value={persona.id}
        onChange={(event) => setPersona(event.target.value as DemoUserId)}
        className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body text-ink-1"
      >
        {DEMO_USERS.map((u) => (
          <option key={u.id} value={u.id}>
            {personaName(u.id)} · {SHELL.roles[u.role] ?? u.role}
          </option>
        ))}
      </select>
    </span>
  );
}

function ThemeMenu() {
  const theme = useTheme();
  const id = useId();
  return (
    <span className="inline-flex items-center gap-1 text-caption">
      <label htmlFor={id} className="text-ink-3">
        {SHELL.theme}
      </label>
      <select
        id={id}
        value={theme}
        onChange={(event) => setTheme(event.target.value as ThemeSetting)}
        className="rounded-control border border-border-strong bg-surface px-2 py-1 text-body text-ink-1"
      >
        {(Object.keys(SHELL.themes) as ThemeSetting[]).map((t) => (
          <option key={t} value={t}>
            {SHELL.themes[t]}
          </option>
        ))}
      </select>
    </span>
  );
}

/** Brand, shop, demo clock and the global controls (docs/ui-design.md 3.4). */
export function TopBar() {
  const meta = useApiMeta();
  const clock = useDemoClock();
  const provenanceMode = useProvenanceMode();
  return (
    <header data-top-bar className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-border bg-surface px-4 py-2">
      <p className="flex flex-wrap items-baseline gap-x-2">
        <Link to="/" className="text-title text-ink-1">
          {APP_NAME}
        </Link>
        <span className="text-caption text-ink-3">· {SHOP_LINE}</span>
        {/* The service's 'now' (/readyz clock: demo, frozen or wall), never the browser's date. */}
        {clock.now && clock.clock ? (
          <span
            className="text-caption text-ink-3"
            title={SHELL.clockTip(clock.clock.note)}
            data-top-clock={clock.kind ?? undefined}
            data-clock-frozen={clock.frozen ? 'yes' : 'no'}
          >
            · {SHELL.serviceNow} <DateText value={clock.now.toISOString()} />
          </span>
        ) : (
          meta.asOf && (
            <span className="text-caption text-ink-3">
              · {SHELL.asOf} <DateText value={meta.asOf} />
            </span>
          )
        )}
      </p>
      <div className="ml-auto flex flex-wrap items-center gap-3">
        <LlmStatusPill />
        <button
          type="button"
          aria-pressed={provenanceMode}
          title={PROVENANCE.toggleHint}
          onClick={toggleProvenanceMode}
          className="inline-flex items-center gap-1 rounded-control border border-border-strong px-2 py-1 text-caption text-ink-1 aria-pressed:bg-accent-bg"
        >
          {PROVENANCE.toggle} <Kbd>P</Kbd>
        </button>
        <ThemeMenu />
        <PersonaSwitcher />
      </div>
    </header>
  );
}
