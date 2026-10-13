import clsx from 'clsx';
import type { DisplayNature } from '@/api/models';
import { NATURE_WORD } from '@/copy/common';

interface NatureMarkProps {
  nature: DisplayNature;
  /** Hide the screen-reader word when the word is already visible next to the mark. */
  decorative?: boolean;
  className?: string;
}

/**
 * The nature glyph in neutral ink (docs/ui-design.md 5.1): solid dot real, hollow diamond
 * synthetic, dashed ring simulated, hatched square illustrative. Shapes survive greyscale.
 */
export function NatureMark({ nature, decorative = false, className }: NatureMarkProps) {
  return (
    <span className={clsx('inline-flex items-center text-ink-2', className)} data-nature-mark={nature}>
      <svg aria-hidden="true" focusable="false" width="10" height="10" viewBox="0 0 10 10">
        {nature === 'real' && <circle cx="5" cy="5" r="4" fill="currentColor" />}
        {nature === 'synthetic' && (
          <rect x="2" y="2" width="6" height="6" transform="rotate(45 5 5)" fill="none" stroke="currentColor" strokeWidth="1.4" />
        )}
        {nature === 'simulated' && (
          <circle cx="5" cy="5" r="3.6" fill="none" stroke="currentColor" strokeWidth="1.4" strokeDasharray="2 1.6" />
        )}
        {nature === 'illustrative' && (
          <g fill="none" stroke="currentColor" strokeWidth="1">
            <rect x="1" y="1" width="8" height="8" />
            <path d="M1 5 L5 1 M1 9 L9 1 M5 9 L9 5" />
          </g>
        )}
      </svg>
      {!decorative && <span className="sr-only">{NATURE_WORD[nature]}</span>}
    </span>
  );
}
