// Maps the backend's statuses and data onto the officer-facing vocabulary. Pure functions.
// Row vocabulary:  Mapped | Conditional | No mapping | Waiting on document | Not evaluated   (+ the officer's resolution, if any)
// Application vocabulary: Processing | Needs you | Waiting on document | Ready
import type { ActionInfo, AppInfo, CaseResult, Job, ProgramClass, Row, RowStateInfo, RowStatus, Sheet } from './api';

export type { RowStatus };
export type AppStatus = 'Processing' | 'Needs you' | 'Waiting on document' | 'Ready';

const RESOLVING = ['CONFIRM', 'CHANGE', 'REJECT', 'NO_CREDIT', 'CORRECT', 'RECONFIRM'];
export const RESOLUTION_LABEL: Record<string, string> = { CONFIRM: 'Confirmed', CHANGE: 'Changed', REJECT: 'Rejected', NO_CREDIT: 'No credit', CORRECT: 'Corrected', RECONFIRM: 'Re-confirmed' };

export type Item = {
  key: string;            // result unit key (source id) or row id
  rowId: string;
  title: string;
  grade: string;
  result: CaseResult | null;
  status: RowStatus;
  reason: string;         // why it has this status, in plain words
  finding?: string;       // short finding for the "Needs your input" card
  objections: number;     // reviewer objections
  canInclude: boolean;    // a "Not evaluated" row the officer may bring in
  state?: RowStateInfo;   // the backend's verdict for this row
  action?: ActionInfo;    // the officer's latest action on this row
  resolved: boolean;      // confirmed, changed, rejected or no credit
  audCode: string | null; // what the row stands at now (the officer's choice, else the suggestion)
  audCredits: number | null;
  cls: ProgramClass | null; // the program-plan class of the mapped equivalent (Program course, General elective suggestion, Not applicable)
  doubts: number;         // doubts the Case agent raised that code corroborated or recorded
  needsInput: boolean;    // No mapping, Conditional or Waiting on document, and not yet resolved: shown in "Needs your input"
};

// Stand-in for results of older runs that have no backend row state: a subject without a syllabus counts as waiting only if it looks advanced.
const ADVANCED = /\b(ap|a-level|as-level|igcse|ib|honou?rs|advanced|calculus|physics|chemistry|biology|statistics|computer science)\b/i;
export const looksAdvanced = (title: string) => ADVANCED.test(title);

function legacyStatus(result: CaseResult | null, row: Row | null, included: boolean, title: string): { status: RowStatus; reason: string; canInclude: boolean } {
  if (row && !row.included) return { status: 'Not evaluated', reason: 'Left out at the extraction check', canInclude: false };
  if (!result) return { status: 'Not evaluated', reason: 'Not evaluated yet', canInclude: false };
  const m = result.match;
  if (m.status === 'SCHOOL_SPECIFIC') return { status: 'Not evaluated', reason: 'School-specific subject: no university equivalent, so it is not mapped', canInclude: false };
  if (m.status === 'NEEDS_SYLLABUS') return included || looksAdvanced(title) ? { status: 'Waiting on document', reason: 'No syllabus on file; a request is drafted', canInclude: false }
    : { status: 'Not evaluated', reason: 'Not in curriculum: no syllabus on file, and not an advanced course', canInclude: true };
  if (m.status === 'POTENTIAL_MATCH' && result.triage.category !== 'BLOCKED_BY_RULE') return { status: 'Mapped', reason: result.triage.reasons[0] ?? 'Suggested by the AI', canInclude: false };
  return { status: 'No mapping', reason: result.triage.reasons[0] ?? 'No AUD equivalent found', canInclude: false };
}

export function classify(row: Row | null, result: CaseResult | null, included: boolean, failedKeys: Set<string>, evaluating: boolean, state?: RowStateInfo, action?: ActionInfo): Item {
  const title = result?.course.title ?? row?.title ?? '';
  const legacy = legacyStatus(result, row, included, title);
  const failed = !result && row?.source_id && failedKeys.has(row.source_id);
  const status: RowStatus = state?.status ?? (failed ? 'No mapping' : legacy.status);
  const reason = state?.reason || (failed ? 'Evaluation did not finish' : legacy.reason || (evaluating ? 'Not evaluated yet' : ''));
  const resolved = !!action && RESOLVING.includes(action.action);
  const m = result?.match;
  const sugg = state?.mapping?.aud_code ?? (m?.status === 'POTENTIAL_MATCH' ? m.target?.code ?? null : null);
  const suggCredits = state?.mapping?.aud_credits ?? null;
  const audCode = resolved ? (action!.chosen.aud_code ?? null) : sugg;
  const audCredits = resolved ? (action!.chosen.credits ?? null) : suggCredits;
  const needsInput = (status === 'No mapping' || status === 'Conditional' || status === 'Waiting on document') && !resolved;
  return { key: result?.course.code ?? row?.id ?? title, rowId: row?.id ?? '', title, grade: result?.course.grade ?? row?.grade ?? '', result, status, reason,
    finding: needsInput ? (state?.finding?.headline ?? reason) : undefined, objections: result?.review?.status === 'CHALLENGED' ? result.review.objections.length || 1 : 0,
    canInclude: state ? status === 'Not evaluated' && state.can_include : legacy.canInclude, state, action, resolved, audCode, audCredits, needsInput, cls: state?.classification ?? null, doubts: state?.doubts?.length ?? 0 };
}

export type Built = {
  items: Item[]; relevant: Item[]; needsInput: Item[]; notEvaluated: Item[]; electives: Item[]; notApplicable: Item[];
  counts: { mapped: number; conditional: number; noMapping: number; waiting: number; notEvaluated: number; resolved: number; unresolved: number; total: number; confirmable: number };
};

export function build(app: AppInfo, results: CaseResult[], pending: { key: string }[], include: Set<string>, evaluating: boolean,
  states: Record<string, RowStateInfo> = {}, actions: Record<string, ActionInfo> = {}): Built {
  const rows = app.rows ?? [];
  const bySid = new Map(results.map(r => [r.course.code, r]));
  const failed = new Set(pending.map(p => p.key));
  const items: Item[] = [];
  const used = new Set<string>();
  for (const row of rows) {
    const res = row.source_id ? bySid.get(row.source_id) ?? null : null;
    if (res) used.add(res.course.code);
    items.push(classify(row, res, include.has(row.id), failed, evaluating, states[row.id], actions[row.id]));
  }
  for (const r of results) if (!used.has(r.course.code)) items.push(classify(null, r, include.has(r.course.code), failed, evaluating)); // demo fixtures have no source ids
  const rank: Record<RowStatus, number> = { 'No mapping': 0, Conditional: 1, 'Waiting on document': 2, Mapped: 3, 'Not evaluated': 4 };
  items.sort((a, b) => Number(a.resolved) - Number(b.resolved) || rank[a.status] - rank[b.status]);
  const relevant = items.filter(i => i.status !== 'Not evaluated');
  const n = (s: RowStatus) => relevant.filter(i => i.status === s).length;
  return { items, relevant, needsInput: items.filter(i => i.needsInput), notEvaluated: items.filter(i => i.status === 'Not evaluated' && i.state?.kind !== 'not_applicable'), notApplicable: items.filter(i => i.state?.kind === 'not_applicable'), electives: relevant.filter(i => i.cls?.value === 'General elective suggestion'),
    counts: { mapped: n('Mapped'), conditional: n('Conditional'), noMapping: n('No mapping'), waiting: n('Waiting on document'), notEvaluated: items.filter(i => i.status === 'Not evaluated' && i.state?.kind !== 'not_applicable').length,
      resolved: relevant.filter(i => i.resolved).length, unresolved: relevant.filter(i => !i.resolved).length, total: items.length,
      confirmable: relevant.filter(i => i.status === 'Mapped' && !i.resolved && !!i.state && !i.doubts && i.cls?.value !== 'Not applicable').length } };
}

const BUSY = ['READING', 'CONFIRMED', 'EVALUATION_IN_PROGRESS'];
export function isBusy(app: AppInfo, jobs: (Job | null)[]): boolean {
  return BUSY.includes(app.status) || jobs.some(j => j && (j.status === 'QUEUED' || j.status === 'RUNNING'));
}

export function appStatus(app: AppInfo, b: Built, jobs: (Job | null)[]): AppStatus {
  if (isBusy(app, jobs)) return 'Processing';
  if (app.status === 'EXTRACTION_REVIEW_REQUIRED' || app.status === 'READY_FOR_EVALUATION') return 'Needs you';
  if (jobs.some(j => j && (j.status === 'INTERRUPTED' || j.status === 'FAILED'))) return 'Needs you';
  if (b.needsInput.some(i => i.status !== 'Waiting on document')) return 'Needs you';
  if (b.needsInput.length > 0) return 'Waiting on document';
  return 'Ready';
}

export const APP_ORDER: Record<AppStatus, number> = { 'Needs you': 0, Processing: 1, 'Waiting on document': 2, Ready: 3 };

export function curriculumOf(app: AppInfo, results: CaseResult[]): string {
  const tally = new Map<string, number>();
  for (const r of results) if (r.course.curriculum_qualification) tally.set(r.course.curriculum_qualification, (tally.get(r.course.curriculum_qualification) ?? 0) + 1);
  for (const r of app.rows ?? []) if (r.subject?.qualification) tally.set(r.subject.qualification, (tally.get(r.subject.qualification) ?? 0) + 1);
  return [...tally.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? (app.kind === 'demo' ? 'Demo fixtures' : '—');
}

/** The checked sheet as text. Columns and their order come from the backend's institution config. */
export function sheetRows(s: Sheet): string[][] {
  const val = (ln: Sheet['rows'][number], c: string) => c === 'source_course' ? ln.source_course : c === 'grade' ? ln.grade : c === 'aud_equivalent' ? ln.aud_equivalent
    : c === 'aud_credits' ? (ln.aud_credits ?? '') : ln.status;
  return [s.headers, ...s.rows.map(ln => s.columns.map(c => String(val(ln, c))))];
}
export const toTsv = (s: Sheet) => sheetRows(s).map(r => r.map(c => c.replace(/[\t\n]/g, ' ')).join('\t')).join('\n');
export const toCsv = (s: Sheet) => sheetRows(s).map(r => r.map(c => `"${c.replace(/"/g, '""')}"`).join(',')).join('\n');

export const CLASS_LABEL: Record<string, string> = { 'Program course': 'Program course', 'General elective suggestion': 'Elective suggestion', 'Not applicable': 'Not in the plan' };


// ---- the review modal: what each open row needs, in one plain sentence, and the one button that moves it forward
export type OpenKind = 'score' | 'document' | 'pick' | 'reconfirm' | 'review';
export type Need = { sentence: string; primary: string; kind: OpenKind };

/** Ready = decided by the officer, or mapped with nothing to look at. Open = everything else that is relevant. */
export const isReady = (i: Item) => i.status !== 'Not evaluated' && (i.resolved || (i.status === 'Mapped' && !i.doubts && i.cls?.value !== 'Not applicable'));
export const isOpen = (i: Item) => i.status !== 'Not evaluated' && !isReady(i);

export function needOf(i: Item, program: string): Need {
  const s = i.state; const cond = s?.mapping?.condition; const code = s?.mapping?.aud_code ?? '';
  const missing = s?.condition_result?.missing ?? [];
  if (i.status === 'Mapped') {
    const d = s?.doubts?.[0];
    return { sentence: d ? d.reason : `${code} is not on the ${program || 'program'} plan.`, primary: 'Review', kind: 'review' };
  }
  if (i.status === 'Waiting on document') {
    const req = (s?.requests ?? []).find(q => q.status !== 'RECEIVED');
    return { sentence: `Waiting on: ${req?.what ?? missing[0]?.evidence ?? 'a document'}.`, primary: 'Add document', kind: 'document' };
  }
  if (s?.kind === 'stale') return { sentence: `The mapping to ${code} is out of date (version ${s.mapping?.version}) and needs re-confirming.`, primary: 'Re-confirm mapping', kind: 'reconfirm' };
  if (s?.kind === 'condition_missing') {
    if (missing.some(m => m.field === 'exam_score') && cond?.type === 'min_exam_score') return { sentence: `Needs an AP score of ${String(cond.value)} or higher. No score on file.`, primary: 'Add score', kind: 'score' };
    return { sentence: s.condition_result?.reason ?? s.reason, primary: 'Add document', kind: 'document' };
  }
  if (s?.kind === 'condition_unmet') return { sentence: s.condition_result?.reason ?? s.reason, primary: 'Pick a course', kind: 'pick' };
  if (s?.kind === 'rule_blocked') return { sentence: s.reason, primary: 'Pick a course', kind: 'pick' };
  return { sentence: s?.kind === 'unmapped' ? 'No AUD course is mapped to this subject yet.' : (s?.reason ?? ''), primary: 'Pick a course', kind: 'pick' };
}

const KIND_WORDS: Record<OpenKind, string> = { score: 'an exam score', document: 'a document', pick: 'a course', reconfirm: 'a stale mapping to re-confirm', review: 'a second look' };
/** One plain sentence about the case, built from the current rows. The Case agent's own sentence is used only when every course it names is still open (a recording can be older than the rows). */
export function plainSummary(b: Built, program: string, agentText: string): string {
  const open = b.relevant.filter(isOpen);
  const first = agentText.split(/(?<=[.!?])\s/)[0] ?? '';
  const names = b.items.filter(i => first.includes(i.title) && i.title.length > 3);
  if (first && names.every(i => isOpen(i)) && names.length > 0) return first;
  if (open.length === 0) return b.relevant.length ? 'Every course is ready to confirm.' : '';
  return `Needs you: ${open.map(i => `${i.title} (${KIND_WORDS[needOf(i, program).kind]})`).join(', ')}.`;
}
