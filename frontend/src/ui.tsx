import type { ReactNode } from 'react';
import type { AppStatus, RowStatus } from './model';

export const Pill = ({ tone, children }: { tone: string; children: ReactNode }) => <span className={`status status-${tone}`}><span className="status-dot" />{children}</span>;

const ROW_TONE: Record<RowStatus, string> = { Mapped: 'green', 'Needs you': 'amber', 'Waiting on document': 'blue', 'Not evaluated': 'neutral' };
const APP_TONE: Record<AppStatus, string> = { 'Needs you': 'amber', Processing: 'blue', 'Waiting on document': 'blue', Ready: 'green' };
export const RowPill = ({ s }: { s: RowStatus }) => <Pill tone={ROW_TONE[s]}>{s}</Pill>;
export const AppPill = ({ s }: { s: AppStatus }) => <Pill tone={APP_TONE[s]}>{s}</Pill>;
