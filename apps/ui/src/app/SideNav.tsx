import clsx from 'clsx';
import { Menu } from 'lucide-react';
import { NavLink } from 'react-router';
import type { Role } from '@/api/models';
import { useQueueCount } from '@/api/queries';
import { NATURE_WORD, SHELL } from '@/copy/common';
import { displayNature } from '@/components/provenance';
import { NAV_ITEMS } from './composition';
import { usePersona } from './persona';
import type { NavBadge, NavGroup, NavItem } from './types';

const GROUP_ORDER: Record<Role, NavGroup[]> = {
  operator: ['work', 'oversight', 'demo', 'system'],
  // Luca's persona moves Oversight to the top (docs/ui-design.md 3.3).
  manager: ['oversight', 'work', 'demo', 'system'],
  warehouse: ['work', 'oversight', 'demo', 'system'],
};

const BADGE_QUERIES = {
  needsApproval: { state: ['proposed' as const], requires_approval: true },
  reviewDue: { review_due: true },
};

/** A badge count from a `page_size=1` queue query; its provenance is the queue's. */
function CountBadge({ badge }: { badge: NavBadge }) {
  const { data } = useQueueCount(badge, BADGE_QUERIES[badge]);
  if (!data || data.total === 0) return null;
  const nature = displayNature(data.provenance);
  return (
    <span
      data-figure
      data-nature={nature}
      title={`${NATURE_WORD[nature]} · ${data.provenance.origin}`}
      className="ml-auto rounded-full bg-surface-2 px-1.5 text-caption tabular-nums text-ink-1"
    >
      {data.total}
      <span className="sr-only">{` (${NATURE_WORD[nature]}: ${data.provenance.origin})`}</span>
    </span>
  );
}

function orderedItems(role: Role): Array<{ group: NavGroup; items: NavItem[] }> {
  return GROUP_ORDER[role].map((group) => {
    const items = NAV_ITEMS.filter((i) => i.group === group).sort((a, b) => a.order - b.order);
    // Ahmed's persona moves Inspection to the top of Work.
    if (role === 'warehouse' && group === 'work') {
      items.sort((a, b) => Number(b.to === '/inspection') - Number(a.to === '/inspection'));
    }
    return { group, items };
  });
}

function NavList({ role }: { role: Role }) {
  return (
    <>
      {orderedItems(role).map(({ group, items }) =>
        items.length === 0 ? null : (
          <div key={group} className="mb-4">
            <p className="px-3 pb-1 text-micro uppercase text-ink-3">{SHELL.navGroups[group]}</p>
            <ul>
              {items.map((item) => (
                <li key={item.to}>
                  <NavLink
                    to={item.to}
                    className={({ isActive }) =>
                      clsx(
                        'flex items-center gap-2 rounded-control px-3 py-1.5 text-body',
                        isActive ? 'bg-accent-bg font-medium text-ink-1' : 'text-ink-2 hover:bg-surface-2',
                      )
                    }
                  >
                    <item.icon aria-hidden="true" size={16} />
                    <span>{item.label}</span>
                    {item.badge && <CountBadge badge={item.badge} />}
                  </NavLink>
                </li>
              ))}
            </ul>
          </div>
        ),
      )}
    </>
  );
}

/** Grouped navigation (docs/ui-design.md 3.3): a column from 1024 px, a disclosure below. */
export function SideNav({ collapsed = false }: { collapsed?: boolean }) {
  const persona = usePersona();
  return (
    <>
      <nav
        aria-label={SHELL.navLabel}
        className={clsx('hidden shrink-0 border-r border-border bg-surface py-4 lg:block', collapsed ? 'lg:hidden' : 'w-nav')}
      >
        <NavList role={persona.role} />
      </nav>
      <details className="border-b border-border bg-surface px-4 py-2 lg:hidden">
        <summary className="flex cursor-pointer items-center gap-2 text-body text-ink-1">
          <Menu aria-hidden="true" size={16} />
          {SHELL.navLabel}
        </summary>
        <nav aria-label={SHELL.navLabel} className="mt-2">
          <NavList role={persona.role} />
        </nav>
      </details>
    </>
  );
}
