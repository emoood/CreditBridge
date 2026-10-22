// Maps the backend's statuses and data onto the officer-facing vocabulary. Pure functions, no backend changes.
// Row vocabulary:  Mapped | Needs you | Waiting on document | Not evaluated
// Application vocabulary: Processing | Needs you | Waiting on document | Ready
import type { AppInfo, CaseResult, Job, Row, RowStateInfo } from './api';

export type RowStatus = 'Mapped' | 'Needs you' | 'Waiting on document' | 'Not evaluated';
export type AppStatus = 'Processing' | 'Needs you' | 'Waiting on document' | 'Ready';
export type ActionKind = 'accept_ge' | 'confirm_no_credit' | 'open' | 'retry';

export type Item = {
  key: string;            // result unit key (source id) or row id
  rowId: string;
  title: string;
  grade: string;
  result: CaseResult | null;
  status: RowStatus;
  reason: string;         // why it has this status, in plain words
  finding?: string;       // short finding for the "Needs your input" strip
  action?: { kind: ActionKind; label: string };
  objections: number;     // reviewer objections shown as a badge
  canInclude: boolean;    // a "Not evaluated" row the officer may bring in
  decided: 'ACCEPTED' | 'DECLINED' | 'MORE_INFORMATION_REQUESTED' | null;
  state?: RowStateInfo;   // the backend's verdict for this row, when the Case-agent path produced one
};

// Stand-in until the new lookup exists: a subject without a syllabus is worth waiting for only if it looks like an advanced course.
const ADVANCED = /\b(ap|a-level|as-level|igcse|ib|honou?rs|advanced|calculus|physics|chemistry|biology|statistics|computer science)\b/i;

export const looksAdvanced = (title: string) => ADVANCED.test(title);

export function classify(row: Row | null, result: CaseResult | null, included: boolean, failedKeys: Set<string>, evaluating: boolean): Item {
  const title = result?.course.title ?? row?.title ?? '';
  const base = { key: result?.course.code ?? row?.id ?? title, rowId: row?.id ?? '', title, grade: result?.course.grade ?? row?.grade ?? '', result,
    objections: result?.review?.status === 'CHALLENGED' ? result.review.objections.length || 1 : 0, canInclude: false,
    decided: result?.decision?.decision ?? null } as const;
  const make = (status: RowStatus, reason: string, extra: Partial<Item> = {}): Item => ({ ...base, status, reason, ...extra });

  if (row && !row.included) return make('Not evaluated', 'Left out at the extraction check', { canInclude: false });
  if (!result) {
    const key = row?.source_id ?? '';
    if (key && failedKeys.has(key)) return make('Needs you', 'Evaluation did not finish', { finding: 'The evaluation of this course did not finish.', action: { kind: 'retry', label: 'Retry' } });
    return make('Not evaluated', evaluating ? 'Not evaluated yet' : 'Not evaluated');
  }
  const d = result.decision;
  if (d?.decision === 'DECLINED') return make('Not evaluated', `Declined by ${d.actor}${d.reason ? `: ${d.reason}` : ''}`);
  if (d?.decision === 'MORE_INFORMATION_REQUESTED') return make('Waiting on document', `More information requested by ${d.actor}`);
  const m = result.match;
  const cat = result.triage.category;
  if (m.status === 'SCHOOL_SPECIFIC') return make('Not evaluated', 'School-specific subject: no university equivalent, so it is not mapped');
  if (m.status === 'NEEDS_SYLLABUS') {
    if (included || looksAdvanced(title)) return make('Waiting on document', 'No syllabus on file; a request is drafted');
    return make('Not evaluated', 'Not in curriculum: no syllabus on file, and not an advanced course', { canInclude: true });
  }
  if (d?.decision === 'ACCEPTED') return make('Mapped', 'Accepted by the officer');
  if (cat === 'BLOCKED_BY_RULE') return make('Needs you', result.triage.reasons[0] ?? 'Blocked by a policy rule', { finding: `Blocked by a rule: ${result.triage.reasons[0] ?? ''}`, action: { kind: 'open', label: 'Open evidence' } });
  if (m.status === 'POTENTIAL_MATCH') return make('Mapped', result.triage.reasons[0] ?? 'Suggested by the AI');
  if (m.status === 'POTENTIAL_GENERAL_ELECTIVE') return make('Needs you', 'The AI flags a possible general elective', { finding: 'The AI flags a possible general elective. You decide.', action: { kind: 'accept_ge', label: 'Accept as elective' } });
  if (m.status === 'NO_DEFENSIBLE_MATCH') return make('Needs you', 'No AUD equivalent found', { finding: 'No AUD equivalent was found.', action: { kind: 'confirm_no_credit', label: 'Confirm no credit' } });
  return make('Needs you', 'The AI could not decide', { finding: 'The AI could not decide. Check the evidence.', action: { kind: 'open', label: 'Open evidence' } });
}

export type Built = { items: Item[]; relevant: Item[]; needsYou: Item[]; notEvaluated: Item[]; counts: { mapped: number; needsYou: number; waiting: number; notEvaluated: number; total: number } };

export function build(app: AppInfo, results: CaseResult[], pending: { key: string }[], include: Set<string>, evaluating: boolean, states: Record<string, RowStateInfo> = {}): Built {
  const rows = app.rows ?? [];
  const bySid = new Map(results.map(r => [r.course.code, r]));
  const failed = new Set(pending.map(p => p.key));
  const items: Item[] = [];
  const used = new Set<string>();
  for (const row of rows) {
    const res = row.source_id ? bySid.get(row.source_id) ?? null : null;
    if (res) used.add(res.course.code);
    items.push(withState(classify(row, res, include.has(row.id), failed, evaluating), states[row.id]));
  }
  for (const r of results) if (!used.has(r.course.code)) items.push(classify(null, r, include.has(r.course.code), failed, evaluating)); // demo fixtures have no source ids
  const rank: Record<RowStatus, number> = { 'Needs you': 0, Mapped: 1, 'Waiting on document': 2, 'Not evaluated': 3 };
  items.sort((a, b) => rank[a.status] - rank[b.status]);
  const relevant = items.filter(i => i.status !== 'Not evaluated');
  const count = (s: RowStatus) => items.filter(i => i.status === s).length;
  return { items, relevant, needsYou: items.filter(i => i.status === 'Needs you'), notEvaluated: items.filter(i => i.status === 'Not evaluated'),
    counts: { mapped: count('Mapped'), needsYou: count('Needs you'), waiting: count('Waiting on document'), notEvaluated: count('Not evaluated'), total: items.length } };
}

/** When the backend decided this row (gate, mapping lookup, Case agent), its status wins; the older result-based mapping is the fallback. */
function withState(i: Item, s: RowStateInfo | undefined): Item {
  if (!s) return i;
  const waiting = s.status === 'Waiting on document';
  const needs = s.status === 'Needs you';
  const headline = s.finding?.headline;
  return { ...i, state: s, status: s.status, reason: s.reason || i.reason, canInclude: s.status === 'Not evaluated' && s.can_include,
    finding: needs ? (headline ?? s.reason) : i.finding,
    action: needs ? (i.action ?? { kind: 'open', label: 'Open evidence' }) : waiting ? { kind: 'open', label: 'See request' } : undefined };
}

const BUSY = ['READING', 'CONFIRMED', 'EVALUATION_IN_PROGRESS'];
export function isBusy(app: AppInfo, jobs: (Job | null)[]): boolean {
  return BUSY.includes(app.status) || jobs.some(j => j && (j.status === 'QUEUED' || j.status === 'RUNNING'));
}

export function appStatus(app: AppInfo, b: Built, jobs: (Job | null)[]): AppStatus {
  if (isBusy(app, jobs)) return 'Processing';
  if (app.status === 'EXTRACTION_REVIEW_REQUIRED' || app.status === 'READY_FOR_EVALUATION') return 'Needs you';
  if (jobs.some(j => j && (j.status === 'INTERRUPTED' || j.status === 'FAILED'))) return 'Needs you';
  if (b.counts.needsYou > 0) return 'Needs you';
  if (b.counts.waiting > 0) return 'Waiting on document';
  return 'Ready';
}

export const APP_ORDER: Record<AppStatus, number> = { 'Needs you': 0, Processing: 1, 'Waiting on document': 2, Ready: 3 };

export function curriculumOf(app: AppInfo, results: CaseResult[]): string {
  const tally = new Map<string, number>();
  for (const r of results) if (r.course.curriculum_qualification) tally.set(r.course.curriculum_qualification, (tally.get(r.course.curriculum_qualification) ?? 0) + 1);
  for (const r of app.rows ?? []) if (r.subject?.qualification) tally.set(r.subject.qualification, (tally.get(r.subject.qualification) ?? 0) + 1);
  return [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? (app.kind === 'demo' ? 'Demo fixtures' : '—');
}

export function toCsv(items: Item[], showCredits = false, credits = 3): string {
  const q = (s: string | number | null | undefined) => `"${String(s ?? '').replace(/"/g, '""')}"`;
  const head = ['Source course', 'Grade', 'AUD code', 'AUD title', ...(showCredits ? ['AUD credits'] : []), 'Status', 'Decision', 'Reviewer objections', 'Reason'];
  const lines = items.map(i => [i.title, i.grade, i.result?.match.target?.code, i.result?.match.target?.title, ...(showCredits ? [i.result?.match.target ? credits : ''] : []), i.status, i.decided ?? '', i.objections, i.reason].map(q).join(','));
  return [head.map(q).join(','), ...lines].join('\n');
}
