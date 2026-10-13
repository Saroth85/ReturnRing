// Platform area strings (docs/ui-design.md 9). English; no number literals.
import { AGENT_MAX_STEPS } from '@/api/contract.gen';

export const PLATFORM = {
  nav: {
    inspection: 'Inspection',
    coupons: 'Coupon rules',
    shop: 'Simulated checkout',
    demo: 'Demo path',
    system: 'System and benchmarks',
    about: 'About the data',
  },
  titles: {
    shop: 'Simulated checkout',
    inspection: 'Warehouse inspection',
    inspectionDetail: 'Inspect a return',
    coupons: 'Coupon rules',
    system: 'System and benchmarks',
    demo: 'Demo path',
    about: 'About the data',
  },
  coupons: {
    approvedBy: 'Approved by',
    note: 'The general rule is approved and versioned by the manager; each application to a cluster is a proposal an operator confirms. Always reversible.',
  },
  demo: {
    preflight: 'Preflight',
    beats: 'Beats',
    beatList: [
      { label: 'Same indicator', to: '/compare?a=RET-000101&b=RET-000102' },
      { label: 'Marco', to: '/returns/RET-000102' },
      { label: 'Rossi', to: '/returns/RET-000104' },
      { label: 'Elena', to: '/returns/RET-000105' },
      { label: 'Net benefit', to: '/simulator' },
      { label: 'AMD', to: '/system' },
    ],
    presenter: 'Start presenter mode',
    leavePresenter: 'Leave presenter mode',
    rail: 'Demo beats',
  },
  about: {
    natures: 'Natures of the numbers',
    golden: 'Golden cases are demonstrations with fixed ids, never accuracy.',
    linked: 'Linked accounts share identifiers (a card, a device, an address). A link is a proposed connection with its evidence, not proof of who uses the accounts.',
    forbidden: 'Gender, country, age, names and raw identifiers are never model inputs. Customer value never feeds the risk score.',
    limits: 'Declared limits: linked accounts are evaluated on synthetic data; legal validation is pending.',
    agentSteps: `The case assistant reads the case with at most ${AGENT_MAX_STEPS} steps and cannot change it.`,
    versions: 'Versions',
  },
};
