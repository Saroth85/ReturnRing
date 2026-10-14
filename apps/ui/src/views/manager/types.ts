// Types the manager screens read, derived from the aliases of src/api/models.ts (components never
// import the generated files directly, docs/ui-design.md 11.1).
import type {
  FairnessResponse,
  KpiResponse,
  PortfolioMatrix,
  ProductAnomaliesResponse,
  SimulatorAssumptionsResponse,
} from '@/api/models';

export type ProductAnomaly = ProductAnomaliesResponse['items'][number];
export type MatrixPoint = PortfolioMatrix['points'][number];
export type AssumptionVersion = SimulatorAssumptionsResponse['current'];
export type FairnessDimension = FairnessResponse['dimensions'][number];
export type CohortRate = KpiResponse['false_positives'][number];
