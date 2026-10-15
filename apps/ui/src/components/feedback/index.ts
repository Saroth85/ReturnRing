// Feedback components (docs/ui-design.md 5.8, 10.1). Owner: platform (Eng 4).
// Still to build here: Toast and ToastRegion.
export { ForbiddenRoleNotice, InlineNotice, LlmUnavailableNotice } from './notices';
export { READ_ONLY, gateFor, useReadOnly, useRoleGate, type Gate } from './roles';
export { RoleGate } from './RoleGate';
export { DataCheck, ScreenPlaceholder } from './ScreenPlaceholder';
export { EmptyState, ErrorState, LoadingBlock, Skeleton } from './states';
