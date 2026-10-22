// Typed client for the FastAPI backend. All data is synthetic demo data.
export type Triage = 'READY_TO_ACCEPT' | 'NEEDS_ATTENTION' | 'BLOCKED_BY_RULE' | 'NEEDS_EVIDENCE' | 'POTENTIAL_GE' | 'NO_MATCH' | 'SCHOOL_SPECIFIC';
export type Excerpt = { id: string; text: string };
export type Course = { code: string; row_code: string; title: string; title_ar: string; credits: string; grade: string; term: string; has_syllabus: boolean; curriculum_subject: string | null; curriculum_qualification: string | null };
export type Decision = { decision: 'ACCEPTED' | 'DECLINED' | 'MORE_INFORMATION_REQUESTED'; reason: string; actor: string; actor_role: string; at: string; idempotency_key: string };
export type CaseResult = {
  course: Course;
  triage: { category: Triage; reasons: string[]; priority: number };
  match: { status: string; target: { code: string; title: string; level: number; credits: number; basis?: string } | null; evidence_strength: string | null;
    supported_outcomes: { subject: Excerpt; target: Excerpt; explanation: string }[]; missing_or_weaker_outcomes: string[]; concerns: string[];
    title_only_suggestions: { target: { code: string; title: string; level: number; credits: number } | null; reason: string; basis: 'TITLE_ONLY_UNVERIFIED' }[];
    flags: string[] };
  review: { status: string; flags: string[]; objections: { text: string; excerpts: Excerpt[] }[]; notes: string[] } | null;
  rules: { rule_id: string; status: string; reason: string; source_url: string }[];
  staff_checklist: { rule_id: string; status: string; reason: string }[];
  case_file: { queries: string[]; notes: { by: string; kind: string; text: string }[]; targets_read: Record<string, string[]>; precedents_seen: unknown[] };
  llm_calls: number; provider: string; model: string; duration_s: number; is_replay: boolean;
  replay?: { label: string; recorded_at: string; model: string; recorded_duration_s: number; recorded_llm_calls: number };
  syllabus_requests: { missing_fields: string[]; status: string }[];
  decision: (Decision & { stale?: boolean }) | null; version: number;
  row_state?: RowStateInfo; mapping?: { version: string | null; condition: unknown; condition_result: RowStateInfo['condition_result'] }; case?: { finding: Finding | null; requests: DraftedRequest[]; calls: number };
};
export type Unit = { stage: string; key: string; status: 'PENDING' | 'RUNNING' | 'DONE' | 'FAILED_RETRYABLE' | 'SKIPPED'; attempts: number; method: string; error_code: string; duration_s: number };
export type Job = { id: string; app_id?: string; phase?: 'READING' | 'EVALUATION'; status: 'QUEUED' | 'RUNNING' | 'DONE' | 'FAILED' | 'INTERRUPTED'; stage?: string; current_unit?: string;
  flags?: string[]; units?: Unit[]; summary?: { triage_counts?: Record<string, number>; worklist?: string[]; failed?: string[] }; timings?: { total_secs: number | null; per_stage_secs: Record<string, number> };
  courses: Record<string, string>; errors: Record<string, string>; route: string; model: string;
  llm_calls: number; activity: { ts: number; kind: string; course?: string; tool?: string; stage?: string; role?: string; ok?: boolean; duration_s?: number }[] };
export type Institution = { institution: string; transfer_credits: number; display: { mark: boolean; school_credits: boolean; aud_credits: boolean; grade_year: boolean } };
export type Finding = { headline: string; needs?: string; missing_evidence: string[]; gaps: string[]; candidates: { aud_code: string; why: string }[]; review_package: string[]; flags?: string[] };
export type DraftedRequest = { id: string; what: string; why: string; to: string; status: 'DRAFT' | 'SENT' | 'RECEIVED' };
export type RowStateInfo = { row_id: string; status: 'Mapped' | 'Needs you' | 'Waiting on document' | 'Not evaluated'; kind: string; reason: string; can_include: boolean; included_override: boolean;
  mapping: { aud_code: string; aud_credits: number; version: string; status: string; condition: { type: string; value: unknown; evidence?: string } | null } | null;
  condition_result: { status: string; reason: string; missing: { field: string; evidence: string }[] } | null; finding: Finding | null; requests: DraftedRequest[]; facts: Record<string, unknown> };
export type Activity = { usage: { calls: number; seconds: number; prompt_tokens: number; output_tokens: number; cost_usd: number; models: string[]; by_stage: Record<string, { calls: number; seconds: number }> };
  trace: { at: string; row_id: string; actor: string; name: string; args: Record<string, unknown>; ok: boolean; detail: string }[] };
export type Subject = { id: string; name: string; code: string; qualification: string };
export type Candidate = Subject & { score: number };
export type Row = { id: string; code: string; title: string; title_ar: string; credits: string; grade: string; term: string; page: number; evidence_ref: string;
  uncertain_fields: string[]; subject: Subject | null; candidates: Candidate[]; included: boolean; corrected: boolean; auto_linked?: boolean;
  mark?: number | null; points?: number | null; flags?: string[]; curriculum_status?: string; source_id?: string };
export type AppInfo = { id: string; kind: 'demo' | 'upload'; student: string; program: string; institution: string; courses: number; status: string; decided: number; catalog: string;
  created_at: string; confirmed?: boolean; reading_job_id?: string | null; evaluation_job_id?: string | null; job_id?: string; rows?: Row[]; document?: { filename: string; page_count: number; sha256: string; ext: string; size: number } | null; corrections?: unknown[];
  extraction?: { method: string; blocks?: { key: string; status: string; reread?: boolean; flags?: number | null; error?: string }[]; failed_blocks?: number; flags?: string[]; unrecognized?: number; rows_found?: number };
  curriculum?: { available: boolean; error: string | null };
  replay?: { name: string; label: string; recorded_at: string; model: string; live: string[] } | null };
export type Recording = { name: string; recorded_at: string; model: string; label: string; evaluation_s: number | null; reading_s: number | null;
  subjects: { row_id: string; title: string; triage: string | null; duration_s: number }[] };
export type DevEvent = Record<string, any> & { id: number; ts: number; kind: string };

const ROLE_HEADERS = (role: string) => ({ 'content-type': 'application/json', 'X-Role': role, 'X-Actor': role === 'admissions' ? 'Admissions officer' : role });
async function j<T>(r: Response): Promise<T> {
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail ?? m; } catch { /* ignore */ } throw new Error(String(m)); }
  return r.json();
}
export const api = {
  applications: () => fetch('/api/v1/applications').then(j<AppInfo[]>),
  application: (id: string) => fetch(`/api/v1/applications/${id}`).then(j<AppInfo>),
  upload: (file: File, label: string, qualification: string) => { const f = new FormData(); f.append('file', file); f.append('student_label', label); f.append('qualification', qualification);
    return fetch('/api/v1/applications', { method: 'POST', body: f, headers: { 'X-Role': 'admissions' } }).then(j<AppInfo>); },
  patchRow: (id: string, row: string, body: Record<string, unknown>) => fetch(`/api/v1/applications/${id}/rows/${row}`, { method: 'PATCH', headers: ROLE_HEADERS('admissions'), body: JSON.stringify(body) }).then(j<Row>),
  addRow: (id: string) => fetch(`/api/v1/applications/${id}/rows`, { method: 'POST' }).then(j<Row>),
  deleteRow: (id: string, row: string) => fetch(`/api/v1/applications/${id}/rows/${row}`, { method: 'DELETE' }).then(j<{ deleted: boolean }>),
  link: (id: string, qualification: string | null, relink: boolean) => fetch(`/api/v1/applications/${id}/link`, { method: 'POST', headers: ROLE_HEADERS('admissions'), body: JSON.stringify({ qualification, relink }) }).then(j<AppInfo>),
  previewUrl: (id: string, page: number) => `/api/v1/applications/${id}/preview/${page}`,
  qualifications: () => fetch('/api/v1/curriculum/qualifications').then(j<{ qualifications: string[]; available: boolean; error: string | null }>),
  subjects: (q: string, qualification: string) => fetch(`/api/v1/curriculum/subjects?q=${encodeURIComponent(q)}&qualification=${encodeURIComponent(qualification)}&limit=15`.replace('&qualification=&', '&')).then(j<Subject[]>),
  results: (id: string) => fetch(`/api/v1/applications/${id}/results`).then(j<{ results: CaseResult[]; counts: Record<string, number>; catalog: string; job: Job | null; pending_units?: { key: string; status: string; error_code: string }[]; status?: string; states?: Record<string, RowStateInfo> }>),
  confirm: (id: string, role: string) => fetch(`/api/v1/applications/${id}/confirm`, { method: 'POST', headers: ROLE_HEADERS(role), body: JSON.stringify({}) }).then(j<{ job_id: string; already_confirmed: boolean }>),
  resume: (jobId: string, role: string) => fetch(`/api/v1/jobs/${jobId}/resume`, { method: 'POST', headers: ROLE_HEADERS(role) }).then(j<{ job_id: string }>),
  include: (id: string, row: string, role: string) => fetch(`/api/v1/applications/${id}/rows/${row}/include`, { method: 'POST', headers: ROLE_HEADERS(role) }).then(j<{ job_id: string }>),
  evidence: (id: string, row: string, role: string, facts: Record<string, unknown>, file?: File | null) => { const f = new FormData(); f.append('facts', JSON.stringify(facts)); if (file) f.append('file', file);
    return fetch(`/api/v1/applications/${id}/rows/${row}/evidence`, { method: 'POST', headers: { 'X-Role': role, 'X-Actor': 'Admissions officer' }, body: f }).then(j<{ job_id: string }>); },
  activity: (id: string) => fetch(`/api/v1/applications/${id}/activity`).then(j<Activity>),
  institution: () => fetch('/api/v1/config/institution').then(j<Institution>),
  recordings: () => fetch('/api/v1/recordings').then(j<Recording[]>),
  saveRecording: (appId: string, name: string, role: string) => fetch('/api/v1/recordings', { method: 'POST', headers: ROLE_HEADERS(role), body: JSON.stringify({ app_id: appId, name }) }).then(j<Recording>),
  replay: (name: string, live: string[], role: string) => fetch(`/api/v1/recordings/${encodeURIComponent(name)}/replay`, { method: 'POST', headers: ROLE_HEADERS(role), body: JSON.stringify({ live }) }).then(j<AppInfo>),
  job: (id: string) => fetch(`/api/v1/jobs/${id}`).then(j<Job>),
  evaluate: (id: string, role: string) => fetch(`/api/v1/applications/${id}/evaluate`, { method: 'POST', headers: ROLE_HEADERS(role) }).then(j<{ job_id: string }>),
  decide: (id: string, course: string, role: string, decision: Decision['decision'], reason: string, version: number) =>
    fetch(`/api/v1/applications/${id}/results/${course}/decision`, { method: 'POST', headers: ROLE_HEADERS(role),
      body: JSON.stringify({ decision, reason, expected_version: version, idempotency_key: crypto.randomUUID() }) }).then(j<Decision>),
  runtime: () => fetch('/api/v1/config/runtime').then(j<{ route: string; model: string; last_latency_s: number | null }>),
  health: () => fetch('/api/v1/health').then(j<{ provider: { ok: boolean; name: string; model: string; detail: string } }>),
  devModel: () => fetch('/api/v1/dev/model').then(j<{ ok: boolean; provider: string; model: string; detail: string; loaded: boolean | null; size_mb: number | null }>),
  devEvents: () => fetch('/api/v1/dev/events?limit=300').then(j<DevEvent[]>),
  reset: () => fetch('/api/v1/dev/reset', { method: 'POST' }).then(j<{ reset: boolean }>),
};
