// Tailwind maps every colour to a CSS custom property of src/styles/tokens.css (docs/ui-design.md
// 4.2), so light and dark themes switch by tokens, never by `dark:` variants. Loaded by
// src/styles/index.css through `@config`.
import type { Config } from 'tailwindcss';

const v = (name: string) => `var(--${name})`;

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: v('bg'),
        surface: { DEFAULT: v('surface'), 2: v('surface-2') },
        border: { DEFAULT: v('border'), strong: v('border-strong') },
        ink: { 1: v('ink-1'), 2: v('ink-2'), 3: v('ink-3') },
        accent: {
          DEFAULT: v('accent'),
          hover: v('accent-hover'),
          bg: v('accent-bg'),
          btn: v('accent-btn'),
          on: v('on-accent'),
        },
        focus: v('focus'),
        attn: { fg: v('attn-fg'), bg: v('attn-bg'), hatch: v('attn-hatch'), border: v('attn-border'), on: v('on-attn') },
        danger: { fg: v('danger-fg'), bg: v('danger-bg') },
        ok: { fg: v('ok-fg'), bg: v('ok-bg') },
        field: { fg: v('field-fg'), bg: v('field-bg'), border: v('field-border') },
        clause: { fg: v('clause-fg'), bg: v('clause-bg'), rule: v('clause-rule') },
        live: { fg: v('live-fg'), bg: v('live-bg') },
        cached: { fg: v('cached-fg'), bg: v('cached-bg'), stripe: v('cached-stripe') },
        mark: v('mark'),
        'chart-pos': v('chart-pos'),
        grid: v('grid'),
      },
      fontFamily: {
        sans: ['ui-sans-serif', 'system-ui', '-apple-system', '"Segoe UI"', 'Roboto', '"Helvetica Neue"', 'Arial', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Consolas', 'monospace'],
      },
      // Typography roles of docs/ui-design.md 4.4, in rem so presenter mode (18 px root) scales.
      fontSize: {
        micro: ['0.6875rem', { lineHeight: '0.875rem', letterSpacing: '0.04em', fontWeight: '500' }],
        caption: ['0.75rem', { lineHeight: '1rem' }],
        dense: ['0.8125rem', { lineHeight: '1.125rem' }],
        body: ['0.875rem', { lineHeight: '1.25rem' }],
        message: ['0.9375rem', { lineHeight: '1.5rem' }],
        title: ['1rem', { lineHeight: '1.5rem', fontWeight: '600' }],
        h3: ['1.125rem', { lineHeight: '1.625rem', fontWeight: '600' }],
        h2: ['1.25rem', { lineHeight: '1.75rem', fontWeight: '600' }],
        page: ['1.5rem', { lineHeight: '2rem', fontWeight: '600' }],
        hero: ['2rem', { lineHeight: '2.5rem', fontWeight: '600' }],
      },
      borderRadius: { chip: '4px', control: '6px', card: '8px' },
      boxShadow: { overlay: '0 8px 24px rgb(20 26 38 / .16)' },
      maxWidth: { content: '1440px', message: '64ch' },
      width: { nav: '232px', 'nav-collapsed': '64px', drawer: '440px', sheet: '560px', record: '640px' },
    },
  },
} satisfies Config;
