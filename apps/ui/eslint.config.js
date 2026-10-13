// ESLint flat config. Besides the usual React and TypeScript rules it enforces the boundaries of
// docs/ui-design.md 13.3: views never import each other (the one exception is operator ->
// CaseAssistant from the agent index), shared code never imports a view, and only src/app
// composes the views through their index. dangerouslySetInnerHTML is banned (T-NO-HTML).
import js from '@eslint/js';
import jsxA11y from 'eslint-plugin-jsx-a11y';
import reactHooks from 'eslint-plugin-react-hooks';
import reactRefresh from 'eslint-plugin-react-refresh';
import globals from 'globals';
import tseslint from 'typescript-eslint';

const VIEWS = ['operator', 'agent', 'manager', 'platform'];
const BOUNDARY = 'Views never import each other: navigate by URL (docs/ui-design.md 13.3).';

function viewBoundary(area) {
  const others = VIEWS.filter((v) => v !== area);
  const aliasBlocked = area === 'operator' ? others.filter((v) => v !== 'agent') : others;
  const patterns = [
    { regex: `^@/views/(${aliasBlocked.join('|')})(/|$)`, message: BOUNDARY },
    { regex: `^(\\.\\./)+(${others.join('|')})(/|$)`, message: BOUNDARY },
    { regex: '^(\\.\\./)+views(/|$)', message: BOUNDARY },
  ];
  if (area === 'operator') {
    patterns.push({
      regex: '^@/views/agent/.+',
      message: 'Import CaseAssistant from @/views/agent (its index) only.',
    });
  }
  const paths =
    area === 'operator'
      ? [
          {
            name: '@/views/agent',
            allowImportNames: ['CaseAssistant', 'CaseAssistantProps'],
            message: 'The operator area imports only CaseAssistant from the agent area.',
          },
        ]
      : [];
  return {
    files: [`src/views/${area}/**/*.{ts,tsx}`],
    rules: { 'no-restricted-imports': ['error', { paths, patterns }] },
  };
}

export default tseslint.config(
  {
    ignores: [
      'dist',
      'dist-mock',
      'node_modules',
      'coverage',
      'test-results',
      'playwright-report',
      'src/api/types.gen.ts',
      'src/api/contract.gen.ts',
    ],
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: { ecmaVersion: 2023, globals: globals.browser },
    plugins: { 'react-hooks': reactHooks, 'react-refresh': reactRefresh },
    rules: {
      ...reactHooks.configs.recommended.rules,
      '@typescript-eslint/no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
      '@typescript-eslint/consistent-type-imports': 'error',
      'no-restricted-syntax': [
        'error',
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: 'Customer and model text render as plain text (T-NO-HTML, docs/ui-design.md 10.3).',
        },
      ],
    },
  },
  {
    files: ['src/**/*.tsx'],
    extends: [jsxA11y.flatConfigs.recommended],
    rules: {
      'react-refresh/only-export-components': ['error', { allowConstantExport: true }],
    },
  },
  ...VIEWS.map(viewBoundary),
  {
    files: ['src/{components,api,format,copy,domain,sim,styles,test}/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { regex: '^@/views(/|$)', message: 'Shared code never imports a view (docs/ui-design.md 13.3).' },
            { regex: '^(\\.\\./)+views(/|$)', message: 'Shared code never imports a view (docs/ui-design.md 13.3).' },
          ],
        },
      ],
    },
  },
  {
    files: ['src/app/**/*.{ts,tsx}'],
    rules: {
      'no-restricted-imports': [
        'error',
        {
          patterns: [
            { regex: '^@/views/[^/]+/.+', message: 'The shell composes views through their index only.' },
          ],
        },
      ],
    },
  },
  {
    files: ['*.config.{js,ts}', 'mock/**/*.ts', 'scripts/**/*.mjs', 'tests/e2e/**/*.ts'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['scripts/**/*.mjs'],
    extends: [js.configs.recommended],
    languageOptions: { ecmaVersion: 2023, sourceType: 'module', globals: globals.node },
    rules: { 'no-unused-vars': ['error', { varsIgnorePattern: '^_' }] },
  },
);
