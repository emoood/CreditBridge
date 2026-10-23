import type { ReactNode } from 'react';
import type { AppStatus, RowStatus } from './model';

export const Pill = ({ tone, children }: { tone: string; children: ReactNode }) => <span className={`status status-${tone}`}><span className="status-dot" />{children}</span>;

const APP_TONE: Record<AppStatus, string> = { Open: 'amber', Processing: 'blue', 'Waiting on document': 'blue', Ready: 'green' };
export const AppPill = ({ s }: { s: AppStatus }) => <Pill tone={APP_TONE[s]}>{s}</Pill>;

/** Quiet row status for the table: plain text with a small dot. Amber marks open rows. */
const DOT: Record<RowStatus, string> = { Mapped: '#2f9e7a', Conditional: '#9aa7ba', 'No mapping': '#9aa7ba', 'Waiting on document': '#5b8bd6', 'Not evaluated': '#c3ccd9' };
export const RowStatusText = ({ s }: { s: RowStatus }) => <span className="qstatus"><i style={{ background: DOT[s] }} />{s}</span>;
