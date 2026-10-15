import { frozenAtSnapshot, useDemoClock } from '@/api/clock';
import { DateText } from '@/components/provenance';
import { MODE } from '@/copy/common';

/**
 * The service's clock in the mode banner (/readyz `clock`, ADR-15 sections 15, 19, 20 and 21):
 * its kind, whether it stands still (the read-only public demo: at the snapshot, or at the latest
 * act recorded after it) or runs since the reset, and the snapshot instant. The API's note is the
 * tooltip, verbatim. The top bar shows the clock's 'now'.
 */
export function ClockSegment() {
  const { clock } = useDemoClock();
  if (!clock) return null;
  const frozen = frozenAtSnapshot(clock) ? MODE.clockFrozen : MODE.clockFrozenAfterActs;
  const state = clock.frozen ? frozen : clock.kind === 'demo' ? MODE.clockRunning : null;
  return (
    <span title={clock.note} data-banner-clock={clock.kind} data-clock-frozen={clock.frozen ? 'yes' : 'no'}>
      · {MODE.clockKind[clock.kind]}
      {state && ` ${state}`}
      {clock.snapshot_as_of && (
        <>
          {' '}
          · {MODE.clockSnapshot} <DateText value={clock.snapshot_as_of} />
        </>
      )}
    </span>
  );
}
