# ReturnRing UI design

Final design for phase 5 (`apps/ui`), API contract 1.1.0 (commit `5b3c8e9`, ADR-15). It merges three
proposals: operator efficiency first (P1), demo and pitch first (P2), trust and compliance first (P3).
It takes P3's trust system as the backbone, P2's case hero, pitch path and visual highlights, and P1's
operator loop and action friction. Section 2 lists every conflict and how it was resolved.

Status: final design for review, 13 Oct 2026. Deadline Sun 18 Oct 2026. Build budget: two days, four
engineers, one view area each (section 13).

---

## 0. Sources and precedence

- **Precedence.** CLAUDE.md rules, then `docs/spec.md`, then ADR-15 and the generated contract, then this
  document. If a screen here contradicts the contract, the contract wins and this document gets a fix.
- **Read before building.**
  - Spec sections 3 and 4 (users, Navigli Fashion), 5 (E3, E4, E5, E6, E7, E9), 8.6 to 8.10, 9, 16.
  - ADR-15 and, at `5b3c8e9`: `contracts/json/api/*.schema.json`, `openapi.json`, `endpoints.json`,
    `contracts/examples/api/*.json`, `index.json`.
  - ADR-14 (facts cited by statements, customer phrasebook), `contracts/levers.py`, `llm/legal_clause.py`.
- **Numbers in this document** come from contract examples. Their nature is synthetic or simulated, their
  origin starts with "Contract example:", and benchmark values are illustrative. They are placeholders,
  never accuracy (US-906).
- **Demo clock.** `as_of` 2026-09-30T18:00:00Z, the end of the synthetic year.

---

## 1. Principles

| # | Principle | What it means on screen |
| --- | --- | --- |
| P1 | **The engine decides, and the page says so** (rule 1) | The decision lives only in the sticky Decision rail, headed "Engine decision · rules `{rules_version}`". Generated text sits elsewhere, titled "cannot change the decision", with a visible decision-echo check. A four-step `DecisionChain` (models score, rules decide, legal guard checks, a person approves) is on every case. |
| P2 | **Every number carries origin, period and nature** (rule 11, US-906) | Numbers render only through `Figure` (a value plus its `ProvenanceBadge`) or `PlainNumber` inside a `ProvenanceScope`. A dev build throws on a number with no provenance. |
| P3 | **Positions, not verdicts** (rule 6, spec 16) | Q1 to Q4 have no colour: a 2×2 position glyph and the thresholds. Risk and value use the same neutral track. The value legend is always visible. |
| P4 | **A person, a reason, a date** (rule 5) | A restriction always shows its approver or "needs approval", its review date and its lifecycle. Overrides, rejections, lifts and disputes require a reason. |
| P5 | **The customer's floor comes first** (rules 3, 4) | The first line of every decision is the refund deadline and "withdrawal right intact". The guarantee path is never scored and never offers a restrictive lever. |
| P6 | **Three visible layers in a customer message** (rule 10) | Connective sentences, template fields (filled by code) and the fixed legal clause are styled differently, in Italian and English. |
| P7 | **Live and cached are never confused** (US-901) | A banner that cannot be dismissed, a corner tag, a tab-title prefix, and a label on each generated text. |
| P8 | **Friction follows consequence** | A non-restrictive confirmation is two keystrokes. A restrictive approval opens a sheet that repeats every term change, the review date and the coupon scope, and only an explicit confirm commits it. |
| P9 | **The server owns legal state** | `in_force`, `review_due` and lapses come from the API. The client clock never decides them. The UI previews the legal guard but never replaces it. |
| P10 | **Fail visibly, never silently** | Template fallback, validator retries, 409, 422, 503, a message whose layers cannot be verified: each has an explicit state with the request id. |

---

## 2. Decisions on conflicts between the proposals

| Topic | Proposals | Decision | Why |
| --- | --- | --- | --- |
| Quadrant colour | P1, P3 neutral glyph; P2 four equal-lightness hues | **Neutral glyph, no hue**, everywhere including the matrix | No hue can read as "worse"; fewer tokens; a test can assert identical styles (T-NEUTRAL). The matrix gets its highlight from annotations, not colour. |
| Case layout | P1 decision first on the left; P2, P3 sticky right rail | **Sticky Decision rail on the right (5/12)**, evidence column on the left (7/12) with P2's **CaseHero slot** at its top | The decision never scrolls away; the hero gives each golden case its picture in the pitch. |
| Queue | P1 split view (queue, case, rail); P2, P3 full-width table | **Full-width table**, plus P1's loop: `[`/`]` move between cases in the current tab, next two cards prefetched, auto-advance after an action | Same speed for Sara without a three-pane layout that needs 1600 px and two extra days. |
| Editing the customer message | P1, P3 free connective-text editor now, phrasebook later | **No free-text editor.** `message.text` is always null. Wording changes go through Regenerate (tone, languages). `PhrasebookPicker` only once the contract carries sentence ids (Could) | ADR-14 already replaced free customer prose with the phrasebook; an editor built now would be thrown away and would reopen the risk ADR-14 closed. |
| Agent placement | P1 right-rail tab; P2, P3 drawer | **Right drawer, 440 px**, exported as one component `CaseAssistant` | Does not compete with the Decision rail; satisfies the "single exported component" boundary. |
| Provenance density | P1 tag on every figure; P2 glyph plus section footer; P3 scope-aware compact | **Glyph on every `Figure`; the nature word appears when it differs from the section scope or in provenance mode (`P`)**; section footers; `PlainNumber` throws outside a scope | Every number shows its nature at a glance without word noise; the full origin is one hover or focus away. |
| Nature styling | P1 border styles; P2 glyphs; P3 hues | **Glyph shapes in neutral ink** (solid dot real, hollow diamond synthetic, dashed ring simulated, hatched square illustrative) | Hues are reserved for meaning (section 4.1); shapes survive greyscale and print. |
| Mode labelling | P1 top-bar badge; P2 band; P3 banner, corner tag, title prefix | **P3's set with P2's styling**: solid dark band for live, yellow hatch for cached | Survives cropped screenshots and video frames. |
| Story mode | P2 full story landing, preflight and rail; P3 `/demo` link list | **`/demo` page with preflight and the six beats, plus an optional presenter `DemoRail`**; no separate story landing | Keeps the pitch safety (preflight, failover) at a fraction of the cost. The Compare view is beat 1. |
| Matrix rendering | P1 Recharts up to 3,000 points then bins; P2, P3 canvas | **Canvas 2D for points, SVG overlay for axes and labels** | About 20,000 live points; Recharts SVG scatter does not scale. |
| Insufficient evidence on the matrix | P2 strip under the x axis; P3 side column | **Strip under the plot**, with its own list drawer | No risk exists for those customers, so they get no plotted position. |
| Lapse of restrictions | P1 derives from dates until fields exist; P2, P3 server only | **Server only.** Where the lapse fields are still pending, only the review date is shown | Rule-safety; a client clock must not change a legal state (T-SERVER-STATE). |
| Approving while the draft is pending | P1 disable; P2 actionable; P3 allowed with template | **Message-sending actions wait for the draft**, with the reason printed; polling up to 60 s, then Regenerate | Every decision action sends one message; the contract does not say what is sent while pending (open question O-2). |
| Restrictive confirmation | P1 `Ctrl+Enter` on an attention button; P3 no default button | **Both**: no default button, `Enter` does nothing, `Ctrl/Cmd+Enter` or a click confirms; the button uses the attention fill; coupon acknowledgement required when a coupon rule applies | Muscle memory from the fast loop cannot approve a restriction. |
| Approve body | P1 send `levers: null`; contract example sends the proposal | **Send `levers` equal to the proposal**, exactly as `decision_request.marco_approve.json` | Matches the example the mock routes on and the "if sent, must equal" rule. |
| Disallowed actions | P1 hidden; P2, P3 disabled with reason | **Disabled with the reason** (`RoleGate`) | The role model reads on screen; Ahmed sees a read-only card. |
| Mock server | P1, P3 MSW; P2 Vite middleware | **Vite dev-server middleware `returnring-mock`** | No runtime dependency, real SSE timing, role checks and queue filters on the server side, works for Playwright. |
| Waterfall colours | P2 gain and cost hues; P3 solid and hatched | **Increases solid `--chart-pos`, decreases hatched `--ink-2`, total `--ink-1`** | Polarity by direction and fill, never red and green. |
| Template field style | P1 accent background; P2 yellow; P3 blue chip with caps label | **P3**, plus a "Preview as the customer" toggle that removes the chip styling | Yellow collides with the cached hatch and attention amber. |
| Guarantee styling | P1 violet; P2 slate outline; P3 neutral | **Neutral outline with `ShieldCheck` icon**, no own hue | One fewer hue; the icon and words carry the meaning. |
| Term-change direction | P1 amber down, teal up | **Arrows and words**; "less generous" uses the attention colour, "more generous" stays in ink | A restrictive change is exactly what needs a person's attention; no extra hue. |
| Coupon rules page | not owned explicitly | **Platform area** (US-402 is in E4); the applications table and the reverse dialog are shared records components used by the manager's Reviews page and the case card | Balances the work and keeps one owner for coupon rules end to end (rule, application, checkout block). |
| Keyboard | P1 full map with chords and palette | **Reduced map** (section 3.5); no command palette, no density toggle | Two-day budget. |
| Runtime schema validation | P3 Ajv in dev | **No Ajv.** Adapter tests run every contract example | One fewer dependency; the contract tests already validate the examples. |
| Timestamps | P1 Europe/Rome; P3 UTC | **UTC with the suffix**, dates as `12 Oct 2026` | The demo clock and the audit log are UTC. |

---

## 3. Information architecture

### 3.1 Personas, landing pages, acting user

There is no authentication (spec 4). `PersonaSwitcher` in the top bar reads "Acting as Sara · operator ▾"
and offers the three `DEMO_USERS`: `sara` (operator), `luca` (manager), `ahmed` (warehouse).

- The chosen id fills every actor field: `approver`, `author`, `requested_by`, `asked_by`, `inspector`.
- It is stored in `localStorage` inside try/catch, default `sara`.
- `/` redirects by persona: Sara to `/queue`, Luca to `/portfolio`, Ahmed to `/inspection`.
- Every GET is open to every role, so every page is reachable. Writes go through `RoleGate` (section 5.8).
- **Public read-only demo.** Spec section 11 makes the public demo views read-only. A build flag
  `VITE_READ_ONLY=true` disables every write control with the reason "Read-only public demo" and adds a line
  to the mode banner. The presenter's URL is built without it.

### 3.2 Route map

| Route | Screen | Area | API calls (mock examples) | Stories | Priority |
| --- | --- | --- | --- | --- | --- |
| `/queue` | Returns queue | operator | GET `/api/returns` (`queue_response.json`; `queue_query.json` → `queue_response.approval.json`; `queue_query.review_due.json` → `queue_response.review_due.json`) | US-301 | Must |
| `/returns/:returnId` | Case card | operator | GET `/api/returns/{id}` (`case_card.*.json`; RET-999999 → `error.not_found.json`), GET `/api/audit?return_id=` | US-302..310, US-607 | Must |
| `/returns/:returnId/versions/:decisionId` | Case card with the decision record drawer | operator | GET `/api/decisions/{id}` (`decision_record.chiara_v1.json`, `decision_record.lorenzo_v1.json`) | US-604 | Should |
| `/compare?a=&b=` | Two cases side by side (pitch beat 1) | operator | 2 × GET `/api/returns/{id}` | US-302, US-906 | Must |
| `/outbox` | Simulated outbox | operator | GET `/api/outbox` (`outbox.json`; `outbox_query.json`) | US-304 | Should |
| `/portfolio` | Risk × value matrix | manager | GET `/api/portfolio/matrix` (`portfolio_matrix.json`) | US-601 | Must |
| `/simulator` | Economic simulator | manager | GET/PUT `/api/simulator/assumptions` (`simulator_assumptions.v1.json`; `simulator_assumptions_update.json` → `simulator_assumptions.v2.json`), GET `/api/portfolio/matrix` | US-602 | Must |
| `/kpi?week=` | Weekly KPIs | manager | GET `/api/kpi` (`kpi_query.json` → `kpi.json`) | US-603 | Must |
| `/fairness` | Fairness report | manager | GET `/api/fairness` (`fairness.json`) | US-605 | Should |
| `/products`, `/products/:anomalyId`, `/products/tasks` | Product anomalies and catalogue tasks | manager | GET `/api/products/anomalies` (`product_anomalies.json`), GET/POST `/api/catalog-tasks` (`catalog_tasks.json`; `catalog_task_create.json` → `catalog_task.elena.json`), PATCH `/api/catalog-tasks/{id}` (`catalog_task_update.json` → `catalog_task.in_progress.json`) | US-208, US-606 | Must |
| `/reviews` | Restrictions under review | manager | GET `/api/returns?review_due=true` (`queue_response.review_due.json`), GET `/api/coupon-rules` | US-607 | Must |
| `/audit` | Audit log with CSV export | manager | GET `/api/audit` (`audit_page.json`, `audit_query.json`; `format=csv`) | US-604 | Should |
| `/shop?preset=giulia\|marco` | Simulated checkout | platform | POST `/api/checkout/evaluate` (`checkout_evaluate_request.{giulia,marco}.json` → `checkout_evaluate_response.*`), POST `/api/checkout/confirm` (`checkout_confirm_request.giulia.json` → `checkout_confirm_response.giulia.json`, 201) | US-401..403 | Should |
| `/inspection`, `/inspection/:returnId` | Warehouse inspection | platform | GET `/api/returns?path=withdrawal`, POST `/api/inspections` (`inspection_create_request(.manual_review).json` → `inspection.awaiting.json` / `inspection.manual_review.json`), POST `/api/inspections/{id}/confirm` (`inspection_confirm_request.json` → `inspection_confirm_response.json`) | US-501..503 | Could |
| `/coupons` | Coupon rule, versions, applications | platform | GET/POST `/api/coupon-rules` (`coupon_rules.json`; `coupon_rule_create.json` → `coupon_rules.after_create.json`), POST `/api/coupon-rules/applications/{id}/reverse` (`coupon_application_reverse.json` → `coupon_rule_application.reversed.json`) | US-402 | Should |
| `/system` | Readiness, versions, benchmarks, telemetry | platform | GET `/readyz` (`readyz_response.{live,cached}.json`), GET `/api/benchmarks` (`benchmarks.json`), GET `/api/telemetry` (`telemetry.{live,cached}.json`) | US-901, US-902, US-905 | Must |
| `/demo` | Demo path and preflight | platform | GET `/readyz`, GET `/api/returns/RET-0001{01..05}`, GET `/api/simulator/assumptions` | spec 16, US-903 | Must |
| `/about` | About the data | platform | GET `/readyz` (versions) | US-906 | Should |
| `/dev/agent?return=` | Agent harness (dev builds only) | agent | POST `/api/agent/ask` | US-701 | dev |
| `*` | Not found | shell | none | | Must |

Deep-link parameters the operator area accepts (used by other areas and by `/demo`):

- `?action=approve|override|reject|review|evidence` opens that sheet once the card has loaded.
- `?link=<link_id>` selects a link in the graph and the link table.
- `?focus=linked|risk|value|orders|alternatives|message|product|history` scrolls to a section.
- `?from=<tab>` keeps the queue context for `[`/`]` navigation.

### 3.3 Navigation

`SideNav` (232 px, collapsible to 64; a top drawer under 1024 px). Groups, in this order:

| Group | Items | Shown first for |
| --- | --- | --- |
| Work | Returns queue (badge: needs approval) · Reviews due (badge) · Inspection · Outbox | Sara, Ahmed |
| Oversight | Portfolio matrix · Simulator · Weekly KPIs · Fairness · Product issues · Coupon rules · Audit log | Luca |
| Demo | Compare cases · Simulated checkout · Demo path | presenter |
| System | System and benchmarks · About the data | judges |

- Luca's persona moves the Oversight group to the top. Ahmed's moves Inspection to the top.
- Badge counts come from `page_size=1` queries (the response `total`):
  `state=proposed&requires_approval=true` and `review_due=true`, refreshed every 30 s.

### 3.4 App shell

```
┌ ModeBanner (sticky, 32 px, not dismissible) ───────────────────────────────────────────────────────┐
│ LIVE · AMD Instinct MI300X · Gemma 4 on vLLM                     (cached: yellow hatch, see 5.2)  │
├ TopBar (48 px) ────────────────────────────────────────────────────────────────────────────────────┤
│ ReturnRing · Navigli Fashion (fictional shop, synthetic data) · as of 30 Sep 2026, 18:00 UTC        │
│                                    [LLM ready] [Provenance P] [Theme ▾] [Acting as Sara · operator ▾] │
├ SideNav ┬ Page ─────────────────────────────────────────────────────────────────────────────────────┤
│         │                                                                                          │
│         │                                                              [GpuDock, toggled with T]   │
├─────────┴──────────────────────────────────────────────────────────── ModeCornerTag (cached, mock) ┤
│ DemoRail (presenter mode only): ① Same rate ② Marco ③ Rossi ④ Elena ⑤ Net benefit ⑥ AMD  01:12 ‹ › │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **`ModeBanner`, `ModeCornerTag`, `LlmStatusPill`**: section 5.2.
- **`ProvenanceToggle` (`P`)**: provenance mode prints the nature word and the period next to every
  figure and expands every section footer. Used for the video and slide screenshots.
- **`GpuDock` (`T`)**: a 280 × 96 floating card with GPU %, a sparkline, memory and tokens/s from
  `/api/telemetry`, polled every `interval_s` (5 s) and paused while the tab is hidden. In cached mode it
  shows the telemetry `note`. It is the "amd-smi in a corner" of spec 16.
- **`DemoRail`**: shown with `?present=1`, which also sets the root font to 18 px and collapses the
  SideNav. `Alt+←`/`Alt+→` move between the six beats; each beat is a URL (section 12).
- **`ToastRegion`** (`aria-live=polite`) carries confirmations only. Errors that need a decision are
  inline, never toasts.
- **Document title**: `[Cached] ReturnRing · Queue` in cached mode, `[Mock] …` on contract examples.

### 3.5 Keyboard

Single keys are inactive while focus is in a text field. Every shortcut also has a visible button with a
`Kbd` hint. `?` opens the list.

| Scope | Keys | Action |
| --- | --- | --- |
| Global | `?` · `/` · `P` · `T` | Shortcut help · focus search or filter · provenance mode · GPU dock |
| Presenter | `Alt+←` / `Alt+→` | Previous / next demo beat |
| Queue | `j` / `k`, `↓` / `↑` · `Enter` | Move the selection · open the case |
| Case | `[` / `]` | Previous / next case of the current queue tab (prefetched) |
| Case | `A` · `O` · `R` · `L` | Approve (popover or sheet) · override · reject · review (lift or renew) |
| Case | `E` · `D` · `I` · `M` | Add favourable evidence · dispute the focused link · ask about this case · message language |
| Sheets | `Enter` | Confirms **only** the non-restrictive popover and the recompute result |
| Sheets | `Ctrl/Cmd+Enter` · `Esc` | Confirm any other sheet · close (asks first if a reason was typed) |
| Inspection | `Y` · `N` · `U` | Yes · no · not observable on the focused checklist row |

Expected loop for a non-restrictive case: `]`, glance, `A`, `Enter`, auto-advance. For a restrictive case:
`A`, read the sheet, tick the acknowledgement, `Ctrl+Enter`. The extra steps fall exactly where the law
asks for a person.

---

## 4. Design tokens

### 4.1 What colour means: one meaning per family

| Family | Meaning, and nothing else | Never on |
| --- | --- | --- |
| `accent` (blue) | Interaction: links, primary non-restrictive buttons, focus, selection | a customer, a quadrant, a score |
| `attn` (amber) | A person must look or act: needs approval, review due or lapsed, a "less generous" term change, a fairness gap above threshold, a stale view | a customer name, a quadrant, a risk score, matrix points |
| `danger` (red) | The system failed or refused: network and 5xx errors, legal-guard refusals, broken integrity checks | customers, quadrants, scores, evidence |
| `ok` (green) | System health and saved confirmations only (System page checks, toasts) | customers, quadrants, scores, evidence |
| `field` (pale blue) | A template field filled by code in a customer message | anything else |
| `clause` (neutral) | The fixed legal clause block | anything else |
| `cached` (yellow hatch) | Cached mode and illustrative figures | anything else |
| Neutral inks | Everything else, including all four quadrants, evidence columns, score bars, matrix points | |

Every coloured element also carries an icon and words, so colour is never the only carrier.

### 4.2 Colour tokens

CSS custom properties in `src/styles/tokens.css`; Tailwind maps `colors` to `var(--…)`. `body` gets
`background: var(--bg); color: var(--ink-1)` explicitly.

```css
:root {
  color-scheme: light;
  --bg:#F6F7F9; --surface:#FFFFFF; --surface-2:#EEF1F5;
  --border:#DCE1E8; --border-strong:#7D8796;
  --ink-1:#141A26; --ink-2:#434D5E; --ink-3:#5C6676;
  --accent:#2557C9; --accent-hover:#1D47A8; --accent-bg:#E8EEFB; --on-accent:#FFFFFF; --focus:#2557C9;
  --attn-fg:#8A4B00; --attn-bg:#FFF3DD; --attn-hatch:#FBE3B8; --attn-border:#B7791F; --on-attn:#FFFFFF;
  --danger-fg:#B42323; --danger-bg:#FDECEC;
  --ok-fg:#256B3C; --ok-bg:#E7F4EC;
  --field-fg:#123A80; --field-bg:#EAF1FF; --field-border:#6F93D6;
  --clause-fg:#141A26; --clause-bg:#EEF1F5; --clause-rule:#434D5E;
  --live-fg:#FFFFFF; --live-bg:#141A26;
  --cached-fg:#3D2B00; --cached-bg:#FFE08A; --cached-stripe:#F2C230;
  --mark:#3B4658; --chart-pos:#2A62C9; --grid:#E6E9EE;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { /* dark values below */ } }
:root[data-theme="dark"] { /* the same dark values */ }
/* dark */
--bg:#0E1117; --surface:#161B24; --surface-2:#1E2430; --border:#2A3140; --border-strong:#6E798C;
--ink-1:#E8ECF2; --ink-2:#B9C1CE; --ink-3:#949EAE;
--accent:#86A8FF; --accent-btn:#3563D6; --accent-bg:#1C2A4A; --on-accent:#FFFFFF; --focus:#86A8FF;
--attn-fg:#F5C26B; --attn-bg:#2E2410; --attn-hatch:#3D3015; --attn-border:#B7862F; --on-attn:#141A26;
--danger-fg:#FF9B9B; --danger-bg:#3A1A1A; --ok-fg:#86D69C; --ok-bg:#14301F;
--field-fg:#C2D6FF; --field-bg:#17294A; --field-border:#4D72B8;
--clause-fg:#E8ECF2; --clause-bg:#1E2430; --clause-rule:#B9C1CE;
--live-fg:#141A26; --live-bg:#E8ECF2; --cached-fg:#FFE7A0; --cached-bg:#3A2E07; --cached-stripe:#4E3E0C;
--mark:#AEB8C8; --chart-pos:#6E9BF0; --grid:#232A36;
```

- In dark mode the primary button uses `--accent-btn` with white text; links and focus rings use `--accent`.
- The restrictive confirm button uses `--attn-fg` as its fill with `--on-attn` text.
- The projector uses the light theme (Theme menu or `?theme=light`).

**Contrast (WCAG 2.x, computed for these exact values).** Text needs 4.5:1, non-text UI 3:1.

| Pair | Light | Dark |
| --- | --- | --- |
| ink-1 / surface | 17.42 | 14.56 |
| ink-2 / surface-2 | 7.53 | 8.58 |
| ink-3 / surface-2 (worst muted text) | 5.12 | 5.75 |
| accent / bg (links) | 5.96 | 8.15 |
| on-accent / accent button (dark: accent-btn) | 6.39 | 5.37 |
| attn-fg / attn-bg | 6.19 | 9.31 |
| attn-fg / attn-hatch (worst stripe) | 5.44 | 7.85 |
| on-attn / attn-fg (restrictive confirm) | 6.80 | 10.63 |
| danger-fg / danger-bg | 5.74 | 7.76 |
| ok-fg / ok-bg | 5.71 | 8.23 |
| field-fg / field-bg | 9.54 | 9.88 |
| clause-fg / clause-bg | 15.38 | 13.12 |
| cached-fg / cached-stripe (worst) | 8.12 | 8.52 |
| live-fg / live-bg | 17.42 | 14.69 |
| border-strong / bg (inputs, non-text) | 3.39 | 4.30 |
| field-border / surface (non-text) | 3.08 | 3.63 |
| attn-border / surface (non-text) | 3.64 | 5.31 |
| chart-pos / surface (marks) | 5.69 | 6.26 |

The CONTRAST test (section 14.2) recomputes this table from `tokens.css` and fails below the thresholds.

### 4.3 Patterns

- `.hatch-attn`: `repeating-linear-gradient(135deg, var(--attn-bg) 0 10px, var(--attn-hatch) 10px 20px)`,
  always with the `UserCheck` icon and the words "Needs your approval".
- `.hatch-cached`: the same 45° stripes with `--cached-bg` and `--cached-stripe`, for cached mode and
  illustrative figures.
- `.hatch-sim`: a 6 px `--ink-3` stripe at 18% alpha, for decreases in the waterfall and simulated marks.
- In forced-colors mode the hatches fall back to system colours; the words stay.

### 4.4 Typography

- **No webfont.** Inter, IBM Plex and Source Sans are OFL, which is not on the rule 15 list; system stacks
  also avoid any runtime font request.
  - Sans: `ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif`.
  - Mono (ids): `ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`.
- Sizes are rem-based, so presenter mode (root 18 px) scales everything.

| Role | Size / line | Weight | Use |
| --- | --- | --- | --- |
| micro | 11/14, caps, letter-spacing .04em | 500 | Nature words, field labels, statement labels (11 px is the floor) |
| caption | 12/16 | 400 | Provenance footers, axis labels |
| dense | 13/18 | 400 | Tables |
| body | 14/20 | 400 | Default |
| message | 15/24, max 64ch | 400 | Customer messages |
| title | 16/24 | 600 | Case header |
| h3 / h2 / page | 18/26 · 20/28 · 24/32 | 600 | Section and page titles |
| hero | 32/40 | 600 | KPI and benchmark hero figures |

- `font-variant-numeric: tabular-nums` in tables, axes and metric columns.
- Ids (`RET-000102`, `C-MARCO-1`, `CL-000417`, `DEC-000102-1`) in mono at 13 px, click to copy (`IdText`).
- Sentence case everywhere; caps only for micro labels.

### 4.5 Spacing, layout, shape, motion

- **Spacing**: 4 px grid: 4, 8, 12, 16, 24, 32, 48. Card padding 16; gap between sections 24.
- **Grid**: 12 columns, 24 px gutters (16 px under 768 px), maximum content width 1440 px.
- **Breakpoints and targets**:

| Width | Behaviour |
| --- | --- |
| ≥ 1280 | Case card in two columns (7/12 + 5/12 sticky rail). Target screens 1440 × 900 (operator), 1280 × 720 and 1920 × 1080 (projector, video) |
| 1024–1279 | Case card in one column; the rail collapses into a sticky bottom `DecisionBar` (outcome, status, primary action) that expands into a sheet |
| 768–1023 | Queue rows become cards; checkout panes stack; matrix above simulator |
| ≥ 360 | Inspection works from 360 px (tablet first, 48 px targets). Other screens are readable, not optimised |

- No horizontal page scroll; wide tables scroll inside their own container.
- **Radius**: 4 chips and badges, 6 controls, 8 cards, sheets and dialogs, full for pills.
- **Elevation**: hairline borders instead of shadows; one shadow for sheets, popovers and dialogs:
  `0 8px 24px rgb(20 26 38 / .16)` (dark: a lighter border instead).
- **Motion**: 150 ms ease-out for sheets and disclosures; 300 ms for the waterfall morph between scenarios;
  none for data updates and streamed tokens. `prefers-reduced-motion` turns all of it off.

### 4.6 Icons (lucide-react, ISC)

Always paired with a word. `UserCheck` approval · `CalendarClock` review date · `ShieldCheck` guarantee
path · `CircleDashed` not enough evidence · `Link2` linked accounts · `Lock` legal clause · `Braces` template
field (legend only) · `History` versions · `Cpu` system actor · `MessageSquareText` generated text ·
`Search` assistant · `Mail` outbox · `Ticket` coupon · `Shirt` product issue · `TriangleAlert` attention ·
`OctagonAlert` danger · `Info` provenance. Identifier kinds: `CreditCard` card, `AtSign` email,
`Smartphone` device, `Home` address, `Phone` phone, `Network` IP prefix.

No warning triangles next to customers, no shields-and-police imagery, no "AI sparkle".

### 4.7 Number and date formatting (`src/format/figure.ts`, by `Metric.unit`)

`Intl` with `en-GB`, a true minus sign (U+2212), tabular numerals.

| Unit | Format | Example |
| --- | --- | --- |
| eur | `€1,234.50`; signed totals `+€123.50`, `−€4.40` | `€42.10` |
| probability | 2 decimals, **never a percentage** (a score of 0.87 is not "87%") | `0.87` |
| ratio | percent, 1 decimal | `54.5%` |
| count / expected_count | integer / 1 decimal | `7` / `1.8 expected` |
| z_score | `z = 6.86` | |
| percentage_points | `6.1 pp` | |
| days, seconds, milliseconds | `9 days`, `41 s`, `3,900 ms` | |
| tokens_per_second, gib, watts, percent | `1,450 tok/s`, `143.0 GiB`, `535 W`, `71%` | |

- Dates (`review_at`, `refund_due_by`) as given, `27 Dec 2026`, never shifted across time zones.
- Timestamps in UTC with the suffix: `30 Sep 2026, 18:20 UTC`; relative labels ("3 days overdue") are
  computed against the response `as_of`, never the wall clock.
- A period is `1 Oct 2025 – 30 Sep 2026`; when start equals end, one date.
- `null` is never rendered as 0: it is "not observed yet", "not scored" or "—" with screen-reader text.

### 4.8 Charts and graph

- **Charts (Recharts)**: 2 px lines, markers ≥ 8 px, `--grid` hairlines (no vertical grid), axis text in
  `--ink-3` 12 px, text never in a series colour, tooltips that render `Figure`. No dual axis: counts and
  rates are separate charts; benchmarks are small multiples by unit. Every chart has a "View as table"
  toggle (`ChartFrame`).
- **Waterfall**: increases solid `--chart-pos`, decreases `.hatch-sim` outlined in `--ink-2`, total
  `--ink-1`, zero line always visible, signed direct labels.
- **Cytoscape stylesheet** (resolved from tokens on theme change):

| Selector | Style |
| --- | --- |
| `node` | 44 px, fill `surface`, 2 px border `ink-2`; label = customer id over display name (ids first: names are not features), 12 px `ink-2` |
| `node.case` | 60 px, 4 px border `accent`, label weight 600 |
| `node.coupon` | small ticket glyph (welcome coupon redeemed), `ink-3` |
| `edge` | width `mapData(weight_decayed, 0, 1, 1, 6)`, colour `border-strong`; **line style by strongest kind**: card solid, email solid thin, device long dash, address dash, phone dash-dot, IP prefix dotted |
| `edge.match` (identifier filter) | colour `accent`, width +1, z-index 10; others `.faded` (opacity .15) |
| `edge.disputed` | dashed [6, 4], label "disputed" |
| `edge.explained` | dotted, opacity .4, label "explained" |
| `:selected` | colour `accent`, width 6 |

- Layout: ≤ 4 nodes `circle` with the case customer at the top; 5 to 20 `concentric` with the case customer
  at the centre and the others clockwise from 12 o'clock by `created_at`; more than 20 `cose` with a fixed
  seed. No physics, `userZoomingEnabled: false`, `boxSelectionEnabled: false`, zoom buttons and Fit.

---

## 5. Shared component library

Shared code lives outside `src/views/`. Each directory has one owner (section 13.2). Props listed here are
frozen at the Day 1 11:00 checkpoint; a change goes through the owner.

### 5.1 Provenance (`src/components/provenance/`)

| Component | Props | Behaviour |
| --- | --- | --- |
| `Figure` | `metric: Metric; label?: string; signed?: boolean; compareTo?: Metric; size?: 'default' \| 'hero'` | Formats by unit (4.7), then a `ProvenanceBadge`. Renders as a focusable button with `data-figure` and `aria-describedby` pointing to the popover. With `compareTo` it prints "0.87 (threshold 0.60)". |
| `ProvenanceBadge` | `provenance: Provenance; showWord?: boolean` | `NatureMark` glyph in `--ink-2`; the nature word when `showWord`, when the provenance differs from the enclosing `ProvenanceScope`, or in provenance mode. |
| `NatureMark` | `nature: 'real' \| 'synthetic' \| 'simulated' \| 'illustrative'` | Solid dot real · hollow diamond synthetic · dashed ring simulated · hatched square illustrative (when the origin starts with "Illustrative" or a benchmark has `status: illustrative`). Screen-reader text is the word. |
| `ProvenancePopover` | `provenance` | On hover, focus, or `Enter`/`Space`: value and label; Nature with a one-line meaning; Origin verbatim (the "Contract example:" prefix is never stripped); Period; for net benefits the assumption label and `based_on`; links "What do natures mean?" (`/about#natures`) and "Copy provenance". |
| `ProvenanceScope` | `provenance: Provenance; covers: string; children` | React context for a section; covers its plain numbers (ADR-15 section 2 table). |
| `ProvenanceFooter` | none (reads the scope) | One caption line: "Numbers in this section: SYNTHETIC · risk model risk-xgb-0.1.0 on decision-time features of the synthetic shop · 1 Oct 2025 – 30 Sep 2026". |
| `PlainNumber` | `value: number; unit: Unit` | For covered attributes (a price, a link weight, a SHAP value, a declared confidence). **Throws in dev builds** outside a `ProvenanceScope`. |
| `Money`, `DateText`, `DateRange`, `IdText` | value props | Formatting primitives; `IdText` is mono and copyable. Dates and ids are exempt from the provenance scan. |
| `GeneratedText` | `generation: GenerationInfo; children` | Wraps model-written operator text (statements, agent answers): `data-generated`, plain text only, with its `GenerationBadge`. |
| `ContractExampleBanner` | none | When any origin starts with "Contract example:", a thin line under the mode banner: "Mock data from contract examples, not results." |

Natures explained once, on `/about`: **Real** measured on real data (ASOS GraphReturns, CC BY 4.0) or a
real measurement in this browser; **Synthetic** computed on the Navigli Fashion synthetic shop (seed 42);
**Simulated** estimated by the economic simulator under stated assumptions; **Illustrative** a placeholder
until measured.

### 5.2 Mode (`src/components/mode/`)

- **Source of truth.** `GET /readyz` (`mode`, `llm_mode`, `device`, `label`, `checks`) and the
  `X-ReturnRing-Mode` header of every response, which `apiFetch` stores. If a header disagrees with
  `readyz.mode`, the banner reads "MIXED MODE: some responses are cached".

| Situation | `ModeBanner` text | Style |
| --- | --- | --- |
| `mode: live`, `llm_mode: vllm`, `device: gpu` | **LIVE** · AMD Instinct MI300X · Gemma 4 on vLLM | `--live-bg` band, `--live-fg` text |
| `mode: live`, `llm_mode: mock` | **LIVE (CPU)** · mock language model: drafts are hand-written stand-ins | live band with a hatched end segment |
| `mode: cached` | **CACHED DEMO** · `{readyz.label}` ("Precomputed on AMD MI300X") · GPU off: nothing is computed live | `.hatch-cached`, `--cached-fg` |
| UI on the mock server | **CONTRACT EXAMPLES** · UI running on `contracts/examples/api` | cached style |
| `status: degraded` or `not_ready` | second line in attention style listing the failing `checks` with `detail` | attention |
| `VITE_READ_ONLY` | appended: "· read-only public demo" | same |

- **`ModeCornerTag`**: fixed bottom-right, 11 px, in cached and mock modes, so a cropped screenshot keeps it.
- **`LlmStatusPill`**: from the `vllm` check: "LLM ready" · "LLM off · templates and cache in use" (cached,
  neutral) · "LLM unavailable · templates in use" (danger outline). It never blocks decisions.

### 5.3 Decision components (`src/components/decision/`, `src/domain/`)

| Component | Input | Look |
| --- | --- | --- |
| `QuadrantGlyph` | `quadrant` | 14 × 14, four cells; the active one filled `--ink-2`, others outlined `--border`. Q1 top-left (higher value, lower risk score), Q3 top-right, Q2 bottom-left, Q4 bottom-right, as on the matrix. |
| `QuadrantBadge` | `quadrant`, thresholds | Glyph + "Q4". Tooltip: "Q4: risk score ≥ 0.60 and value < €185 (matrix thresholds). A position in the matrix, not a judgement of the customer." Thresholds come from the card or matrix data. Identical computed styles for Q1 to Q4. |
| `OutcomeBadge` | `path`, `evidence_quality`, `quadrant` | Exactly three families: "Guarantee path · not scored" (`ShieldCheck`, neutral outline) · "Not enough evidence · standard terms" (`CircleDashed`, dashed outline) · `QuadrantBadge`. |
| `PathBadge` | `path` | "Withdrawal" · "Guarantee" |
| `EvidenceBadge` | `evidence.quality` | "Evidence sufficient" · "Not enough evidence" (dashed) |
| `StateBadge` | `DecisionView` | "Proposed · v1 of 1" · "Approved by sara · 30 Sep 2026, 18:20 UTC" · "Overridden by sara" · "Proposal rejected (the return proceeds)" · "Superseded by DEC-…" |
| `RestrictionBadge` | server fields only (P9) | See the table below. |
| `ReviewDateChip` | `review_at` | `CalendarClock` + "27 Dec 2026". Tooltip: "90 days after the decision (project assumption). The restriction lapses at this date unless a person renews it." Non-restrictions show "—" with screen-reader text "no review date: not a restriction". |
| `ApprovalFlag` | `requires_approval`, `state` | `UserCheck` + "Needs approval" on `.hatch-attn` |
| `ApproverLine` | `approver`, `approved_at` | "Approved by **sara** (operator) · 30 Sep 2026, 18:20 UTC" |
| `LeverChip`, `LeverList` | `Lever[]` | Label and scope tag (tables below); "restrictive" micro tag on a less generous change. |
| `TermsDiff` | `order_terms`, `future_terms`, `levers` | Rows: return window, return shipping, instant refund, size exchange, coupons, cluster coupons, transparency notice. Columns "This order (snapshot TS-…)" and "Future orders", plus the change: "↓ less generous" (attention), "↑ more generous" (ink), "unchanged" (ink-3). A 14-day window carries "legal minimum". |
| `RefundFloorNotice` | `refund_due_by`, `path` | "Refund due by 12 Oct 2026 · withdrawal right intact on every order" (guarantee: "Guarantee path · free remedy under Directive (EU) 2019/771"). |
| `StatementLabelChip` | `OBSERVED \| SCORE \| HYPOTHESIS` | "Observed" solid · "Score contribution" outlined, tooltip "a contribution to the score, not a cause" · "To verify" dashed, statement in italics. |
| `DecisionChain` | `DecisionView` | "Models score → Rules decide → Legal guard checked → A person approves (pending · approved by sara · not required: logged)". Versions in tooltips. |

`RestrictionBadge`, from API fields only (`state`, `requires_approval`, `in_force`, `review_at`,
`review_due`, `superseded_by`, `revision_trigger`):

| Condition | Label | Style |
| --- | --- | --- |
| restrictive, `proposed`, `requires_approval` | "Restrictive · needs a person's approval · review 27 Dec 2026" | attention |
| `approved` or `overridden`, restrictive, `in_force` | "In force for future orders · review 27 Dec 2026" | neutral, `CalendarClock` |
| restriction with `review_due` (or `in_force: false` after review) | "Lapsed on 27 Sep 2026: no longer applies to new orders. Renew or lift." | attention |
| `superseded_by` set | "Superseded by DEC-…" | muted, read-only |
| `revision_trigger: review`, `overridden`, no levers | "Restriction lifted on … by …" | neutral |
| Q1 upgrade, `requires_approval: false`, `in_force` | "Applies by itself · logged" | neutral |
| no levers | "No change to terms" | neutral |

Lever labels and scopes (`src/domain/levers.ts`, mirrors `contracts/levers.py`):

| Lever id:value | Label | Direction |
| --- | --- | --- |
| `return_window_days:n` | "Return window n days" (+ "legal minimum" at 14) | smaller is less generous |
| `free_return_shipping:false` | "Return shipping paid by the customer" | less generous |
| `instant_refund:false` | "Refund within the legal terms" | less generous |
| `free_size_exchange:true` | "Free size exchange" | more generous |
| `coupons_enabled:false` | "Coupons not available" | less generous |
| `cluster_coupons_disabled:true` | "Coupons paused for the linked accounts" | less generous |
| `transparency_notice:true` | "Return-rate notice in the message" | less generous (communication) |
| `size_advice:true` | "Size advice at checkout" | more generous |
| `priority_size_exchange:true` | "Priority size exchange" | more generous |

| Scope | Tag |
| --- | --- |
| `future_orders` | Future orders |
| `future_orders_cluster` | Future orders of the linked accounts |
| `all_orders` | All orders (a concession) |
| `current_order` | This order at checkout |
| `communication` | Message |

### 5.4 Message and explanation (`src/components/message/`)

**`MessagePreview`** takes a normalised `MessageSegment[]` (from `toMessageSegments`, section 11.3) and
renders three layers that never mix:

```
┌ Customer message   [Italiano · will be sent] [English] [Both]      [ ] Preview as the customer ┐
│ Ciao, abbiamo ricevuto la tua richiesta di reso e il rimborso arriverà entro il                 │
│ ┊DEADLINE 12 ottobre 2026┊. Dal prossimo ordine valgono queste condizioni: ┊TERMS finestra di    │
│ reso di 14 giorni, spedizione del reso a tuo carico, rimborso nei termini di legge, cambio      │
│ taglia gratuito, coupon non disponibili┊. Le condizioni dell'ordine attuale non cambiano.       │
│                                                                                                 │
│ ┃ [Lock] Fixed legal clause · withdrawal · added by code, cannot be edited                      │
│ ┃ Ti ricordiamo che puoi sempre esercitare il diritto di recesso entro 14 giorni dalla           │
│ ┃ consegna, senza indicarne il motivo. Ti rimborseremo entro 14 giorni dalla tua comunicazione…  │
│ Legend: plain text = reviewed sentences · ┊FIELD┊ = template field filled by code · ┃ = legal text │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Connective text** (`phrase` segments): plain `--ink-1`, message size, `lang="it"` or `lang="en"` on the
  block. With phrasebook ids, the hover tag reads "Reviewed sentence · OPEN_THANKS".
- **Code-built body** (`body` segments, after the phrasebook change): same style as connective text, hover
  note "Written by code from the engine decision".
- **`TemplateField`** (`field` segments): inline chip, `--field-bg`, 1 px `--field-border`, `--field-fg`,
  radius 4, a leading micro label `DEADLINE`, `TERMS` or `AMOUNT`. Tooltip: "{deadline}: filled by code from
  the decision (refund_due_by), never written by the language model". Selectable, never editable. A null
  value renders "[missing deadline]" in danger and blocks sending (a code bug, never silent).
- **`LegalClause`** (`clause` segment): a separate block after a gap, `--clause-bg`, 3 px `--clause-rule`
  left border, `Lock` icon, label "Fixed legal clause · withdrawal" or "· guarantee", caption "Fixed text
  from llm/legal_clause.py, added by code". When `legal_clause_kind` is null, a muted line: "No legal clause:
  terms are not restrictive."
- **"Preview as the customer"** removes chip styling and labels so the text reads as sent.
- **Integrity**: if the segments cannot be verified against `rendered_text` (draft) or `body` (outbox), the
  preview shows the stored text plain with an attention note "Message layers could not be verified;
  showing the text as stored", and logs the request id.

**`GenerationBadge`** (`GenerationInfo`): `llm` "Gemma 4 · validated first try" · `llm_regenerated`
"Gemma 4 · validated on retry" (validator codes in a disclosure) · `template` "Reviewed template (validator
fallback or model unavailable)", plus `served_from` ("Precomputed on MI300X" when cached), `llm_mode`
("Mock text (hand-written stand-in)" when mock), model and latency.

**`ExplanationList`** (`draft.explanation`, `path`): statements grouped Observed → Score contribution → To
verify, each with `StatementLabelChip`, the text in `GeneratedText`, and a **`FactCitation`** line
`entity · metric · value · period · nature`. Today one pseudo-fact per statement from its own fields and the
card provenance; after ADR-14's facts, one chip per cited fact with its own nature. Clicking an entity
calls `onRefSelect`. A SCORE statement on the guarantee path is hidden behind an integrity notice.

### 5.5 Linked accounts (`src/components/linked-accounts/`)

`LinkedAccountsPanel` (props: `view: LinkedAccountsView; focusRef?: EntityRef; onDispute(linkId);
onExplain(linkIds); readOnly?`) composes:

- Header: "Cluster of 7 accounts · CL-000417 (graph id) · 21 links". Fixed sub-line: "A link is a shared
  identifier, not proof of who uses the accounts. Common identifiers count less."
- **`IdentifierFilter`**: chips per kind with counts from `links[].kinds`, combined with OR; matching edges
  highlight, others fade.
- **`AccountGraph`** (Cytoscape, 4.8). Hover a node: created, welcome coupon redeemed at, orders and returns
  in 12 months. Click an edge: `LinkDetailPopover` with kinds, weight and weight after decay, first and last
  shared, `n_shared_events`, "identifier shared by up to `max_sharers` accounts (common identifiers weigh
  less)", dispute state, and the actions **Dispute link…** and **Explain with evidence…**.
- **`LinkTable`**, always rendered under the graph, the accessible primary view: accounts, kinds, first and
  last shared, events, sharers note (≥ 10: "identifier shared by 41 accounts: weak link"), weight, decayed
  weight, state, actions. Shows the 5 strongest links with "Show all 21". Row focus and graph selection stay
  in sync.
- **`CouponTimeline`**: one 16 px row per account on a shared axis from the first `created_at` to the last
  `welcome_coupon_redeemed_at`; ○ creation, ticket glyph redemption. This is the "coupon sequence" of pitch
  beat 2.
- **`ClusterFacts`**: five `Figure`s from `features` (distinct cards, creation spread, welcome coupons per
  account, cluster return rate, strong-identifier share) and the cluster `ProvenanceFooter`.
- When `linked_accounts` is null: one line, "No linked accounts".

### 5.6 Economics and charts (`src/components/economics/`, `src/components/charts/`, `src/sim/`)

- **`NetBenefitBreakdown`** (`NetBenefitView`): the 8 disjoint components in contract order, each a signed
  `Figure`, the total, and the fixed footnote "The refund owed to the customer is never counted as a
  saving." `variant: 'list' | 'waterfall'`.
- **`AssumptionLabel`** (`label`, `based_on`, period): parses `<decision_id>/<kind>` ("Decision DEC-000102-1 ·
  alternative: proposal", the id a link), `kpi/<week>` ("KPI week of 21 Sep 2026"), `<version>/<scenario>`
  ("Scenario: logistics halved, from assumptions-v1"); unknown patterns show the raw label.
- **`Waterfall`**, **`ChartFrame`** (title, provenance footer, "View as table"), **`Sparkline`**, **`DotPlot`**.
- **`src/sim/netBenefit.ts`**: a pure TypeScript port of `policy.simulator.net_benefit` and `sensitivity`;
  **`src/sim/rebucket.ts`**: quadrant counts for given thresholds.

### 5.7 Records (`src/components/records/`)

- **`ActorBadge`**: a person ("sara · operator", initials) or the system (`Cpu`, "System", italic ink-3). A
  human action recorded with the system actor shows an integrity warning.
- **`AuditEntryList`** (compact) and **`AuditDiff`** (`DecisionAuditState` before → after with "changed"
  markers: state, quadrant, risk, lever ids, approval flag, review date, approver, category, coupon
  application).
- **`CouponApplicationsTable`** (`coupon_rules.applications`): cluster, affected accounts (count, ids in a
  popover), source decision link, state (Proposed / Confirmed / Reversed), confirmed by, review date, reversal.
- **`ReverseCouponDialog`**: reason required; POST `…/applications/{id}/reverse`; roles operator and manager.

### 5.8 Feedback and primitives (`src/components/feedback/`, `src/components/ui/`)

- **`ErrorState`** (`ApiError`): code, message, copyable `request_id`, Retry. Panel-level error boundaries,
  so one failing panel never blanks the page.
- **`EmptyState`**: icon, one sentence, one next action.
- **`Skeleton*`**: same dimensions as the final layout; a `Figure` placeholder is a shimmer block, never 0.
- **`InlineNotice`** (info, attention, danger), **`Toast`**.
- **`LlmUnavailableNotice`**: "The language model is unavailable. The current draft stays valid, template
  messages still work, and the decision does not depend on it." Retry after 10 s.
- **`ForbiddenRoleNotice`**: "Your role (warehouse) can't approve decisions. Switch user under 'Acting as'
  to demonstrate another persona." with persona buttons.
- **`RoleGate`** (`action`): reads the roles generated from `endpoints.json`; children render disabled with
  the reason as visible text under the control and in the tooltip. Example: "Approvals are made by operators
  and managers (warehouse can view)."
- **UI primitives** over Radix (MIT): `Button` (primary, secondary, restrictive, quiet), `Sheet` (right,
  560 px), `Dialog`, `Popover`, `Tooltip`, `Tabs`, `SegmentedControl`, `Checkbox`, `RadioGroup`, `Select`,
  `TextArea` with counter, `Chip`, `Kbd`. Focus trapped in sheets and dialogs and returned to the trigger.

---

## 6. Operator area (view 1): queue, case card, decisions, evidence, disputes, alternatives

Stories US-301..310, US-607 (actions), US-604 (drawer). Directory `src/views/operator/`.

### 6.1 Returns queue `/queue`

```
Returns queue                                                    as of 30 Sep 2026, 18:00 UTC  [Refresh]
[Needs approval 3] [Review due 1] [All open 12] [Guarantee path 2] [Not enough evidence 1] [All 13]
Filters: Path ▾ · Quadrant ▾ (Q1 Q2 Q3 Q4 none) · State ▾ · Evidence ▾ · Search [id or name] · Sort: Net benefit ▾
┌─┬──────────────────────────┬───────────────┬──────────────────┬───────────────────────────┬────────────────────┬──────┬───────┬──────────┬──────────────┐
│ │ Return · customer        │ Reason · age  │ Outcome          │ Proposed terms            │ Status             │ Risk │ Value │ Net ben. │ Flags        │
│ │                          │               │                  │                           │                    │ ◆    │ ◆     │ ◌        │              │
├─┼──────────────────────────┼───────────────┼──────────────────┼───────────────────────────┼────────────────────┼──────┼───────┼──────────┼──────────────┤
│▒│ RET-000102 · 28 Sep      │ Changed mind  │ ▦ Q4             │ 6 changes · restricts     │ Needs approval ·   │ 0.87 │€42.10 │ +€123.50 │ 6 other      │
│▒│ Marco Bianchi C-MARCO-1  │ 2 days        │                  │ future orders, coupons    │ review 27 Dec 2026 │      │       │          │ accounts     │
│ │                          │               │                  │ paused for linked accounts│                    │      │       │          │              │
│┃│ RET-000105 · Elena       │ Defective     │ Guarantee path · │ No change to terms        │ Proposed           │ not  │€68.05 │ +€77.33  │ Product issue│
│ │ Marchetti C-ELENA        │               │ not scored       │                           │                    │scored│       │          │              │
│┆│ RET-000109 · Davide Costa│ Not as exp.   │ Not enough       │ Standard terms            │ Proposed           │ not  │€18.95 │ €0.00    │              │
│ │ C-19921                  │               │ evidence         │                           │                    │scored│       │          │              │
└─┴──────────────────────────┴───────────────┴──────────────────┴───────────────────────────┴────────────────────┴──────┴───────┴──────────┴──────────────┘
Numbers in this table: column headers carry their provenance · linked-account counts: SYNTHETIC · account graph graph-0.1.0 …
```

- **Data**: GET `/api/returns` with `QueueQuery`; list filters repeat the parameter
  (`quadrant=Q3&quadrant=Q4`); default sort `-expected_net_benefit` (contract sorts only:
  `±expected_net_benefit`, `±requested_at`, `-risk`); `page_size` 25 with pagination.
- **Tabs** (each a query; counts from `page_size=1` requests, cached 30 s):

| Tab | Query | Mock example |
| --- | --- | --- |
| Needs approval (Sara's default) | `state=proposed&quadrant=Q3&quadrant=Q4&requires_approval=true` | `queue_query.json` → `queue_response.approval.json` (Marco, Martina, Chiara) |
| Review due | `review_due=true` | `queue_query.review_due.json` → `queue_response.review_due.json` (Lorenzo) |
| All open | `state=proposed` | filtered by the mock |
| Guarantee path | `path=guarantee` | filtered by the mock (Elena, Francesca) |
| Not enough evidence | `evidence_quality=insufficient` | filtered by the mock (RET-000109) |
| All | none | `queue_response.json` (13 items) |

- **Columns** (`QueueItem`):
  - rail: `ConsequenceRail`, a 6 px left edge: hatched attention when proposed and `requires_approval`;
    solid attention when `review_due`; solid ink when guarantee; dashed ink-3 when not enough evidence.
  - Return · customer: mono id, `requested_at`, display name, mono customer id.
  - Reason · age; Outcome (`OutcomeBadge`); Proposed terms (`LeverSummary`: "6 changes · restricts future
    orders", "2 changes · more generous", "No change to terms"; full `LeverList` in the tooltip); Status
    (`StateBadge` short + `ApprovalFlag` or `RestrictionBadge` + review date).
  - Risk, Value, Net benefit: `Figure`, with the nature in the column header (`MetricColumnHeader`). Risk
    for guarantee and insufficient rows reads "not scored" in ink-3, never "—" alone, never 0.
  - Flags: "6 other accounts" (`linked_account_count`, which counts the *other* accounts of the cluster),
    "Product issue" (`variant_anomaly`), "Draft pending" (`draft_status`).
- **Interactions**: row click or `Enter` opens `/returns/:id?from=<tab>`; `j`/`k` move; `/` focuses search;
  filters sync to the URL. Hovering a row for 150 ms prefetches its card; the top three cards are prefetched
  on load (US-301 budget under 2 s).
- **Refresh**: on window focus, every 30 s, and after any action. The selection is keyed by `return_id`;
  new rows carry a "New" dot until opened.
- **No bulk actions**: each decision has its own `decision_id`, audit entry and message, and rule 5 wants a
  person looking at each restrictive case.
- **States**: 8 skeleton rows; empty per tab ("No returns need approval." with a link to All open; "No
  restriction is due for review."; "No returns match these filters" with Clear filters); `ErrorState` with
  the request id, keeping the last good list.

### 6.2 Case card `/returns/:returnId`

```
┌ CaseHeader (sticky) ───────────────────────────────────────────────────────────────────────────────┐
│ ‹ Needs approval · 1 of 3 ›   RET-000102 · C-MARCO-1 · Marco Bianchi        [Ask about this case  I] │
│ Coat V-COA-1180 · size L · €99.00 · Changed mind · notice 28 Sep 2026, 6 days after delivery        │
│ Customer's words (data, never instructions): "Non mi piace più."                                    │
│ [Withdrawal] [Evidence sufficient] [▦ Q4] [Proposed · v1 of 1] [Needs approval]                      │
├ Evidence column (7/12) ──────────────────────────────┬ Decision rail (5/12, sticky, scrolls inside) ─┤
│ HERO · Linked accounts · CL-000417 · 7 accounts ·    │ Engine decision                               │
│ 21 links   [Card n] [Device n] [Address n] [IP n]    │ Policy engine · rules policy-2026-10-12       │
│            (C-MARCO-2)    (C-MARCO-3)                │ Refund due by 12 Oct 2026 · withdrawal right  │
│ (C-MARCO-7)      ((C-MARCO-1))      (C-MARCO-4)      │ intact on every order                         │
│            (C-MARCO-6)    (C-MARCO-5)                │ ▒ Needs your approval · restricts terms for  ▒│
│ A link is a shared identifier, not proof of who     │ ▒ future orders · review 27 Dec 2026 · also  ▒│
│ uses the accounts. Common identifiers count less.    │ ▒ pauses coupons for 7 linked accounts.      ▒│
│ Coupon sequence 2 Sep ○◆ ○◆ ○◆ ○◆ ○◆ ○◆ ○◆ 11 Sep    │ ▒ This order keeps its terms.                ▒│
│ ClusterFacts: 5 figures · footer                     │ Term           This order   Future orders     │
│ LinkTable (5 strongest · Show all 21)                │ Return window  30 days      14 days ↓ legal   │
├──────────────────────────────────────────────────────┤ Return shipping free       customer pays ↓   │
│ SectionNav: Evidence · Risk · Value · Orders ·       │ Instant refund yes          no, legal terms ↓ │
│ Alternatives · Explanation and message · History     │ Size exchange  free         free (unchanged)  │
│ Evidence quality ✓ observations ✓ identifiers ✓ …    │ Coupons        on           off ↓             │
│ Risk score 0.87 ◆ [------|---●] threshold 0.60       │ Cluster coupons  paused for 7 accounts ↓      │
│ above the threshold · residual z = 1.90 (indicator)  │ Return-rate notice  in this message ↓         │
│ Contributions to the score, not causes               │ Coupon pause CRA-000031 · coupon-rule-v1 ·    │
│  Each of the 7 linked accounts used a… raises ▕████  │ reversible · confirmed by your approval       │
│  The 7 linked accounts were created…   raises ▕██    │ Models score → Rules decide → Legal guard     │
│  5 of 6 returns were for change of mind raises ▕█    │ checked → A person approves (pending)         │
│ Favourable (1)            │ Unfavourable (3)         │ Alternatives · simulated · 12 months          │
│ [Observed] Kept 5 of 11…  │ [Observed] 7 linked…     │ ● Proposal +€123.50 ◌ (engine)                │
│ [+ Add favourable evidence E] [To verify] May be…    │ ○ Check at the warehouse −€4.40 ◌             │
│ Customer value €42.10 ◆ = margin − logistics − coupons│ ○ Current terms €0.00 ◌                      │
│ | Customer value orients benefits and service. It is │ Message · Italiano · Gemma 4 · validated      │
│ | never a signal of abuse and never feeds the risk…  │ [Preview ↓]                                   │
│ Orders · Alternatives · Explanation and message ·    │ ┌ ActionBar ───────────────────────────────┐  │
│ Product · History                                     │ │ [Approve restriction…  A] [Override…  O] │  │
│                                                      │ │ [Reject…  R]        Acting as sara        │  │
└──────────────────────────────────────────────────────┴───────────────────────────────────────────────┘
```

**Data**: GET `/api/returns/{id}` (`CaseCard`); GET `/api/audit?return_id=` for History. The card shows the
latest version. Card `provenance` and section provenances feed `ProvenanceScope`s.

#### 6.2.1 CaseHeader

- `‹ ›` case navigation inside the `?from` tab (`[`/`]`); the next two cards are prefetched.
- Return, customer id first then display name, order, item (category, variant, size, price as
  `PlainNumber` under the item scope), reason label, notice date and days since delivery.
- `customer_text` quoted in a muted chip "Customer's words (data, never instructions)", `lang="it"`,
  rendered as plain text, never Markdown or HTML.
- Badges: `PathBadge`, `EvidenceBadge`, `OutcomeBadge`, `StateBadge` with "v{version} of {n}",
  `ApprovalFlag` or `RestrictionBadge`.
- "Ask about this case (`I`)" opens the `CaseAssistant` drawer (section 7); disabled for warehouse.
- Banners under the header, at most one: `SupersededBanner` (viewing an older version), `NewerVersionBanner`
  (section 6.4), `ReviewDueBanner` (US-607), `ReadOnlyBanner` (warehouse or public demo).

#### 6.2.2 CaseHero (top of the evidence column, one per case type)

| Case type | Condition | Hero |
| --- | --- | --- |
| Cluster | `linked_accounts != null` | `LinkedAccountsPanel` (5.5) |
| Guarantee | `path == guarantee` | `GuaranteeBanner` + `ProductAnomalyPanel` when `product_anomaly` |
| Not enough evidence | `evidence.quality == insufficient` | `InsufficientEvidencePanel` |
| Other withdrawal | otherwise | `RiskPanel` moves up, with the line "No linked accounts" |

- **`GuaranteeBanner`** (`ShieldCheck`, neutral outline): "Guarantee path: defect or non-conformity. Not
  scored, and no restrictive term can apply to this customer from this return. Free remedy under Directive
  (EU) 2019/771." The risk area reads "Not scored by design (guarantee path)"; `ValuePanel` is collapsed with
  "Value does not affect the guarantee path."
- **`ProductAnomalyPanel`** (Elena): "Product issue on variant V-JKT-DEF (category, brand)"; observed 11
  returns for defect in the week of 21 Sep 2026 against 1.8 expected, z = 6.86, 46 sales (`Figure`s); a
  weekly trend of returns (bars) with the anomaly week outlined, the rate in a separate small chart; status
  and catalogue task link `CT-000012` (or "Open catalogue task…", POST `/api/catalog-tasks`); other returns
  of the anomaly as chips. Fixed line: "A catalogue issue. The customer's return stays regular."
- **`InsufficientEvidencePanel`** (RET-000109): title "Not enough evidence to score: standard terms apply";
  the three components as threshold bars with "below threshold" in words; `missing_evidence` as a list; line
  "Standard terms apply until there is enough evidence. The case is not placed in the matrix." Neutral grey,
  dashed borders, no quadrant, **no risk number anywhere** (US-307).

#### 6.2.3 Evidence column sections (`SectionNav`, each collapsible, state remembered per browser)

1. **Evidence quality**: three threshold bars (observations, identifier completeness, reason consistency)
   with "meets threshold" or "below threshold" in words. Compact when sufficient; shown on every path.
2. **Risk** (`RiskPanel`, only when `risk` is non-null):
   - `ScoreTrack`: neutral 0 to 1 track, threshold tick labelled with its basis, value marker, and "above
     the threshold" or "below the threshold" (from `band`; the word "high" is never shown about a person).
   - `FactorList`: exactly 3 rows: phrase, "raises" or "lowers", signed contribution, bar diverging from a
     centre axis (raises solid ink-2 to the right, lowers hatched outline to the left). Title "Contributions
     to the score, not causes". Feature values and SHAP values are `PlainNumber`s under `risk.provenance`.
   - `ResidualIndicator`: "z = 1.90" with the contract's `residual_note` verbatim.
   - **`EvidenceBalance`**: "Favourable to the customer (n)" and "Unfavourable to the customer (n)" in two
     equal neutral columns; inside each, Observed, then Score contribution, then To verify; refs as links
     that scroll to and highlight an order, a link, the cluster or a customer; an empty column says "None
     recorded" and is never hidden. "Add favourable evidence (`E`)" sits in the favourable heading. There is
     no "add unfavourable" action.
3. **Value** (`ValuePanel`): value against threshold with the band in words; the three parts as an equation
   (kept margin − return logistics − coupon discounts, each a `Figure`); the contract `legend` in a bordered
   note directly under the value, never collapsible.
4. **Orders** (`OrderHistoryPanel`, `OrderTermsPanel`): 12-month orders with nested lines (variant, size,
   price, returned or kept, reason, return id link), coupon kind and code, terms snapshot link; totals for
   lines, returned lines and return rate. The returned item of this case is highlighted. Order terms: "Terms
   of order ORD-005230 (snapshot TS-005230, base terms) shown at checkout on 18 Sep 2026, 20:14 UTC. They stay
   valid for this order."
5. **Alternatives** (US-308): up to three `AlternativeCard`s side by side: kind label ("Engine's proposal"
   with the tag "recommended by the engine", "Current terms", "Size exchange", "Check at the warehouse",
   "Product report"), summary, `LeverList`, `NetBenefitBreakdown`, `AssumptionLabel`, period, nature
   simulated, the refund footnote. A non-recommended card whose levers differ from the proposal has "Use these
   terms instead…", which opens the override sheet prefilled with them. For `verification`, `size_exchange`
   and `product_report` the card adds "The decision applies only the levers; the operational step is
   guidance."
6. **Explanation and message**: `ExplanationList` and `MessagePreview` (5.4), with "Regenerate… (tone,
   languages)" (6.3.6). The header reads "Generated explanation and message · cannot change the decision" and
   shows **`DecisionEchoCheck`**: "Echo matches the engine decision: withdrawal · Q4 · 6 levers". If
   `draft.decision_echo` differs from `{path, quadrant, lever_ids}` of the decision shown, the draft is hidden
   behind "Draft withheld: it does not match the engine decision" (danger) and the request id is logged.
7. **Product**: `ProductAnomalyPanel` when present and not already the hero.
8. **History**: `VersionChain` (v1 → v2 with trigger labels: initial, evidence added, link disputed, scores
   refreshed, review), each node opening the decision record drawer; `AuditEntryList` for this return; "Export
   CSV" (GET `/api/audit?return_id=…&format=csv`).

#### 6.2.4 Decision rail (sticky; the only place where the outcome and the actions live)

1. Header "Engine decision" with `AuthorshipLabel` "Policy engine · rules policy-2026-10-12 · scores
   risk-xgb-0.1.0" and the line "Rules decided this. The language model only drafts text."
2. `RefundFloorNotice` (always first on the withdrawal path).
3. `ApprovalGate` (`.hatch-attn`, only when proposed and `requires_approval`): "Needs your approval ·
   restricts terms for future orders · review 27 Dec 2026 · also pauses coupons for 7 linked accounts. This
   order keeps its terms."
4. `TermsDiff`, `ReviewDateChip` or `RestrictionBadge`, `ApproverLine` when decided.
5. `CouponApplicationNotice` (when `coupon_rule`): "Coupon pause CRA-000031 for 7 linked accounts of
   CL-000417 · rule coupon-rule-v1 · reversible · approving confirms it". After approval: "Confirmed by sara ·
   review 27 Dec 2026 · [Reverse…]".
6. `ClaimNotice` (when `diminished_value_claim`, Valentina): "Diminished-value claim €17.98 · confirmed by
   ahmed · rule DV-RULE-1".
7. `DecisionChain`.
8. `AlternativesStrip`: one pill per alternative with its total (simulated) and "engine" on the recommended
   one; clicking a pill scrolls to the Alternatives section.
9. Message summary: send language, `GenerationBadge`, "Preview ↓".
10. `ActionBar`, pinned at the bottom of the rail.

**Actions by decision state** (operator and manager; for warehouse every action is disabled with the role
reason; with `VITE_READ_ONLY` every action is disabled with "Read-only public demo"):

| Decision state | Primary | Secondary |
| --- | --- | --- |
| proposed, `requires_approval` (Q3, Q4) | **Approve restriction… (`A`)** opens `ApproveRestrictionSheet` | Override… · Reject… |
| proposed, no approval, withdrawal (Q1, Q2, not enough evidence) | **Confirm and send message (`A`)** opens `ConfirmPopover` | Override… · Reject… |
| proposed, guarantee path | **Confirm guarantee return and send message** | Override… (restrictive levers disabled) · Reject… |
| approved or overridden restriction, in force | none | Lift… (`L`) · Renew… (only with `HAS_RENEW`) |
| restriction with `review_due` (Lorenzo) | **Review… (`L`)** opens `ReviewSheet` | none |
| rejected, overridden without restriction, superseded | none; read-only summary of who, when, why | none |
| `draft_status: pending` | message-sending actions disabled with the printed reason "Message draft being prepared; usually ready within seconds" | the card polls every 3 s for up to 60 s, then offers Regenerate |

#### 6.2.5 Case variants (every queued return has a card in the examples)

| Case | Example | What changes |
| --- | --- | --- |
| Marco, Q4, cluster, coupon pause | `case_card.marco.json` | Cluster hero; gate with 6 levers including the cluster coupon pause; "Approve restriction…" |
| Martina, Chiara, Q3 (deliberately gentle) | `case_card.martina.json`, `case_card.chiara.json` | Gate with 2 levers (instant refund off, transparency notice). Chiara: the card link is "shared by 41 accounts: weak link" and its row suggests Dispute |
| Giulia, Andrea, Q1 upgrade | `case_card.giulia.json`, `case_card.andrea.json` | No gate; changes marked "↑ more generous"; "Applies by itself · logged"; "Confirm and send message" |
| Rossi, Rossi with shared card, Q2 with a cluster | `case_card.rossi.json`, `case_card.rossi_card.json` | Cluster hero shown neutrally; "No change to terms"; "Explain with evidence…" on the card link |
| Davide, not enough evidence | `case_card.insufficient.json` | `InsufficientEvidencePanel`; no risk anywhere |
| Elena, Francesca, guarantee path | `case_card.elena.json`, `case_card.francesca.json` | `GuaranteeBanner`; Elena's hero is the product anomaly |
| Lorenzo, approved Q3 past review | `case_card.lorenzo.json` | `ReviewDueBanner`: "Restriction approved by luca on 30 Jun 2026 · review date 27 Sep 2026 passed. It no longer applies to new orders. Renew it or lift it."; primary "Review…" |
| Valentina (inspected, claim), Simone (manual review) | `case_card.valentina.json`, `case_card.simone.json` | `ClaimNotice`; Simone: "Inspection: verify by hand" notice |

### 6.3 Action sheets and requests

All sheets are right-side `Sheet`s (560 px) over the Decision rail, so the evidence column stays visible.
Every sheet first refetches the card; if `decision.decision_id` changed, it opens in the stale state (6.4)
instead of the form. Decisions are **never optimistic**: the UI waits for the `DecisionResult`.

#### 6.3.1 `ConfirmPopover` (non-restrictive)

- Anchored to the button: "Confirm the proposal and send the message in Italian · RET-000101", the first two
  lines of the message, a language switch, "Acting as sara (operator)". `Enter` confirms, `Esc` cancels.
- Request: `{decision_id, action: "approve", levers: <the proposal's levers>, message: {lang, text: null},
  override_reason: null, override_category: null, approver}`.

#### 6.3.2 `ApproveRestrictionSheet` (Q3, Q4, any `requires_approval`)

```
┌ Approve restrictive terms for future orders ─────────────────────────────── Esc ┐
│ RET-000102 · C-MARCO-1 Marco Bianchi · Q4 · DEC-000102-1 v1                      │
│ What changes from the next order                                                 │
│   Return window         30 days → 14 days (legal minimum)            ↓           │
│   Return shipping       free → paid by the customer                  ↓           │
│   Instant refund        yes → no, refund within the legal terms      ↓           │
│   Coupons               on → off                                     ↓           │
│   Cluster coupons       paused for 7 linked accounts                 ↓           │
│   Return-rate notice    sent with this message                       ↓           │
│ What does not change                                                             │
│   ✓ This return proceeds under the terms of order ORD-005230 (TS-005230)         │
│   ✓ Refund due by 12 Oct 2026 · the 14-day withdrawal right stays on every order │
│   ✓ Free size exchange                                                           │
│ When it is shown: at checkout, before the customer's next purchase               │
│ Until when: review on 27 Dec 2026 · lapses unless a person renews it             │
│ ┌──────────────────────────────────────────────────────────────────────────────┐ │
│ │ [ ] I confirm the coupon pause for the 7 linked accounts of CL-000417         │ │
│ │     (coupon-rule-v1, CRA-000031). Reversible at any time. C-MARCO-1 … ▾       │ │
│ └──────────────────────────────────────────────────────────────────────────────┘ │
│ Message  (•) Italiano ( ) English   [Preview ▾ with layers]                      │
│ You: sara (operator). Your id is recorded as the approver in the audit log.      │
│ Want different terms? Override instead (O)                                       │
│                         [Cancel  Esc]   [Approve restriction  Ctrl+Enter]        │
└──────────────────────────────────────────────────────────────────────────────────┘
```

- Rows come from `TermsDiff` data; only changed levers are listed under "What changes".
- The coupon acknowledgement appears only when `coupon_rule` is non-null and is required (approval confirms
  the application). To approve without it, Sara overrides.
- The confirm button uses the attention fill, never the accent blue. There is no default button: `Enter`
  does nothing; `Ctrl/Cmd+Enter` or a click confirms. While disabled, the reason is printed under the button.
- Request: as 6.3.1 (`decision_request.marco_approve.json` → `decision_result.marco_approved.json`).

#### 6.3.3 Result handling (every decision action)

1. Put `result.decision` into the card cache, then refetch the card, queue, counts, audit and outbox.
2. Toast: "Approved by sara · message MSG-000102-1 queued (simulated) · audit entry #…", ids linking to the
   outbox and the audit log (from `outbox_message` and `audit_entry`).
3. The rail shows `ApproverLine`, "In force for future orders · review 27 Dec 2026", and the coupon notice
   "Confirmed by sara · [Reverse…]"; `DecisionChain` step 4 completes.
4. Auto-advance to the next case of the `?from` tab (on by default, toggle in the queue menu). There is no
   undo: corrections go through lift, reverse or a new decision.

#### 6.3.4 `OverrideSheet` (US-306)

- **`LeverEditor`** prefilled from the proposal or a chosen alternative ("Start from: Proposal · Current terms
  · …"). The window is an integer stepper with minimum 14 and the hint "14 days is the legal minimum";
  booleans are toggles; each lever shows its scope; restrictive changes carry a "restrictive" tag. On the
  guarantee path, every lever less generous than the base is disabled with "Not allowed on the guarantee path
  (rule 4)".
- **Legal guard preview**: the rules the guard will apply (window ≥ 14 days, no restriction on the guarantee
  path, nothing retroactive on the current order). The server stays the authority.
- **`CategorySelect`** (required): evidence disagreement, customer context, linked accounts explained, policy
  exception, other. `periodic_review` is reserved for the review sheet.
- **`ReasonField`** (required, ≥ 3 trimmed characters, counter, hint "One sentence a colleague would
  understand").
- If the chosen levers are restrictive: the gate block, "Review date: set by the server, 90 days after the
  decision (assumption)", and the `Ctrl+Enter` attention confirm. After success, the actual `review_at` shows.
- Message: send language only; `message.text` stays null.
- Request: `decision_request.override.json` → `decision_result.override.json` (Martina, levers `[]`,
  `customer_context`).
- **422 `legal_guard`**: `LegalGuardViolations` above the editor, each `code` and `message` plus a plain line
  (GUARANTEE_RESTRICTED → "Guarantee-path returns can't receive restrictive terms."), offending rows outlined
  in danger, "Nothing was saved." The sheet stays open. Demo: `decision_request.elena_shipping.json` →
  `error.legal_guard.json`.
- **422 `invalid_request`**: `violations` mapped onto fields, a summary at the top.

#### 6.3.5 `RejectSheet` and `ReviewSheet`

- **Reject**: fixed copy "Reject the proposal. No lever changes, this order's terms stay, and the return
  itself is never refused: the withdrawal right is intact." Category and reason required; `Ctrl+Enter`.
  `decision_request.reject.json` → `decision_result.reject.json` (Andrea).
- **Review (lift or renew, US-607)**: the restriction (levers, approver, approved at, review date, state from
  the API) and two choices:
  - **Lift: back to standard terms** (`action: "lift"`, category `periodic_review` by default, or
    `customer_context` for a rectification the customer asked for). Copy: "Creates version n+1 with the
    standard terms; the earlier version stays in the log." `decision_request.lift.json` →
    `decision_result.lift.json` (DEC-000088-2, trigger review, overridden, no levers, supersedes DEC-000088-1).
  - **Renew** (pending contract action): rendered only when the generated action enum contains `"renew"`
    (`HAS_RENEW`, section 11.1). Category and reason; "the next review date is set by the server".

#### 6.3.6 Regenerate (US-304, US-305)

- `RegenerateMenu`: languages (Italiano, English) and tone (neutral, warm, formal). No free-text instruction
  (NFR-11). POST `/regenerate` (`regenerate_request.json` → `regenerate_response.json`).
- While it runs, the old draft stays visible, dimmed, with "Generating and validating…". Nothing streams: the
  validator checks the whole output first.
- On success the new `GenerationBadge` appears; the old draft remains in History.
- 503 `llm_unavailable`: `LlmUnavailableNotice` inline in the panel; Regenerate disabled for 10 s; the
  top-bar pill turns to "LLM unavailable". Never a modal.

#### 6.3.7 Evidence and dispute (US-310, flow F7)

- **`AddEvidenceSheet`**: kind (Household members · Shared payment explained · Collective address or pickup
  point · Customer contact · Other) with a helper per kind ("explains the selected links: they leave the
  cluster features" or "recorded; changes no feature"); target links (multi-select from `LinkTable`, required
  for the three link-explaining kinds, preselected from the clicked link); note (≥ 3 characters); sends the
  `decision_id` shown and `author`. `evidence_request.rossi_card.json` → `recompute_response.rossi_card.json`.
- **`DisputeLinkSheet`**: the link preselected; reason required; sends `return_id`, the `decision_id` shown
  and `author` to POST `/api/links/{link_id}/dispute`. Copy: "One dispute per link. Every open case of the
  cluster is recomputed; the dispute is logged." `link_dispute_request.json` → `link_dispute_response.json`.
- **`RecomputeComparison`** (modal after either): two columns "Before · version 1" and "After · version 2"
  from the `CaseSnapshot`s: decision id and version, quadrant glyph, evidence quality, risk `Figure`, top
  factors, lever ids, approval flag, cluster size, active links. Changed rows carry a "changed" marker and an
  arrow, not colour alone. Then the new decision state, the regenerated draft badge, the audit entry ids and,
  for a dispute, "Also recomputed: RET-…" (`affected_return_ids`). Footer: "Both versions stay in the audit
  log. Only version 2 can be acted on." `Enter` = "Open version 2". Examples: Rossi with card (risk 0.34 →
  0.21, Q2 stays Q2, active links 3 → 2); Chiara's dispute (Q3 → Q1, the gate disappears).

#### 6.3.8 Coupon reverse

"Reverse…" on `CouponApplicationNotice` opens the shared `ReverseCouponDialog` (5.7).

### 6.4 Concurrency: newer versions and 409

- **While viewing**: on window focus and every 60 s, the open card refetches. If `decision.decision_id`
  changed: a non-blocking `NewerVersionBanner` "A newer version exists: v2 (evidence added by luca, 18:30 UTC).
  You are viewing v1." with "Show what changed" (a client-side before/after of the two cards) and "Load v2".
- **409 `stale_decision`** on any action (`error.stale_decision.json`, scenario `stale_decision` on the
  evidence route): `StaleDecisionPanel` replaces the sheet's confirm area: "This case changed while you were
  reviewing. DEC-000104-1 is not the latest version of RET-000104: DEC-000104-2 is. Nothing was saved." Actions
  "Show what changed", "Load the new version" (refetch, show the diff, reopen the sheet with the typed reason
  and category preserved), "Close". No automatic retry: the new version may propose something else.
- **409 `conflict`**: inline "Already decided by luca at 18:20 UTC" (or "the levers differ from the
  proposal"), "Show current", refetch; nothing saved.

### 6.5 Compare `/compare?a=RET-000101&b=RET-000102` (pitch beat 1)

```
Same return rate, different action
┌ Giulia Conti · C-GIULIA ──────────────────────┐   ┌ Marco Bianchi · C-MARCO-1 ────────────────────┐
│ RET-000101 · Dress · Size issue               │   │ RET-000102 · Coat · Changed mind              │
╞═ Return rate, 12 months  54.5% (6 of 11 lines) ◆ ══ SAME INDICATOR ══ 54.5% (6 of 11 lines) ◆ ═════╡
│ Top score factor (contribution, not cause)     │   │ Each of the 7 linked accounts used a welcome… │
│ Linked accounts: none                          │   │ Cluster of 7 accounts, created within 9 days  │
│ Risk score 0.12 ◆ ─●───|────── threshold 0.60  │   │ Risk score 0.87 ◆ ───────|───●── thr. 0.60    │
│ Customer value €211.85 ◆                       │   │ Customer value €42.10 ◆                       │
│ ▦ Q1                                          │   │ ▦ Q4                                          │
│ Engine: 60-day window, priority size exchange ·│   │ Engine: legal-minimum terms for future orders,│
│ applies by itself, logged                      │   │ coupons paused for the cluster · needs a      │
│                                                │   │ person's approval                             │
│ This return: refund by 11 Oct 2026, never      │   │ This return: refund by 12 Oct 2026, never     │
│ refused                        [Open case →]   │   │ refused                       [Open case →]   │
└────────────────────────────────────────────────┘   └───────────────────────────────────────────────┘
A rule on return rate alone would treat both the same.
```

- Data: two cards. The band uses `orders.return_rate`, `orders.returned_lines`, `orders.lines`. Other rows:
  `risk.top_factors[0].phrase`, `linked_accounts.cluster_size` and `features.creation_spread_days`, risk and
  value with thresholds, `QuadrantBadge`, `decision.lever_ids` as a compact `LeverList` with
  `requires_approval` and `in_force`, `refund_due_by`.
- In presenter mode the rows below the band reveal on `→` (step 2); otherwise all show at once.
- Any two return ids work through the query parameters (Q&A).

### 6.6 Decision record drawer `/returns/:id/versions/:decisionId` (US-604)

A 640 px right drawer over the card, from `VersionChain`, audit entries or a deep link. GET
`/api/decisions/{id}`.

- `SupersededBanner` when `superseded_by` is set: "Version 1 was superseded by DEC-000106-2. Read-only."
- Inputs (order, customer, reason, delivery and notice dates, order and base terms, risk and value with
  thresholds, evidence quality, cluster id) under the record `provenance`; each input that is a `Metric`
  renders as a `Figure` (and per-input provenance once the contract adds it).
- Engine output, alternatives with `AssumptionLabel`, human outcome (state, approver, time, reason, category),
  versions of rules, models and assumptions, generation id; "Copy decision id"; "Open audit entries for this
  decision".

### 6.7 Outbox `/outbox`

- Banner: "Simulated outbox: no email leaves the system."
- List: subject, customer, language, status "Sent (simulated)", clause kind, created by, created at;
  `?return_id=` filter.
- Detail: `MessagePreview` built from `connective_text`, `fields` and `body`, with links to the decision and
  the generation.

---

## 7. Agent area (view 2): the case assistant

Stories US-701, US-702. Directory `src/views/agent/`. **One public export**:

```ts
// src/views/agent/index.ts
export { CaseAssistant } from './CaseAssistant';
export interface CaseAssistantProps {
  returnId: string;
  open: boolean;
  onOpenChange(open: boolean): void;
  onRefSelect?(ref: EntityRef): void; // {kind: 'cluster'|'link'|'customer'|'order'|..., id}
}
```

The operator area mounts it in the case card; `onRefSelect` scrolls to and pulses the cited link, cluster,
customer or order (graph edge or node, link row, order row).

```
┌ Ask about RET-000102 · read-only assistant (Gemma 4) ─────────────────────────────── × ┐
│ It can read the case; it cannot change it.                                              │
│ Suggested: [Which evidence supports the link between C-MARCO-1 and C-MARCO-3?]           │
│            [What makes this case different from the Rossi family?]                       │
│            [Which terms applied when the order was placed?]                              │
│ [Ask a question about this case…                                         ] [Ask  Enter] │
│ Step 1 of at most 5 · Read cluster ✓  Cluster CL-000417: 7 linked accounts; …  [CL-000417]│
│ Step 2 · Read orders ✓  C-MARCO-3: 2 orders, welcome coupon redeemed …         [C-MARCO-3]│
│ Answer                                                                                   │
│ C-MARCO-1 and C-MARCO-3 are linked by a payment card, a device and a delivery address…   │
│ Evidence consulted                     │ Evidence missing                                │
│ [CL-000417] [C-MARCO-1~C-MARCO-3] …    │ …                                               │
│ Gemma 4 · validated first try · mock · live                                             │
│ Read-only: 5 tools, at most 5 steps. Answers cannot change the decision.                │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Request**: POST `/api/agent/ask` `{return_id, question, lang: "en", asked_by}`; the response is
  `text/event-stream`, read with `fetch` and the `sse.ts` parser (EventSource cannot POST). An
  `AbortController` cancels the stream when the drawer closes or a new question is asked.
- **Events, in order**: `tool_call`/`tool_result` pairs (n ≤ 5) → `token` deltas → exactly one `answer`,
  `refusal` or `error` → `done`.
- **`AgentStepList`**: one row per pair: "Step n of at most 5", the tool in plain words (Read case, Read
  orders, Read cluster, Read score factors, Read policy text), arguments, ✓ or ✗ (`ok`), the summary, ref chips.
- **Streaming answer**: tokens append without animation into a region with `aria-busy="true"`; on `answer`
  the full text replaces the token text and one polite announcement fires.
- **`AgentAnswer`**: the text in `GeneratedText`, then two equal columns "Evidence consulted" (ref chips that
  call `onRefSelect`) and "Evidence missing" (a list). `done` shows the `GenerationBadge`.
- **Refusal** (`write_request`): neutral card "The assistant only reads the case. Approvals and changes are
  made on the case card." with "Go to the decision panel". `out_of_scope` and `step_limit` get their own copy.
- **Errors**: `error` event or 503 → `LlmUnavailableNotice` ("The decision is unaffected"); 429 → "Too many
  questions in a short time. Try again in a few seconds." and input disabled for 10 s; 403 →
  `ForbiddenRoleNotice`; warehouse sees the button disabled.
- **Cached mode**: only the three suggested questions are enabled; free text shows "Free questions need the
  live model; these answers were precomputed on the MI300X."
- Session history: earlier questions and answers of this drawer stay listed above the latest (not persisted).
- Examples: `agent_ask_request.marco_{link,rossi,terms}.json` → `agent_stream.marco_*.json`/`.sse`;
  `agent_ask_request.write.json` → `agent_stream.write_refused.json`.
- **Dev harness** `/dev/agent?return=RET-000102` (dev builds only) renders the drawer standalone so the agent
  area can be built before the case card exists.

---

## 8. Manager area (view 3): matrix, simulator, KPI, fairness, products, reviews, audit

Stories US-601..607. Directory `src/views/manager/`.

### 8.1 Portfolio matrix `/portfolio` (US-601)

```
Risk score × customer value · 503 scored customers ◆ · as of 30 Sep 2026   [Golden cases ✓] [Highlight cluster ▾] [Table view]
┌───────────────────────────────────────────────┬───────────────────────────────────────────┐  Quadrant terms
│ Q1 · 63 customers ◆ · €18,251 ◆ · returns ◆    │ Q3 · 3 customers ◆ · …                    │  (rules policy-2026-10-12,
│   ·  ·  ◎Giulia  ·   ·                         │      ·                                    │   static summary of spec 8.6)
│ ─ ─ ─ ─ ─ ─ ─ ─ value €185 (70th percentile of customer value) ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─│  Q1 60-day window, priority
│ Q2 · 406 customers ◆ · …                       │ Q4 · 7 customers ◆ · …                    │   exchange; applies by itself
│   ·· ·· ◎Elena ·  ·                            │      ⬡ CL-000417 (7 linked accounts)      │  Q2 standard terms
└───────────────────────────── risk threshold 0.60 (lowest risk with precision ≥ 0.8) ──────┘  Q3 no instant refund, notice;
Not placed: 24 customers with not enough evidence (no risk score) ◆   [Show list]                 needs approval
Customer value orients benefits and service. It is never a signal of abuse and never feeds the risk score.
Numbers in this chart: SYNTHETIC · …                                                           Q4 legal-minimum terms, coupons
                                                                                                paused for the cluster; needs approval
```

- **Data**: GET `/api/portfolio/matrix`: `thresholds` (with `risk_basis`, `value_basis`), `quadrants`,
  `insufficient_evidence`, `points`, `points_provenance`, `legend`.
- **`MatrixCanvas`**: Canvas 2D points (3 px, `--mark` at alpha .35, about 20,000 drawn in < 50 ms), with an
  SVG overlay for axes, threshold lines (dashed `--border-strong`, labelled with their basis), tiles and
  labels. x = risk score 0 to 1; y = value in €, linear, clamped to the 1st and 99th percentiles with "n
  customers outside the shown range".
- **Quadrant positions** equal `QuadrantGlyph`: Q1 top-left, Q3 top-right, Q2 bottom-left, Q4 bottom-right.
- **`QuadrantTile`s** (one neutral surface for all four): customers, value, returns in 12 months as
  `Figure`s. A click (or `?quadrant=Q4`) opens `QuadrantCustomerList`, a right drawer with the points of that
  quadrant (name, id, risk, value, cluster, returns, coupons), sortable. "Open case" looks up an open return
  with GET `/api/returns?q=<customer_id>` and links to `/returns/:id` (open question O-12).
- **Golden annotations** (toggle): Giulia ◎; a convex hull around the CL-000417 points labelled
  "CL-000417 · 7 linked accounts"; the Rossi clusters CL-000233 and CL-000588 as hulls; Elena ◎ with "her open
  return follows the guarantee path and is not scored" (she is Q2 as a customer). "Highlight cluster ▾" rings
  any `cluster_id` in accent.
- Hover and focus on a point: display name, id, risk, value, quadrant, cluster, returns, coupons.
- **Insufficient strip**: "Not placed: 24 customers with not enough evidence (no risk score)" with a list
  drawer; never plotted.
- **Legend** (the contract `legend`) directly under the value axis title, same size as axis labels, always
  visible.
- **`QuadrantPolicyCard`**: the spec 8.6 lever summary, labelled "static summary of rules
  policy-2026-10-12" until the API exposes the table (open question O-19).
- **Table view**: quadrant rows plus the not-placed row.
- **Cover mode** `?cover=1`: a 1920 × 1080 render with Giulia and CL-000417 highlighted, for the 16:9 cover.

### 8.2 Economic simulator `/simulator` (US-602, ADR-04)

```
Economic simulator  ◌ SIMULATED · estimates under explicit assumptions, not realised savings
Baseline: Navigli base policy (spec 4), same period · assumptions-v1 by system · period: month, 1,000 return requests
┌ AssumptionForm ─────────────┬ NetBenefitWaterfall ─────────────────────────┬ Scenarios ───────────────────┐
│ Returns avoided       [80]  │ +640 logistics costs avoided   ████          │ [Base +€368]                 │
│  × cost per return [€8.00]  │ +300 undue discounts avoided   ██            │ [Logistics halved +€48]      │
│ Undue discounts avoided[25] │ +88  time saved                █             │ [Abandonment doubled +€128]  │
│  × average discount[€12.00] │ +0   incremental margin                      │ [Both −€192]                 │
│ Minutes saved        [240]  │ −200 incentives               ▨▨▨            │ Scenarios, not confidence    │
│  × hourly cost     [€22.00] │ −120 system                    ▨▨            │ intervals                    │
│ Incentives          [€200]  │ −100 errors                    ▨             │ Realised: not observed yet   │
│ System             [€120]   │ −240 abandonment margin       ▨▨▨▨           ├ Threshold sandbox ───────────┤
│ Errors             [€100]   │ = +€368 net benefit ◌                        │ risk  [====|=====] 0.60      │
│ Abandonment margin [€240]   │ The refund owed to the customer is never     │ value [===|======] €185      │
│ Incremental margin   [€0]   │ counted as a saving and has no field.        │ Q4 7 → 12 (+5) · Q3 …        │
│ No refund field (rule 9)    │ Origin: illustrative example from the        │ Preview only: thresholds     │
│ Note [……]  [Save as v2]     │ proposals document; not a ReturnRing result  │ here do not change the engine│
└─────────────────────────────┴──────────────────────────────────────────────┴──────────────────────────────┘
History: assumptions-v1 (system) · …   [Compare versions]
```

- **Data**: GET `/api/simulator/assumptions` (`current`, `history`; `net_benefit` and four `sensitivity`
  scenarios computed by the server); GET `/api/portfolio/matrix` for the sandbox.
- **Live recompute in the browser** (ADR-04) with `src/sim/netBenefit.ts`. Parity test ECO-01-UI: v1 gives
  +€368 with scenarios +€48, +€128, −€192; v2 gives +€248 and its scenarios, to the cent. If local and server
  results differ for the same inputs: "Browser and server results differ: showing the server value", logged.
- **Waterfall** (4.8): clicking a scenario chip morphs to it (300 ms) and shows its `AssumptionLabel`
  (`assumptions-v1/both`, based on `assumptions-v1`). A negative total reads "Net benefit is negative under
  these assumptions" in words, hatched, never red.
- **`AssumptionForm`**: every field with unit, grouped (volumes, unit costs, costs, incremental margin); a
  slider plus a number field; edits show "Unsaved scenario · computed in your browser · simulated".
- **Threshold sandbox**: moving the risk and value thresholds re-buckets the matrix points in the browser
  (< 16 ms per move) and shows per-quadrant counts before → after. It **does not feed the waterfall**: the
  contract does not define how quadrant membership becomes returns avoided (open question O-6). "Reset to
  engine thresholds".
- **Save** (manager only, `RoleGate`): note required; PUT with `base_version` = current and `author`.
  409 → "assumptions-v2 was saved meanwhile. Your edits are kept; review the differences and save on top of
  it." with `AssumptionDiff`.
- **History**: versions with author, time, note, supersedes, total; "Compare versions".
- Fixed formula footer: B = C avoided + M incremental + T saved − I − S − E − A.

### 8.3 Weekly KPIs `/kpi?week=2026-09-21` (US-603)

Week picker (`week_start`). Each tile carries a `ProvenanceFooter`.

- **Net benefit, simulated**: hero `Figure` (+€140.83) with `AssumptionLabel` `kpi/2026-09-21` and the
  components in a disclosure. **Realised**: "Not observed yet: realised benefit needs observed outcomes"
  (null, never 0).
- **Decisions**: total, approved, overridden, rejected, pending as one stacked bar with direct labels.
- **Decision time**: p50 and p95.
- **Abstentions (not enough evidence)**: count and rate, linking to the queue tab.
- **False positives by cohort**: dot plot (all, new customers, households, low data, high value) with
  customer counts; header "From the offline evaluation report `evaluation_report_id` · synthetic labels"; the
  period printed inline because it differs from the week; note "Labels come from the offline evaluation; the
  running API never reads ground truth."
- **Override reasons** by category; **open catalogue tasks** linking to `/products/tasks`.

### 8.4 Fairness `/fairness` (US-605)

- Header: report id, scenario, model version, maximum gap threshold (5 pp, `Figure`).
- One `FairnessDimensionCard` per dimension (new customers, households, low data, tenure, price band,
  category): a dot plot of false-positive rate by segment with customer counts, a bracket for `max_gap` and
  the threshold line.
- `warning: true` (low data, 6.1 pp): attention border and banner "Gap above the 5-point threshold: needs
  review." It flags the model's errors, never a group.
- Fixed footer: "Gender, country, age, names and raw identifiers are not model inputs (ADR-11). Excluding them
  is not enough, so false positives are measured by segment."

### 8.5 Products and catalogue tasks `/products`, `/products/:anomalyId`, `/products/tasks` (US-208, US-606)

- **Anomalies tab**: variant, product, category, brand, reason, week, observed vs expected, z, sales, status
  (Open · Task opened · Resolved), linked returns, sparkline. Header: "Flagged when z > 3 with at least 20
  sales in the week (project thresholds)" from `thresholds`.
- **Detail**: observed vs expected summary; `WeeklyTrendChart` (returns as bars, expected as a dashed line,
  both counts on one axis; anomaly week labelled "z = 6.86") and a separate small return-rate chart; linked
  returns with "These returns stay regular; defect returns follow the guarantee path"; **`CatalogTaskForm`**
  (kind: description, size guide, batch check; note; optional return id) → POST (201).
- **Tasks tab**: `TaskStatusStepper` (Open → In progress → Done or Cancelled) via PATCH (status, note,
  author). A done task shows `FollowUpPanel` (baseline rate vs rate after, weeks after) and the fixed caveat
  "A decrease alone does not prove the effect of the correction: that needs a controlled comparison."

### 8.6 Restrictions under review `/reviews` (US-607)

- Header note: "A restriction lapses at its review date and stops applying to new orders. A person renews it
  with a new decision or lifts it. No label is permanent."
- **Restrictions due** (GET `/api/returns?review_due=true`): return, customer, levers, approver and date,
  review date with "lapsed n days ago" (relative to `as_of`), decision link. Action "Review…" opens
  `/returns/:id?action=review` (the operator's `ReviewSheet`); "Open case".
- **Coupon pauses with review dates** (GET `/api/coupon-rules`, `applications`): the shared
  `CouponApplicationsTable` with "Reverse…". When the pending lapse fields land, a "Lapsed" state appears.
- Empty: "No restriction is due for review."

### 8.7 Audit log `/audit` (US-604)

- Banner: "Append-only log. Human actions are never recorded as the system; every restrictive approval names
  a person and a review date."
- Filters: return, decision, actor, action (multi), entity type, since, until; toggle "Human actions only".
- Table: time, `ActorBadge`, action as a phrase (map below), entity link, reason as a quote, request id
  (copy). Restrictive approvals carry a "Human approval · review 27 Dec 2026" pill. A row expands into
  `AuditDiff` and the versions (rules, models, assumptions).
- **Export CSV**: GET `/api/audit?…&format=csv` with the current filters, downloaded as
  `returnring-audit-<filters>.csv`.

| Action | Label |
| --- | --- |
| `decision_proposed` · `decision_approved` · `decision_overridden` · `decision_rejected` | Engine proposed · Approved · Overridden with other terms · Proposal rejected (return proceeds) |
| `restriction_lifted` · `case_recomputed` | Restriction lifted · Case recomputed |
| `draft_generated` · `draft_regenerated` · `message_queued` | Draft generated · Draft regenerated · Message queued (simulated) |
| `evidence_added` · `link_disputed` | Favourable evidence added · Link disputed |
| `checkout_terms_shown` · `checkout_confirmed` | Terms shown at checkout · Order confirmed with terms |
| `inspection_created` · `inspection_confirmed` | Inspection started · Inspection confirmed |
| `catalog_task_opened` · `catalog_task_updated` | Catalogue task opened · Catalogue task updated |
| `coupon_rule_approved` · `coupon_rule_application_reversed` · `assumptions_updated` | Coupon rule approved · Coupon pause reversed · Simulator assumptions updated |

---

## 9. Platform area (view 4): checkout, inspection, coupons, system, demo

Stories US-401..403, US-501..503, US-901, US-902, US-905, US-906. Directory `src/views/platform/`.

### 9.1 Simulated checkout `/shop` (US-401..403)

```
┌ SHOP VIEW · what the customer sees ───────────────┬ BEHIND THE SCENES · presenter only ───────────────┐
│ Navigli Fashion (fictional shop · synthetic data)  │ Customer C-GIULIA · Q1 · terms from DEC-000101-1  │
│ Cart                                               │ (applies by itself)                               │
│  Dress V-DRS-2500 · size M                         │ Line return probability vs threshold 0.35 ◆       │
│  Dress V-DRS-2500 · size L                         │  M 0.18 [--o-----|----] below                     │
│   Size tip: customers with your measurements kept  │  L 0.47 [--------|-o--] above → advice (specific) │
│   size M of this dress.  [Keep both] [Switch to M] │ Shown terms vs shop standard (TermsDiff)          │
│ Return terms for this order                        │ Snapshot TS-CHK-000981 stored at shown_at, before │
│  60-day return window · free return shipping ·     │ the answer                                        │
│  instant refund · free size exchange               │ Model propensity-xgb-0.1.0 ◆ · provenance         │
│  You can always withdraw within 14 days of         │ Evaluated in n ms ● measured in this browser      │
│  delivery, without giving a reason.                │ (US-403 target < 1 s)                             │
│ Coupon code [            ]                         │                                                   │
│ [Place order]                                      │ After the order: ORD-CHK-000001 · snapshot bound  │
└────────────────────────────────────────────────────┴───────────────────────────────────────────────────┘
Presets: [Giulia C-GIULIA] [Account C-MARCO-1 of cluster CL-000417]   (presenter pane)
```

- **Flow**: pick a preset (cart from the request examples); POST `/evaluate` on load and on every cart change
  (debounced 300 ms, skeleton while waiting); "Place order" sends POST `/confirm` with `terms_snapshot_id`
  (201); the shop shows "Order ORD-CHK-000001 placed. The terms above are saved with your order."
- **Shop view rules** (customer-facing): never quadrant, risk, score, cluster, linked accounts or the model.
  - Size advice only when present (p ≥ threshold): `specific` text plus "Switch to M";
    `measurement_guide` "Check the size guide for this item"; `no_reliable_recommendation` "We can't recommend
    a size reliably for this item." No probability shown.
  - Terms in words; the withdrawal line always present; terms less generous than the base add "These return
    terms apply to your account until 27 Dec 2026, when they are reviewed."
  - When `coupon_eligible` is false: neutral customer copy "Welcome coupons aren't available on this account
    until 27 Dec 2026." The contract's `coupon_block.text` is operator wording and appears only behind the
    scenes (open question O-11). Never error red.
- **Behind the scenes**: per-line `p_return` vs `p_return_threshold`, quadrant, `terms_source` (base, or a
  decision link with state, approver and review date), `TermsDiff` base vs shown, `coupon_block` rule and
  application, snapshot id and `shown_at`, model version, provenance. The client-measured latency is a REAL
  figure ("measured in this browser").
- **`SnapshotReceipt`** (E2E-02): compares the stored terms of the confirm response with the terms shown:
  "The terms shown are the terms stored (TS-CHK-000981) ✓", or a danger notice.
- Pending lapse fields: once checkout terms carry `in_force`/`review_due`, a lapsed restriction shows "Terms
  source DEC-… lapsed on …; base terms shown".

### 9.2 Warehouse inspection `/inspection` (US-501..503, Could)

Tablet first, 48 px targets, a stepper:

1. **Find the return**: a list of withdrawal-path returns (GET `/api/returns?path=withdrawal`) and search. On
   the guarantee path: "The inspection documents the item; no diminished-value claim applies (art. 14(2)
   concerns withdrawals)."
2. **Photo**: drop, choose a file or camera (`capture`); JPEG, PNG or WebP; read as base64 into
   `photo.data_base64`; files above 8 MB are refused client-side with a reason. POST `/api/inspections` (201).
3. **Checklist** (tag attached, visible damage, photo quality): the suggestion as a ghost hint ("Suggested:
   no · No tag visible") with "model confidence 0.78 · declared by the model, not calibrated" as muted text,
   never a progress bar; Ahmed's answer as Yes / No / Not observable with **nothing preselected**; all three
   required.
4. **Proposal**: shown only while a `proposal` exists **and** Ahmed answered Yes to every item in its
   `basis`: "Proposed claim €17.98 · visible damage, not resellable as new · rule DV-RULE-1", with "Apply the
   compensation" **off by default**. If his answers contradict the basis: greyed, "No claim: visible damage
   not confirmed."
5. **Confirm** (`Ctrl+Enter`): POST `/confirm`; the result card shows "Claim €17.98 attached to DEC-000110-1 ·
   confirmed by ahmed · message MSG-000110-1" and the message preview with the `{amount}` field highlighted.

- **Manual review** (`inspection.manual_review.json`): "The system stopped: photo too dark to check the tag
  and the fabric. Verify by hand." No proposal, no compensation control; observations only (open question O-22).
- Fixed copy: "A photo cannot establish the origin or time of damage. Suggestions are candidate observations
  that you confirm."
- Only warehouse can create or confirm; Sara and Luca see the steps read-only with the reason.

### 9.3 Coupon rules `/coupons` (US-402)

- **Current rule**: text verbatim, `rule_version`, "Approved by luca (manager) on 29 Sep 2026", history.
- **New rule version…** (manager): text and confirmation (`Ctrl+Enter`); POST (201).
- **Applications**: the shared `CouponApplicationsTable` and `ReverseCouponDialog` (operator and manager).
  Copy: "The general rule is approved and versioned by the manager; each application to a cluster is a proposal
  an operator confirms. Always reversible."

### 9.4 System and benchmarks `/system` (US-901, US-902, US-905)

```
┌ AMD Instinct MI300X · every model on one GPU ─────────────────────────────── ● Live · ready ──────┐
│ ArchitectureStrip: [vLLM · Gemma 4 31B] [XGBoost] [GraphSAGE] ← GPU │ api · ui · db ← CPU          │
│ ┌ B1 XGBoost training · ASOS ── Illustrative, not measured ▨ ┐ ┌ B2 GraphSAGE · ASOS ── Pending ┐ │
│ │ CPU    ███████████████████  (hatched, ink-3)              │ │ Not run yet. A benchmark of the  │ │
│ │ MI300X ██                                                 │ │ ASOS task, not evidence on       │ │
│ │ dataset · versions · config ▸                             │ │ account links                    │ │
│ └───────────────────────────────────────────────────────────┘ └──────────────────────────────────┘ │
│ ┌ B3 Gemma 4 31B on vLLM ── Illustrative, not measured ▨ ┐ ┌ B4 Nightly pipeline ── Pending ─────┐ │
│ │ latency p50 · p95 @ concurrency · throughput @ batch ·   │ │                                    │ │
│ │ cost per decision, idle time included                    │ │                                    │ │
│ └──────────────────────────────────────────────────────────┘ └────────────────────────────────────┘ │
│ Telemetry · every 5 s · GPU % ▁▃▇▆ · memory used / total · W · LLM requests · p50/p95 · tok/s · API p95 │
│ Readiness · database ✓ scores ✓ models ✓ vllm ✓ demo_cache – telemetry ✓ · contract 1.1.0 · versions │
└────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- Tabs on narrow screens: Readiness · Benchmarks · Telemetry.
- **`ModeCard` and `ComponentChecks`** (`/readyz`): mode, `llm_mode`, device, `label`, contract, rules,
  assumptions and model versions; each check ok / disabled / error with `detail`. `--ok` appears only here.
- **`BenchmarkCard`** (B1 to B4): status badge **Measured** (with `measured_at`), **Pending** ("Not run yet")
  or **Illustrative, not measured** (`.hatch-cached` overlay behind the chart and a diagonal "ILLUSTRATIVE"
  watermark, bars in ink-3). Dataset, versions, config and notes in disclosures. `ResultBars` pair CPU and
  MI300X per metric and phase, one small chart per unit; training, scoring, generation and cold start kept
  separate. A **`SpeedupBadge`** appears only when both results are measured and share metric, phase and
  protocol. Latency always shows its concurrency, throughput its batch size; cost reads "idle included".
- **`TelemetryPanel`**: polls every `interval_s`, pauses when hidden; small multiples (no dual axis): GPU
  utilisation (from `gpu_history`), memory used vs total, power; LLM requests, latency p50/p95, tokens/s; API
  p95. In cached mode it shows the `note` ("GPU off: cached mode serves results precomputed on the AMD
  MI300X") and **no numbers**, never zeros. A 401 from Caddy shows a "Protected" card with "Sign in at
  /admin/" (open question O-16).
- **`GpuDock`** (shell slot exported by this area): section 3.4.

### 9.5 Demo path `/demo` and `DemoRail`

- **Preflight** (green or red, each red names its fix):
  - `/readyz` is ready, and the mode is the one expected for this run (live for the pitch);
  - `vllm` and `telemetry` checks ok;
  - RET-000102 is `proposed` at version 1 ("Marco already approved: run the demo reset of US-903");
  - RET-000104's latest decision is DEC-000104-1;
  - the current assumptions are `assumptions-v1`;
  - the five golden cards and the matrix load in under 2 s.
- **Beats**: the six deep links of section 12 in order, each with a one-line "what to say" from spec 16.
- **Buttons**: "Start presenter mode" (adds `?present=1`, starts the timer), "Open cached URL"
  (`VITE_CACHED_ORIGIN`), and per beat "Open in a new tab" so the tabs can be pre-opened in order.
- **`DemoRail`** (shell slot): beat numbers, the current one highlighted, elapsed timer, `Alt+←`/`Alt+→`.
  `Shift+C` reloads the same route on the cached origin (failover; the striped banner makes it honest).

### 9.6 About the data `/about` (US-906)

Natures (5.1) with examples; "Golden cases are demonstrations with fixed ids, never accuracy"; declared limits
(synthetic data for linked accounts, legal validation pending); what "linked accounts" means; forbidden
features (rule 6); versions from `/readyz`. Every `ProvenancePopover` links here.

---

## 10. Cross-cutting states, copy and accessibility

### 10.1 States catalogue

Every error shows `request_id` with a copy button. Every non-2xx body is `ErrorResponse {code, message,
violations, request_id}`.

| State | Trigger | Where and how |
| --- | --- | --- |
| Loading | any query | Skeletons with final dimensions; the graph skeleton is a ring of grey circles; charts show grey axes; no spinner after 300 ms; the queue keeps its last list |
| Empty | 0 items | `EmptyState` with screen-specific copy and one next action |
| Error (network, 5xx) | fetch failure | Panel-level `ErrorState` "The service did not answer." + Retry; the rest of the page keeps working |
| 404 `not_found` | unknown id (`error.not_found.json`) | "Return RET-999999 not found" + "Back to queue" |
| 403 `forbidden_role` | wrong persona | Prevented by `RoleGate`; if received, `ForbiddenRoleNotice` inline in the sheet, nothing saved |
| 409 `stale_decision` | decision, evidence, dispute, regenerate | `StaleDecisionPanel` (6.4), typed reason kept |
| 409 `conflict` | already decided, levers differ, lift on a non-restriction, task or coupon state, assumptions not current, checkout confirm | Inline message + reload; assumptions get `AssumptionDiff` |
| 422 `legal_guard` | override refused | `LegalGuardViolations` in the sheet, "Nothing was saved" |
| 422 `invalid_request` | bad body | Field messages from `violations` |
| 429 `rate_limited` | agent | Input disabled 10 s with copy |
| 503 `llm_unavailable` | regenerate, agent | `LlmUnavailableNotice` inline; pill turns to "LLM unavailable" |
| Template fallback | `generation_path: template` | `GenerationBadge` "Reviewed template"; no error styling |
| Mock or cached text | `llm_mode: mock`, `served_from: cached` | `GenerationBadge` "Mock text (hand-written stand-in)" or "Precomputed on MI300X" |
| Draft pending | `draft_status: pending` | Skeleton + "Draft being prepared in batch"; message-sending actions wait (6.2.4) |
| Not enough evidence | `evidence.quality: insufficient` | Neutral, dashed, no risk, no quadrant |
| Guarantee path | `path: guarantee` | `GuaranteeBanner`, no risk, restrictive levers disabled |
| Review due / lapsed | `review_due` or `in_force: false` after review | `ReviewDueBanner`, Review action |
| Superseded version | `superseded_by` set | Read-only banner with "Go to the latest" |
| Message layers unverifiable | segments do not rebuild the stored text | Plain stored text with an attention note |
| Mixed mode | header vs `/readyz` | "MIXED MODE" banner |
| Protected | 401 on telemetry | "Protected" card |
| Offline | `navigator.onLine` false | Banner; actions disabled with the reason |
| Read-only demo | `VITE_READ_ONLY` | Write controls disabled with "Read-only public demo" |

### 10.2 Copy and wording (spec 16 "Formulazioni da evitare", rules 11 and 14)

- All UI strings live in `src/copy/common.ts` and `src/views/<area>/copy.ts`, in English. Customer messages
  are shown in Italian and English.
- **Never in UI-authored strings** (English and Italian): fraud, fraudulent, scam, suspicious, suspect,
  abuser, abusive, abuse, culprit, caught, blacklist, ban, blocked, flagged customer, same person, same
  customer, identity match, alias, bad customer, good customer, trusted, untrusted, VIP, ring (for a
  cluster; the brand name ReturnRing is allowed), "AI decided", "Gemma recommends", savings, money saved,
  legal by construction, compliant, certified; frode, truffa, abuso, sospetto, stessa persona.

| Instead of | Use |
| --- | --- |
| high-risk customer, suspicious | "risk score above the threshold (0.60)" |
| same person, alias | "linked accounts", "accounts that share identifiers", "cluster CL-000417 (graph id)" |
| good or trusted customer | "value above the threshold (€185)"; quadrant labels "Q1 · higher value · lower risk score" |
| block, ban, penalty | "pause coupons", "terms for future orders", "standard terms" |
| deny or refuse the return | never: "the return proceeds; refund due by 12 Oct 2026" |
| detected, proven | "the model estimates", "contributions to the score, not causes", "proposed link with evidence" |
| savings on returns | "estimated net benefit (simulated), under explicit assumptions" |
| legal by design | "checked by the legal guard; legal validation pending" |
| safe case | "not enough evidence to score: standard terms" |
| accuracy (golden cases) | "demo cases with fixed ids" |
| the AI decided | "the rules engine proposed; sara approved" |

- Required phrases: "linked accounts"; "contributions to the score, not causes"; "an indicator, not a
  probability" (residual); "restricts terms for future orders"; "this order keeps its terms"; "the refund
  owed to the customer is never counted as a saving".
- **Contract and model text is shown verbatim** (value legend, residual note, statements, agent answers). The
  COPY-LINT test covers UI-authored strings only; a Playwright check lists banned terms found in rendered API
  text as warnings and reports them to the API and LLM owners (the insufficient-evidence statement currently
  says "neither safe nor suspicious", open question O-20).
- **Customer-facing surfaces** (shop view, message previews) never mention risk, score, quadrant, cluster,
  linked accounts or the model.
- **No number literals in copy**: every number in prose is interpolated from data or from a named constant:
  generated from the contract where it exists (`LEGAL_MINIMUM_WINDOW_DAYS` from the lever schema minimum,
  the agent step limit), otherwise in `src/copy/constants.ts` with a source comment (the 90-day review
  horizon of US-607). COPY-LINT fails on digit literals in copy files and JSX text.

### 10.3 Accessibility rules (WCAG 2.2 AA)

1. Text ≥ 4.5:1, non-text UI ≥ 3:1, with the token table of 4.2 as the source; the CONTRAST test guards it.
2. Colour is never the only carrier: every hue comes with an icon and words; hatches back up colour.
3. Everything is keyboard reachable; focus is always visible (2 px `--focus`, 2 px offset) and follows the
   reading order; sheets and dialogs trap focus and return it to the trigger (Radix).
4. Every shortcut has a visible button; single keys are inactive in text fields.
5. The Cytoscape canvas is supplementary: `LinkTable` is always rendered and is the accessible source;
   selecting in either syncs the other. The matrix and every chart have a table view.
6. Streaming text uses `aria-busy` and one polite announcement on `answer`; toasts are polite; errors that
   need action are inline, never only in a toast.
7. `lang="it"` and `lang="en"` on message blocks; the page is `lang="en"`.
8. Targets ≥ 24 × 24 px, ≥ 44 px on touch layouts, 48 px on Inspection.
9. `prefers-reduced-motion` honoured; forced-colors keeps nature words and shapes.
10. `customer_text` and every model text render as plain text; `dangerouslySetInnerHTML` is banned by lint
    and a test (T-NO-HTML).
11. Lint with `eslint-plugin-jsx-a11y`; Playwright accessibility snapshots on the pitch routes (axe-core is
    MPL-2.0 and not allowed by rule 15).

---

## 11. Data layer and contract absorption

### 11.1 Types and generated constants

- `npm run gen:api` (also `make ui-types`):
  - `openapi-typescript contracts/json/api/openapi.json -o src/api/types.gen.ts`;
  - `scripts/gen-contract.mjs` writes `src/api/contract.gen.ts` with runtime constants read from the
    contract: enum arrays (decision actions, quadrants, states, override categories, evidence kinds, audit
    actions, lever ids and scopes) and the endpoint roles of `endpoints.json`.
- `HAS_RENEW = DECISION_ACTIONS.includes('renew')`, and similar capability constants, come from these arrays.
- CI fails when the generated files differ from a fresh run ("generated, never edited", ADR-15).
- **Components never import generated types directly.** They import `src/api/models.ts` (aliases and narrow
  selectors) and `src/api/adapters/*`. A contract change breaks the compile in a handful of adapters, not in
  forty components. `npm run gen:api && npm run typecheck` is the first step of any contract bump.

### 11.2 Client and cache

- `apiFetch` (`src/api/client.ts`): base URL `/`; JSON in and out; `X-Mock-Scenario` from the dev scenario
  switcher; stores `X-ReturnRing-Mode` and `X-Request-Id` in the mode store; maps non-2xx to
  `ApiError {status, code, message, violations, requestId}`; a 401 becomes the "Protected" state.
- **TanStack Query** keys: `['queue', query]`, `['queueCount', tab]`, `['case', returnId]`,
  `['decision', decisionId]`, `['audit', query]`, `['outbox', query]`, `['matrix']`, `['assumptions']`,
  `['kpi', week]`, `['fairness']`, `['anomalies', query]`, `['tasks']`, `['couponRules']`, `['benchmarks']`,
  `['telemetry']`, `['readyz']`.
- Freshness: queue stale 15 s, refetch every 30 s and on focus; case 60 s, 0 when a sheet opens; telemetry
  every 5 s while visible; matrix once per session, invalidated by a new assumptions version; a pending draft
  polls every 3 s for up to 60 s.
- After a mutation: merge the response into the cache (decision, draft, revisions, recompute), then
  invalidate case, queue, counts, audit and outbox. **Never downgrade**: a refetch with a lower decision
  `version` than the cache holds is ignored.
- Every action sends the `decision_id` it was shown (ADR-15).

### 11.3 Adapters (each tested against every file in `contracts/examples/api/`)

| Adapter | Purpose |
| --- | --- |
| `toMessageSegments(msg)` | 1.1.0: tokenise `connective_text` on `/\{(terms\|amount\|deadline)\}/` into `phrase` and `field` segments (values from `fields`); render locally; if that is a prefix of `rendered_text` (or `body`) followed by `\n\n`, the remainder is the `clause` segment; otherwise return `{verified: false, text}`. After the phrasebook change: map API sentence ids, body parts, fields and clause one to one, and drop the prefix check. |
| `toStatementVM(statement, facts?)` | 1.1.0 `{label, text, entity, metric, value, period}` → one `FactVM` with the card's nature; ADR-14 `{label, fact_ids, text}` + `facts[]` → `FactVM[]` with their own nature. |
| `restrictionStatus(view)` | Server fields only (5.3). |
| `parseAssumptionLabel(label)` | `<decision_id>/<kind>`, `kpi/<week>`, `<version>/<scenario>`, raw fallback. |
| `toLapseVM(entity)` | Lapse fields on checkout terms, snapshots and coupon applications when present; otherwise the review date only. |
| `useDecisionVersions(returnId)` | `card.revisions` + GET `/api/decisions/{id}` today; the superseding-versions route when it exists. |
| `inputFigure(record, key)` | A per-input `Metric` when present; otherwise a `PlainNumber` under the record provenance. |
| `draftEchoMatches(draft, decision)` | The echo check of 6.2.3. |

### 11.4 Pending contract changes and where they land

| Pending change | Lands in | Until then |
| --- | --- | --- |
| Restriction lapse fields (`in_force` or `review_due` at `review_at`) on checkout terms, snapshots and coupon applications | `toLapseVM` → `RestrictionBadge` in the shop's behind-the-scenes pane, `OrderTermsPanel`, `SnapshotReceipt`, `CouponApplicationsTable` | Only the review date; the client never derives a lapse |
| `renew` action | `ReviewSheet` second choice, ActionBar "Renew…", `/reviews` | Hidden (`HAS_RENEW` false); Lift available |
| Provenance on decision inputs | `DecisionRecordDrawer` inputs as `Figure`s via `inputFigure` | The record-level provenance covers the inputs |
| Assumption labels `<decision_id>/<kind>` | `AssumptionLabel` everywhere | Already parsed |
| Routes for superseding versions | `useDecisionVersions`, `VersionChain`, the newer-version banner | `revisions` + `superseded_by` |
| Operator statements citing code-built facts `{id, entity, metric, value, period, nature}` | `toStatementVM` → `FactCitation` chips with their own nature; digits in the text linked to facts by id | One pseudo-fact per statement with the card nature |
| Customer message from reviewed phrasebook sentences + code-built body + template fields + fixed clause | `toMessageSegments` → `MessagePreview` (`phrase`, `body`, `field`, `clause`); `PhrasebookPicker` (Could: one opening, up to three extras, one closing, filtered by the allowed ids, body locked) | Tokenised connective text; no editor; Regenerate only |

### 11.5 Mock server (`apps/ui/mock/returnring-mock.ts`, Vite dev middleware)

- Loads `contracts/examples/api/index.json` and serves every route by method, concrete path, scenario
  (`X-Mock-Scenario`, default `default`) and the matching keys below. Unmatched requests fall back to the
  first route of the path with a console warning and a dev-only banner "Mock: no example for this request;
  served <file>"; unknown paths get `error.not_found.json` (404).
- **Matching keys** (free text such as reasons and notes never has to equal the example):

| Route family | Keys compared |
| --- | --- |
| GET with a query example | the query parsed into its model with defaults, deep-equal to the example; otherwise the plugin filters `queue_response.json` items itself with the `QueueQuery.matches` semantics |
| POST `/decision` | `action` |
| POST `/agent/ask` | `question` |
| POST `/checkout/evaluate` | `customer_id` |
| POST `/inspections` | `photo.data_base64` equal to the manual-review example → that route, else the default |
| others | path and scenario only |

- Sets `X-ReturnRing-Mode` (`cached` for the `cached` scenario) and `X-Request-Id` on every response.
- Enforces `roles` from `endpoints.json` using the actor field of the body, so switching to Ahmed returns a
  real 403.
- Streams `agent_stream.*.sse` with 60 ms between frames so streaming is visible.
- **UI-only scenarios** until the contract ships examples (open question O-1), built with the
  `ErrorResponse` shape and labelled "UI fixture": `forbidden_role`, `llm_unavailable`, `rate_limited`,
  `conflict`, `empty`, `draft_pending`, `agent_error`.
- A dev-only `ScenarioSwitcher` sits in the mode banner on mocks: default, cached, stale_decision,
  invalid_request, plus the UI-only ones.

---

## 12. Demo click path (spec 16, the four golden cases)

Prerequisites: `/demo` preflight green; light theme; `?present=1`; browser tabs pre-opened in beat order; the
cached URL ready on a second origin; the demo reset (US-903) run after each rehearsal.

| Time | Beat | Route | Clicks | What the audience sees | API (example) | E2E-PITCH asserts |
| --- | --- | --- | --- | --- | --- | --- |
| 0:00–0:25 | ① Same indicator | `/compare?a=RET-000101&b=RET-000102` | `→` reveals the rows below the band | Giulia and Marco with the same 12-month return rate, 54.5% (6 of 11 lines), then the rows that diverge: factor, linked accounts, risk, value, quadrant, engine decision, "refund by …, never refused" | 2 × GET case (`case_card.giulia.json`, `case_card.marco.json`) | "54.5%" twice; "Same indicator" |
| 0:25–1:05 | ② Marco | `/returns/RET-000102` | 1) "Card" identifier chip; 2) the edge C-MARCO-1~C-MARCO-3; 3) "Approve restriction…"; 4) tick the coupon pause acknowledgement; 5) Approve (`Ctrl+Enter`) | Graph of CL-000417 (7 accounts, 21 links) with the card links lit; link popover (card, device, address; first shared; weight after decay); coupon sequence over 9 days; favourable and unfavourable side by side; this order vs future orders; then "Approved by sara", review 27 Dec 2026, message queued in Italian with its layers | `case_card.marco.json`; POST decision `decision_request.marco_approve.json` → `decision_result.marco_approved.json` | "Approved by sara"; "27 Dec 2026"; the restrictive button ignores a bare `Enter` |
| 1:05–1:35 | ③ Rossi | `/returns/RET-000103`, then `/returns/RET-000104` | 1) on RET-000104, the edge C-ROSSI-CARD-1~C-ROSSI-CARD-2; 2) "Explain with evidence…"; 3) kind "Shared payment explained" (presenter mode offers "Use demo note"); 4) Recompute | Three accounts sharing address and device, then the variant with a shared card; before and after: risk 0.34 → 0.21, quadrant Q2 → Q2, active links 3 → 2, standard terms before and after, explanation regenerated; the edge turns dotted "explained" | `case_card.rossi.json`, `case_card.rossi_card.json`; POST evidence `evidence_request.rossi_card.json` → `recompute_response.rossi_card.json` | "0.21"; "Open version 2" |
| 1:35–2:00 | ④ Elena | `/returns/RET-000105` | (optional) "Open in Product issues" | Guarantee banner, no score, no restrictive lever; hero: V-JKT-DEF, 11 returns for defect in the week of 21 Sep vs 1.8 expected (z = 6.86, 46 sales), trend chart, catalogue task CT-000012; "The customer's return stays regular" | `case_card.elena.json`; optional `product_anomalies.json`, `catalog_tasks.json` | "Guarantee path"; no `RiskPanel` |
| 2:00–2:30 | ⑤ Net benefit | `/simulator` (persona Luca) | "Logistics halved", then "Both" | Waterfall +640 +300 +88 +0 −200 −120 −100 −240 = +€368, then +€48, then −€192 with "Net benefit is negative under these assumptions" | `simulator_assumptions.v1.json` | "−€192" |
| 2:30–3:00 | ⑥ AMD | `/system` (or slide 8) | none | B1 to B4 with Measured, Pending or "Illustrative, not measured"; cost per decision with idle included; live telemetry every 5 s | `benchmarks.json`, `telemetry.live.json`, `/readyz` | "Illustrative, not measured" or "Measured" |

- About 10 clicks plus 6 beat moves. The persona follows the beat (Sara for ① to ④, Luca for ⑤ and ⑥); the
  persona chip announces the change, which reinforces "a person approves".
- **Off the main path, one key away for Q&A** (spec 16): the agent (`I` on Marco's card, the three suggested
  questions), the shop (`/shop?preset=giulia` and `?preset=marco`), the inspection (`/inspection/RET-000110`,
  then RET-000111 for manual review), Chiara's dispute ("what if a link is wrong": Q3 → Q1), Lorenzo's lift
  (`/reviews`).
- **Video (5 minutes, spec 16)**: the same six beats, plus the outbox message in IT and EN with the layers,
  the agent with the GPU dock showing the spike, Giulia's size advice and Marco's checkout terms with
  "The terms shown are the terms stored", Valentina's inspection, the matrix with the golden annotations
  (cover mode for the 16:9 cover). Provenance mode (`P`) is on for every shot with numbers. A backup recording
  is made in cached mode; the striped banner must be visible in it.
- **Deterministic visuals**: seeded graph layouts with no physics, wheel zoom off, fixed chart domains per
  beat, animation ≤ 300 ms, so every rehearsal looks the same.
- **Failover**: `Shift+C` reloads the current route on the cached origin.

---

## 13. Work split: four view areas

### 13.1 Directory tree and owners

```
apps/ui/
  package.json · vite.config.ts · tailwind.config.ts · tsconfig.json · playwright.config.ts   (Eng 2)
  mock/returnring-mock.ts                                                                   (Eng 2)
  scripts/gen-contract.mjs                                                                  (Eng 2)
  src/
    main.tsx · app/ (shell, router, persona store, hotkeys, theme, SideNav)                  (Eng 2)
    api/ (types.gen.ts, contract.gen.ts, models.ts, client.ts, queries.ts, mutations.ts,
          sse.ts, adapters/)                                                                (Eng 2)
    styles/ (tokens.css, patterns.css) · format/ · copy/common.ts + banned-terms.ts          (Eng 4)
    domain/ (levers.ts, decision.ts, refs.ts)                                               (Eng 1)
    sim/ (netBenefit.ts, rebucket.ts)                                                       (Eng 3)
    components/
      ui/ · feedback/ · provenance/ · mode/                                                 (Eng 4)
      decision/                                                                             (Eng 1)
      message/ · linked-accounts/                                                           (Eng 2)
      economics/ · charts/ · records/                                                       (Eng 3)
    views/
      operator/   queue/ case/ (header, hero, sections, rail, sheets) compare/ record/ outbox/ copy.ts index.ts   (Eng 1)
      agent/      CaseAssistant.tsx, AgentStepList, AgentAnswer, SuggestedQuestions,
                  useAgentStream.ts, dev/AgentHarness.tsx, copy.ts, index.ts                (Eng 2)
      manager/    portfolio/ simulator/ kpi/ fairness/ products/ reviews/ audit/ copy.ts index.ts            (Eng 3)
      platform/   shop/ inspection/ coupons/ system/ (incl. GpuDock) demo/ (incl. DemoRail) about/ copy.ts index.ts (Eng 4)
  tests/e2e/      pitch.spec.ts (Eng 1 leads) · operator · manager · platform · agent specs (each owner)
```

### 13.2 What each engineer owns

| Engineer | View area (exclusive directory) | Routes | Shared code owned |
| --- | --- | --- | --- |
| **Eng 1 · operator** | `src/views/operator/` | `/queue`, `/returns/:returnId`, `/returns/:returnId/versions/:decisionId`, `/compare`, `/outbox` | `src/domain/`, `src/components/decision/`; E2E-PITCH |
| **Eng 2 · agent** | `src/views/agent/` | no page route (drawer); `/dev/agent` in dev builds | scaffold and config, `src/app/`, `src/api/`, `mock/`, `scripts/`, `src/components/message/`, `src/components/linked-accounts/` |
| **Eng 3 · manager** | `src/views/manager/` | `/portfolio`, `/simulator`, `/kpi`, `/fairness`, `/products`, `/products/:anomalyId`, `/products/tasks`, `/reviews`, `/audit` | `src/sim/`, `src/components/economics/`, `src/components/charts/`, `src/components/records/` |
| **Eng 4 · platform** | `src/views/platform/` | `/shop`, `/inspection`, `/inspection/:returnId`, `/coupons`, `/system`, `/demo`, `/about` | `src/styles/`, `src/format/`, `src/copy/`, `src/components/ui/`, `src/components/feedback/`, `src/components/provenance/`, `src/components/mode/` |

The agent area is the smallest view, so its engineer owns the data layer, the shell, the message renderer and
the linked-accounts panel (whose refs the agent's citations focus). The platform engineer owns the visual
foundation because the platform screens are the lightest after it.

### 13.3 Boundaries between areas (frozen at Day 1, 11:00)

1. A view never imports from another view, with one exception: `views/operator` imports `CaseAssistant` from
   `views/agent`. ESLint `no-restricted-imports` enforces it.
2. Each view's `index.ts` exports `routes: AppRoute[]` and `nav: NavItem[]`; `views/platform` also exports
   `shellSlots = { GpuDock, DemoRail }`; `views/agent` exports only `CaseAssistant` (and its dev route in
   dev builds). `src/app/router.tsx` composes them; nobody else edits it after Day 1 morning.
3. Cross-area navigation uses URLs only (section 3.2 deep-link parameters): the manager's Reviews opens
   `/returns/:id?action=review`; Products and the matrix open `/returns/:id?focus=…`; the audit log opens
   `/returns/:id/versions/:decisionId`; `/demo` opens every beat.
4. Shared component props are those of section 5. A change is requested from the owner; consumers never fork
   a shared component inside their view.
5. Copy: each area writes `views/<area>/copy.ts`; shared strings go to `src/copy/common.ts` (Eng 4).
6. Mutations live in `src/api/mutations.ts` (Eng 2) from the first hour as typed stubs, so views can wire
   actions before the real hooks land.

### 13.4 Schedule

**Day 1 (Wed 14 Oct)**

- 09:00–11:00 **foundation, in parallel, disjoint files**:
  - Eng 2: Vite + React + TypeScript + Tailwind scaffold, router with empty routes, `gen:api`, `apiFetch`,
    query and mutation stubs, the mock middleware serving `index.json` (scenarios and SSE), persona store.
  - Eng 4: `tokens.css` and Tailwind mapping, UI primitives, provenance family, formatters, `ModeBanner`.
  - Eng 1: `domain/levers.ts`, `domain/decision.ts`, decision components with stories on the examples.
  - Eng 3: `sim/netBenefit.ts` with ECO-01-UI, `ChartFrame`, `NetBenefitBreakdown`, `AssumptionLabel`.
- 11:00 checkpoint: props frozen, merge to the integration branch.
- 11:00–19:00 **read-only screens on mocks** (Must): queue and case card for Marco, Giulia, Elena,
  RET-000109 (Eng 1); message, explanation and linked-accounts components, shell and SideNav (Eng 2);
  matrix and simulator (Eng 3); system page, `/demo` and checkout (Eng 4).
- 19:00 merge and smoke test the six beat URLs.

**Day 2 (Thu 15 Oct)**

- 09:00–14:00 **actions and states**: sheets, recompute, stale, legal guard, 403 and 503 (Eng 1); the agent
  drawer with streaming (Eng 2); products and catalogue tasks, KPI, reviews (Eng 3); coupons, checkout
  confirm, telemetry and GPU dock (Eng 4).
- 14:00–19:00 **Should screens and live API**: compare, outbox, decision record (Eng 1); adapters against
  the phase 4 API as routes land (Eng 2); fairness, audit with CSV (Eng 3); about, presenter rail (Eng 4).
- 19:00–22:00 E2E-PITCH on mocks and on the API, in live and cached modes; fixes.
- **22:00 checkpoint** (ADR-07): the pitch path must be green; otherwise the Streamlit plan B is decided.

**Fri 16 to Sat 17**: Could items (inspection, `PhrasebookPicker` if the contract lands, cover mode),
accessibility pass, dark-mode pass, provenance mode for the video, rehearsals, screenshots, feature freeze.

### 13.5 Priorities and cut order per area

| Area | Must | Should | Could |
| --- | --- | --- | --- |
| Operator | Queue; case card with every variant; approve (both), override, reject, review and lift; evidence and dispute with `RecomputeComparison`; 409, 422, 403, 503 states; compare view | Decision record drawer; outbox; newer-version banner; `[`/`]` navigation and auto-advance | `PhrasebookPicker` in the sheets |
| Agent | (US-701, US-702 are Should) data layer, shell, message and linked-accounts components | `CaseAssistant` with streaming, steps, consulted and missing, refusal, cached mode | session history in the drawer |
| Manager | Matrix; simulator with ECO-01 parity and sensitivity; KPI; products and catalogue tasks; reviews | Fairness; audit with CSV; threshold sandbox; golden annotations | cover mode; assumption diff |
| Platform | Tokens and provenance; mode banner; system page; `/demo` preflight | Checkout with snapshot receipt; coupons; about; GPU dock; presenter rail | Inspection |

Cut order if time runs out: inspection, then the presenter rail, then coupons page (the case card keeps the
coupon notice), then the agent drawer (the video can use the dev harness), then fairness. The pitch path
(compare, Marco, Rossi recompute, Elena, simulator, system) is never cut.

### 13.6 Definition of done (every area)

- `npm run typecheck`, `npm run lint` (ESLint with `jsx-a11y` and the import boundaries), `npm test`
  (Vitest) and the area's Playwright specs green; `make lint` and `make test` call them.
- Every screen of the area renders every relevant contract example with no console error and passes PROV-UI.
- PR description names the story IDs and what to review.

---

## 14. Dependencies, licences and tests

### 14.1 Dependencies (rule 15; exact versions pinned at scaffold, checked by LIC-01)

| Licence | Packages |
| --- | --- |
| MIT | react, react-dom, react-router, @tanstack/react-query, cytoscape (core layouts only, no extensions), recharts (its d3 modules are ISC), tailwindcss, @radix-ui/react-{dialog, popover, tooltip, tabs, toggle-group, checkbox, radio-group, select, slider}, clsx, vite, @vitejs/plugin-react, openapi-typescript, vitest, @testing-library/react, eslint-plugin-jsx-a11y |
| Apache-2.0 | typescript, @playwright/test |
| ISC | lucide-react |

Excluded: axe-core (MPL-2.0), OFL webfonts, MSW (not needed with the dev middleware), Ajv (not needed), date
libraries (`Intl` is enough). No runtime CDN request (no egress).

### 14.2 Tests

| Id | Owner | What it checks |
| --- | --- | --- |
| E2E-PITCH | Eng 1 (all contribute) | The six beats on mocks and on the API in live and cached modes; asserts the key texts of section 12; takes 1920 × 1080 screenshots for slides and the cover |
| E2E-01 decisions | Eng 1 | Giulia confirm and Marco restrictive approve (acknowledgement required, bare `Enter` ignored), both visible in the outbox |
| E2E-02 checkout snapshot | Eng 4 | Shown terms equal stored snapshot terms |
| E2E-03 LLM down | Eng 2 | `llm_unavailable`: the decision flow works; the only notices are in the message panel and the drawer |
| E2E-04 Elena | Eng 1, Eng 3 | No gate, no risk; the legal-guard 422 rendered; a catalogue task created |
| E2E-05 recompute and stale | Eng 1 | Rossi evidence, before and after, v2 loaded; `stale_decision` shows the stale panel and keeps the typed reason |
| PROV-UI | Eng 4 | On every route and case card, every visible text node with a digit is inside `[data-figure]`, a provenance scope with a footer, an id, a date, a version, a template field, the legal clause or a `GeneratedText` region; `PlainNumber` throws outside a scope in dev |
| MODE-UI | Eng 4 | In the `cached` scenario every route shows the banner with "Precomputed on AMD MI300X", the corner tag and the `[Cached]` title |
| COPY-LINT | Eng 4 | Banned terms (English and Italian) and number literals in copy files and JSX literals |
| CONTRAST | Eng 4 | The table of 4.2 recomputed from `tokens.css` |
| T-NEUTRAL | Eng 1 | `QuadrantBadge` Q1 to Q4 have identical computed styles apart from the glyph cell; none uses attention, danger or ok tokens |
| T-MESSAGE | Eng 2 | Segments rebuild `rendered_text` and outbox `body` for every example; clause present and not editable when `legal_clause_kind` is set; a forged text triggers the unverified state |
| T-ECHO | Eng 2 | A forged `decision_echo` hides the draft |
| T-SERVER-STATE | Eng 1 | Freezing the client clock past `review_at` changes nothing unless the API fields change |
| ROLE-UI | Eng 2 | Ahmed sees disabled actions with reasons; the mock returns 403; requests carry the acting user, never `system` |
| ECO-01-UI | Eng 3 | `netBenefit()` reproduces +368, +48, +128, −192 (v1) and +248 (v2), and every alternative's components in the examples; re-bucketing reproduces `quadrants[].customers` |
| ADAPTERS | Eng 2 | Every example in `contracts/examples/api/` passes its adapters |
| T-NO-HTML | Eng 4 | No `dangerouslySetInnerHTML`; a `customer_text` containing `<b>` renders literally |
| KEYBOARD | Eng 1 | The Sara loop with no mouse |

---

## 15. Open questions and contract requests

For the API (phase 4) and LLM (phase 3) owners. Nothing here blocks the build: each has a stated interim
behaviour.

| # | Question or request | Interim behaviour |
| --- | --- | --- |
| O-1 | Add examples and `index.json` scenarios for 403 `forbidden_role`, 503 `llm_unavailable` (regenerate, agent), 429, 409 `conflict`, an empty queue, `draft_status: pending` with `draft: null`, and an agent `error` event | UI-only fixtures labelled "UI fixture" |
| O-2 | What does the API do on a decision action while `draft_status: pending`: send the reviewed template, or 409? | Message-sending actions wait for the draft |
| O-3 | A restrictive override needs a review date but the request has none: expose the horizon (90 days, assumption) in `/readyz` or the card, and say whether it runs from the decision date or the action date | "Set by the server" before saving; the returned `review_at` after |
| O-4 | Customer language for the default `message.lang` | Italian by default |
| O-5 | Warehouse: which returns are physically received? A `received_at` or a queue filter | All withdrawal-path returns |
| O-6 | Thresholds: versioned policy thresholds, and how quadrant changes feed `logistics_returns_avoided` and `undue_discounts_avoided` | Threshold sandbox shows counts only |
| O-7 | Queue counts per saved view in one call (facets) | One `page_size=1` request per tab |
| O-8 | "Linked accounts" counts differ: queue `linked_account_count` 6 (other accounts) vs card cluster size 7; align the example texts | UI says "6 other accounts" and "cluster of 7 accounts" |
| O-9 | Return message segments (sentence ids, body parts, fields, clause) with the phrasebook change, or at least `legal_clause_text` | Client-side segmentation with an integrity check |
| O-10 | Checkout: add `terms_text {it, en}` from the contract renderers and product names and prices | Shop renders terms from the structured fields and shows variant ids |
| O-11 | Customer-facing wording for a coupon pause (IT and EN); `coupon_block.text` is operator wording | UI copy "Welcome coupons aren't available on this account until {review_at}." |
| O-12 | Matrix point → open return: add `return_id` to `MatrixPoint`, or confirm the `q=<customer_id>` lookup | Lookup on click |
| O-13 | Ablation and out-of-distribution rows: an optional `GET /api/evaluation` | Slides only |
| O-14 | Public demo writes: spec section 11 says read-only; confirm, or expose a capability flag | `VITE_READ_ONLY` build flag; `POST /api/agent/ask` stays enabled because the agent only reads (in the cached demo, only the precomputed questions) |
| O-15 | Which agent questions are cached (in `/readyz` or a small endpoint) | The three US-702 questions |
| O-16 | Telemetry behind Caddy basic auth vs "the judge sees telemetry" (US-905): protect only `/admin/*`? | "Protected" state on 401 |
| O-17 | `contracts/examples/llm_output.json` and `llm_case.json` use the truth-ring id `R-MARCO`; rule 12 and ADR-15 require opaque `CL-…` ids | Not shown by the UI; align the fixtures |
| O-18 | Elena's anomaly is `task_opened` in the examples; if the video should show a task being created, the reset needs a variant with the anomaly `open` | Pitch shows the existing task |
| O-19 | Expose the spec 8.6 lever table per quadrant with the rules version | Static, labelled summary |
| O-20 | Wording: the insufficient-evidence statement says "neither safe nor suspicious" (a spec 16 term); suggested: "until then the standard terms apply and no judgement is made" | Shown verbatim (contract text), reported by the copy check |
| O-21 | Example bugs: `case_card.elena.json` and `case_card.francesca.json` share order ORD-005512 and snapshot TS-005512 for different customers; `case_card.valentina.json` says "1 returned items" (plural) | None needed in the UI |
| O-22 | On `manual_review`, can Ahmed attach a claim by hand through `/confirm`? | Observations only, no claim |
| O-23 | Telemetry has no illustrative flag (benchmarks have `status: illustrative`); add one rather than parsing the origin | The origin text is parsed for "Illustrative" |
| O-24 | Customer level vs return level: Elena is Q2 as a customer while her open return is on the guarantee path; confirm it is intended | Explained in the matrix tooltip |
| O-25 | Does `renew` take a reason and a category like `lift`? | Assumed yes |
| O-26 | Maximum photo size for `POST /api/inspections` (base64 in JSON) | 8 MB client limit |

---

## Appendix: ownership split

Four view areas, disjoint directories under apps/ui/src/views/, each with one engineer who also owns specific shared directories (shared props frozen at Day 1 11:00).

1) OPERATOR (Eng 1) - apps/ui/src/views/operator/ (queue/, case/ with header, hero, sections, decision rail and sheets, compare/, record/, outbox/, copy.ts, index.ts). Routes: /queue, /returns/:returnId (deep-link params ?action=approve|override|reject|review|evidence, ?link=, ?focus=, ?from=), /returns/:returnId/versions/:decisionId, /compare?a=&b=, /outbox. Stories US-301..310 plus the US-607 review/lift sheet and the US-604 decision record drawer. Shared code owned: src/domain/ (levers.ts, decision.ts, refs.ts) and src/components/decision/ (QuadrantBadge, QuadrantGlyph, OutcomeBadge, PathBadge, EvidenceBadge, StateBadge, RestrictionBadge, ReviewDateChip, ApprovalFlag, ApproverLine, LeverChip/LeverList, TermsDiff, RefundFloorNotice, StatementLabelChip, DecisionChain). Leads E2E-PITCH. Only allowed cross-view import: CaseAssistant from views/agent.

2) AGENT (Eng 2) - apps/ui/src/views/agent/ (CaseAssistant.tsx, AgentStepList, AgentAnswer, SuggestedQuestions, useAgentStream.ts, dev/AgentHarness.tsx, copy.ts, index.ts). Single public export: CaseAssistant {returnId, open, onOpenChange, onRefSelect?}, mounted by the operator case card as a 440 px drawer. Routes: no page route; /dev/agent?return= in dev builds only. Stories US-701, US-702. Because the view is small, Eng 2 also owns: the scaffold and config (package.json, vite/tailwind/ts/playwright configs), src/app/ (shell, router composing every view's routes, persona store, hotkeys, theme, SideNav), src/api/ (types.gen.ts, contract.gen.ts, models.ts, client.ts, queries.ts, mutations.ts, sse.ts, adapters/), apps/ui/mock/ (Vite mock middleware over contracts/examples/api/index.json), scripts/gen-contract.mjs, src/components/message/ (MessagePreview, TemplateField, LegalClause, GenerationBadge, ExplanationList, FactCitation) and src/components/linked-accounts/ (LinkedAccountsPanel, AccountGraph, LinkTable, LinkDetailPopover, IdentifierFilter, CouponTimeline, ClusterFacts).

3) MANAGER (Eng 3) - apps/ui/src/views/manager/ (portfolio/, simulator/, kpi/, fairness/, products/, reviews/, audit/, copy.ts, index.ts). Routes: /portfolio (?quadrant=, ?cluster=, ?cover=1), /simulator, /kpi?week=, /fairness, /products, /products/:anomalyId, /products/tasks, /reviews (its Review action links to /returns/:id?action=review), /audit (CSV export). Stories US-601..607 (and US-208 anomaly views). Shared code owned: src/sim/ (netBenefit.ts TS port with ECO-01 parity, rebucket.ts), src/components/economics/ (NetBenefitBreakdown, AssumptionLabel, Waterfall), src/components/charts/ (ChartFrame with table view, Sparkline, DotPlot), src/components/records/ (ActorBadge, AuditEntryList, AuditDiff, CouponApplicationsTable, ReverseCouponDialog).

4) PLATFORM (Eng 4) - apps/ui/src/views/platform/ (shop/, inspection/, coupons/, system/ incl. GpuDock, demo/ incl. DemoRail, about/, copy.ts, index.ts; index also exports shellSlots {GpuDock, DemoRail}). Routes: /shop?preset=giulia|marco, /inspection, /inspection/:returnId, /coupons, /system (readiness, versions, benchmarks B1-B4, telemetry), /demo (preflight and six pitch beats), /about. Stories US-401..403, US-501..503 (Could), US-901, US-902, US-905, US-906. Shared code owned: src/styles/ (tokens.css, patterns), src/format/, src/copy/ (common.ts, constants.ts, banned terms), src/components/ui/ (Radix-based primitives), src/components/feedback/ (ErrorState, EmptyState, Skeleton, Toast, InlineNotice, LlmUnavailableNotice, ForbiddenRoleNotice, RoleGate), src/components/provenance/ (Figure, ProvenanceBadge, NatureMark, ProvenancePopover, ProvenanceScope, ProvenanceFooter, PlainNumber, Money, DateText, DateRange, IdText, GeneratedText, ContractExampleBanner), src/components/mode/ (ModeBanner, ModeCornerTag, LlmStatusPill). Owns PROV-UI, MODE-UI, COPY-LINT, CONTRAST, T-NO-HTML tests.

Rules: views never import each other except operator -> agent CaseAssistant (enforced by ESLint no-restricted-imports); each view's index.ts exports routes and nav, composed only by src/app/router.tsx; cross-area navigation is by URL only; shared components change only through their owner.
