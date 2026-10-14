import clsx from 'clsx';
import type { ButtonHTMLAttributes } from 'react';

type Variant = 'primary' | 'secondary' | 'restrictive' | 'quiet';

const VARIANTS: Record<Variant, string> = {
  primary: 'bg-accent-btn text-accent-on hover:bg-accent-hover border border-transparent',
  secondary: 'bg-surface text-ink-1 border border-border-strong hover:bg-surface-2',
  // A restrictive confirm uses the attention fill, never the accent blue (docs/ui-design.md 4.2, 6.3.2).
  restrictive: 'bg-attn-fg text-attn-on border border-transparent hover:opacity-90',
  quiet: 'bg-transparent text-accent border border-transparent hover:bg-accent-bg',
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

/** Button primitive (docs/ui-design.md 5.8): primary, secondary, restrictive, quiet. */
export function Button({ variant = 'secondary', className, type = 'button', ...rest }: ButtonProps) {
  return (
    <button
      type={type}
      className={clsx(
        'inline-flex min-h-8 items-center justify-center gap-2 rounded-control px-3 text-body font-medium',
        'disabled:cursor-not-allowed disabled:opacity-50',
        VARIANTS[variant],
        className,
      )}
      {...rest}
    />
  );
}
