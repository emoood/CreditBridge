import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronRight, CircleDot, Loader2, Play, Plus, Sparkles, UploadCloud, X } from 'lucide-react';
import { api, type AppInfo, type CaseResult, type Decision, type Job, type Row, type Subject, type Triage } from './api';

const TRIAGE: Record<Triage, { label: string; tone: string }> = {
  READY_TO_ACCEPT: { label: 'Ready to accept', tone: 'green' }, NEEDS_ATTENTION: { label: 'Needs attention', tone: 'amber' },
  CONTESTED: { label: 'Contested', tone: 'red' }, BLOCKED_BY_RULE: { label: 'Blocked by rule', tone: 'red' },
  NEEDS_EVIDENCE: { label: 'Needs evidence', tone: 'neutral' }, POTENTIAL_GE: { label: 'Potential elective', tone: 'blue' }, NO_MATCH: { label: 'No match', tone: 'neutral' } };
const RULE_TONE: Record<string, string> = { PASS: 'green', FAIL: 'red', NEEDS_REVIEW: 'amber', NEEDS_HUMAN_VERIFICATION: 'amber', NOT_APPLICABLE: 'neutral' };
const Pill = ({ tone, children }: { tone: string; children: React.ReactNode }) => <span className={`status status-${tone}`}><span className="status-dot" />{children}</span>;

export default function Evaluation() {
  const [apps, setApps] = useState<AppInfo[]>([]);
  const [appId, setAppId] = useState('DEMO-001');
  const [app, setApp] = useState<AppInfo | null>(null);
  const [results, setResults] = useState<CaseResult[]>([]);
  const [job, setJob] = useState<Job | null>(null);
  const [role, setRole] = useState('admissions');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [qual, setQual] = useState('');
  const [quals, setQuals] = useState<string[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const [list, a, r] = await Promise.all([api.applications(), api.application(appId), api.results(appId)]);
      setApps(list); setApp(a); setResults(r.results);
      setJob(r.job ? await api.job(r.job.id) : null);
      setError(null);
    } catch (e) { setError(`Cannot reach the backend (${(e as Error).message}). Start it with: uvicorn backend.app.main:app --port 8000`); }
  }, [appId]);
  useEffect(() => { setResults([]); setJob(null); refresh(); }, [refresh]);
  useEffect(() => { api.qualifications().then(q => setQuals(q.qualifications)).catch(() => undefined); }, []);
  useEffect(() => { // poll while an evaluation runs
    if (!job || job.status === 'DONE') return;
    const t = setInterval(refresh, 1500); return () => clearInterval(t);
  }, [job, refresh]);

  async function upload(f: File | undefined) {
    if (!f) return;
    setUploading(true); setError(null);
    try { const a = await api.upload(f, '', qual); setAppId(a.id); } catch (e) { setError((e as Error).message); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ''; }
  }
  async function start() {
    setBusy(true);
    try { await api.evaluate(appId, role); setResults([]); await refresh(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const running = job?.status === 'RUNNING' || job?.status === 'QUEUED';
  const counts = results.reduce<Record<string, number>>((m, r) => ({ ...m, [r.triage.category]: (m[r.triage.category] ?? 0) + 1 }), {});
  const isUpload = app?.kind === 'upload';

  return <div className="eval">
    {error && <div className="eval-error"><AlertCircle size={17} /> {error}</div>}
    <section className="panel eval-head">
      <div><div className="section-overline">APPLICATION</div>
        <select className="app-select" aria-label="Select application" value={appId} onChange={e => setAppId(e.target.value)}>
          {apps.map(a => <option key={a.id} value={a.id}>{a.kind === 'demo' ? 'Demo fixtures (synthetic)' : `Uploaded transcript ${a.id}`} · {a.status.replace(/_/g, ' ').toLowerCase()}</option>)}</select>
        <p>{app?.program || (isUpload ? 'Uploaded transcript' : '')}{app?.institution ? ` · ${app.institution}` : ''} · {app?.courses ?? 0} subjects</p>
        <p className="muted">AUD course catalog: {app?.catalog}</p></div>
      <div className="eval-actions">
        <label className="role-pick">Curriculum hint <select value={qual} onChange={e => setQual(e.target.value)}><option value="">Detect from transcript</option>{quals.map(q => <option key={q}>{q}</option>)}</select></label>
        <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg" hidden onChange={e => upload(e.target.files?.[0])} />
        <button className="secondary-button" onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? <Loader2 size={16} className="spin" /> : <UploadCloud size={16} />} {uploading ? 'Reading transcript…' : 'Upload transcript'}</button>
        <label className="role-pick">Acting as <select value={role} onChange={e => setRole(e.target.value)}><option value="admissions">Admissions officer</option><option value="registrar">Registrar (cannot decide)</option></select></label>
        <button className="primary-button" onClick={start} disabled={busy || running || !app?.courses}>{running ? <Loader2 size={17} className="spin" /> : <Play size={17} />} {running ? 'Evaluating…' : results.length ? 'Re-run evaluation' : 'Run AI evaluation'}</button>
      </div>
    </section>

    {isUpload && app && <Verify app={app} quals={quals} qual={qual} locked={running} onChanged={refresh} collapsed={results.length > 0} />}

    {job && <section className="panel progress-panel">
      <div className="panel-header"><div><div className="section-overline">AI PROGRESS</div><h2>{job.status === 'DONE' ? 'Evaluation complete' : 'Agents are working'}</h2></div>
        <span className="muted">{job.route} · {job.model} · {job.llm_calls} model calls</span></div>
      <div className="course-chips">{Object.entries(job.courses).map(([c, s]) => <span key={c} className={`chip chip-${s.toLowerCase()}`}>{s === 'DONE' ? <Check size={13} /> : s === 'RUNNING' ? <CircleDot size={13} /> : null} {c}</span>)}</div>
      {running && <ul className="activity">{[...job.activity].reverse().slice(0, 7).map((a, i) => <li key={i}>
        <span className="muted">{a.course ?? ''}</span> {a.kind === 'tool_call' ? <>{a.role} used <strong>{a.tool}</strong> {a.ok ? '✓' : '✗'}</> : a.kind === 'stage' ? <>stage: <strong>{a.stage}</strong></> : a.kind === 'llm_call' ? <>model call <span className="muted">{a.duration_s}s</span></> : 'error'}</li>)}</ul>}
      {Object.keys(job.errors).length > 0 && <div className="eval-error"><AlertCircle size={16} /> {Object.entries(job.errors).map(([c, m]) => `${c}: ${m}`).join(' · ')} (retryable)</div>}
    </section>}

    {results.length > 0 && <>
      <div className="triage-bar">{(Object.keys(TRIAGE) as Triage[]).filter(t => counts[t]).map(t => <Pill key={t} tone={TRIAGE[t].tone}>{counts[t]} {TRIAGE[t].label}</Pill>)}
        <span className="muted push">Suggestions only. Nothing is final until an admissions officer decides.</span></div>
      <div className="result-list">{results.map(r => <ResultRow key={r.course.code} appId={appId} r={r} role={role} onChanged={refresh} />)}</div>
    </>}
    {!results.length && !job && !error && !isUpload && <section className="panel placeholder-panel"><div className="placeholder-icon"><Sparkles size={26} /></div><h2>No evaluation yet</h2><p>Run the AI evaluation on the demo fixtures, or upload a transcript to try your own.</p></section>}
  </div>;
}

function Verify({ app, quals, qual, locked, onChanged, collapsed }: { app: AppInfo; quals: string[]; qual: string; locked: boolean; onChanged: () => void; collapsed: boolean }) {
  const [open, setOpen] = useState(!collapsed);
  const [page, setPage] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { setOpen(!collapsed); }, [collapsed]);
  const rows = app.rows ?? [];
  const ex = app.extraction;
  const wrap = async (f: () => Promise<unknown>) => { setErr(null); try { await f(); await onChanged(); } catch (e) { setErr((e as Error).message); } };
  const linked = rows.filter(r => r.included && r.subject).length;
  const included = rows.filter(r => r.included).length;
  return <section className="panel verify">
    <button className="verify-head" onClick={() => setOpen(!open)} aria-expanded={open}>
      <div><div className="section-overline">VERIFY EXTRACTION</div><h2>{included} subjects found · {linked} linked to the curriculum</h2></div>
      <span className="muted">{ex?.method === 'ocr' ? 'Read with OCR — check every row' : 'Read from the PDF text'} {open ? '▲' : '▼'}</span></button>
    {open && <>
      {ex?.warnings.map((w, i) => <div className="eval-warn" key={i}><AlertCircle size={15} /> {w}</div>)}
      {app.curriculum && !app.curriculum.available && <div className="eval-error"><AlertCircle size={15} /> The curriculum database is unreachable ({app.curriculum.error}). Subjects cannot be linked.</div>}
      <div className="verify-grid">
        <div className="scan"><div className="scan-tabs">{Array.from({ length: app.document?.page_count ?? 1 }, (_, i) => <button key={i} className={page === i + 1 ? 'on' : ''} onClick={() => setPage(i + 1)}>Page {i + 1}</button>)}</div>
          <img src={api.previewUrl(app.id, page)} alt={`Transcript page ${page}`} /></div>
        <div className="vtable-wrap"><table className="vtable"><thead><tr><th/><th>Code</th><th>Subject (as written)</th><th>Credits</th><th>Grade</th><th>Term</th><th>Curriculum subject</th><th/></tr></thead>
          <tbody>{rows.map(r => <VRow key={r.id} appId={app.id} r={r} quals={quals} qual={qual} locked={locked} wrap={wrap} />)}</tbody></table>
          {rows.length === 0 && <div className="empty-state">No subjects were recognised. Add rows manually.</div>}
          <div className="verify-actions">
            <button className="secondary-button" disabled={locked} onClick={() => wrap(() => api.addRow(app.id))}><Plus size={15} /> Add subject</button>
            <button className="secondary-button" disabled={locked} onClick={() => wrap(() => api.link(app.id, qual || null, true))}>Re-run auto-link</button>
            <span className="muted push">Amber fields were uncertain. Edits are saved as you leave a field and recorded in the audit log.</span></div>
          {ex && ex.unparsed_lines.length > 0 && <details><summary>{ex.unparsed_lines.length} lines were not recognised as subjects</summary><ul>{ex.unparsed_lines.map((u, i) => <li key={i}>p{u.page}: {u.text}</li>)}</ul></details>}
          {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}</div>
      </div></>}
  </section>;
}

function VRow({ appId, r, quals, qual, locked, wrap }: { appId: string; r: Row; quals: string[]; qual: string; locked: boolean; wrap: (f: () => Promise<unknown>) => Promise<void> }) {
  const [picker, setPicker] = useState(false);
  const field = (k: 'code' | 'title' | 'credits' | 'grade' | 'term', w?: number) =>
    <input defaultValue={r[k]} disabled={locked} style={w ? { width: w } : undefined} className={r.uncertain_fields.includes(k) ? 'unsure' : ''}
      aria-label={k} onBlur={e => { if (e.target.value !== r[k]) wrap(() => api.patchRow(appId, r.id, { [k]: e.target.value })); }} />;
  return <><tr className={r.included ? '' : 'off'}>
    <td><input type="checkbox" checked={r.included} disabled={locked} aria-label="Include" onChange={e => wrap(() => api.patchRow(appId, r.id, { included: e.target.checked }))} /></td>
    <td>{field('code', 70)}</td>
    <td>{field('title')}{r.title_ar && <small dir="auto" className="ar">{r.title_ar}</small>}</td>
    <td>{field('credits', 52)}</td><td>{field('grade', 52)}</td><td>{field('term', 90)}</td>
    <td className="subj">{r.subject ? <span className="subj-chosen"><strong>{r.subject.name}</strong><small>{r.subject.qualification}{r.auto_linked ? ' · auto' : ''}</small></span> : <span className="muted">Not linked</span>}
      <button className="link-btn" disabled={locked} onClick={() => setPicker(!picker)}>{r.subject ? 'Change' : 'Choose'}</button></td>
    <td><button className="icon-button" disabled={locked} aria-label="Remove row" onClick={() => wrap(() => api.deleteRow(appId, r.id))}><X size={15} /></button></td></tr>
    {picker && <tr><td colSpan={8}><SubjectPicker r={r} quals={quals} qual={qual} onPick={id => { setPicker(false); wrap(() => api.patchRow(appId, r.id, { subject_id: id })); }} /></td></tr>}</>;
}

function SubjectPicker({ r, quals, qual, onPick }: { r: Row; quals: string[]; qual: string; onPick: (id: string) => void }) {
  const [q, setQ] = useState(''); const [filter, setFilter] = useState(qual); const [res, setRes] = useState<Subject[]>(r.candidates);
  useEffect(() => { if (!q && !filter) { setRes(r.candidates); return; } const t = setTimeout(() => api.subjects(q, filter).then(setRes).catch(() => setRes([])), 200); return () => clearTimeout(t); }, [q, filter, r.candidates]);
  return <div className="picker"><div className="picker-controls"><input autoFocus placeholder="Search curriculum subjects…" value={q} onChange={e => setQ(e.target.value)} />
    <select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All curricula</option>{quals.map(x => <option key={x}>{x}</option>)}</select>
    {r.subject && <button className="link-btn" onClick={() => onPick('')}>Unlink</button>}</div>
    <div className="picker-list">{res.length === 0 && <span className="muted">No matches.</span>}{res.map(s => <button key={s.id} onClick={() => onPick(s.id)}><strong>{s.name}</strong><small>{s.qualification} · {s.code}</small></button>)}</div></div>;
}

function ResultRow({ appId, r, role, onChanged }: { appId: string; r: CaseResult; role: string; onChanged: () => void }) {
  const [open, setOpen] = useState(false);
  const t = TRIAGE[r.triage.category];
  const target = r.match.target;
  const headline = r.match.status === 'POTENTIAL_MATCH' && target ? `${target.code} · ${target.title}` : r.match.status === 'POTENTIAL_GENERAL_ELECTIVE' ? 'Possible general elective' : r.match.status === 'NEEDS_SYLLABUS' ? 'Syllabus missing' : t.label;
  return <article className={`result ${open ? 'result-open' : ''}`}>
    <button className="result-top" onClick={() => setOpen(!open)} aria-expanded={open}>
      <div className="result-subject"><strong>{r.course.row_code || r.course.curriculum_qualification || r.course.code}</strong><span>{r.course.title}</span><small dir="auto">{r.course.title_ar}</small></div>
      <div className="result-grade"><small>GRADE</small><strong>{r.course.grade}</strong></div>
      <div className="result-suggest"><small>SUGGESTION</small><strong>{headline}</strong><span>{r.triage.reasons[0]}</span></div>
      <div className="result-state">{r.decision ? <Pill tone={r.decision.decision === 'ACCEPTED' ? 'green' : r.decision.decision === 'DECLINED' ? 'neutral' : 'blue'}>{r.decision.decision.replace(/_/g, ' ').toLowerCase()}</Pill> : <Pill tone={t.tone}>{t.label}</Pill>}</div>
      {open ? <ChevronDown size={18} /> : <ChevronRight size={18} />}
    </button>
    {open && <div className="result-body"><Reasoning r={r} /><DecisionBar appId={appId} r={r} role={role} onChanged={onChanged} /></div>}
  </article>;
}

function Reasoning({ r }: { r: CaseResult }) {
  const m = r.match;
  return <div className="reasoning">
    {m.supported_outcomes.length > 0 && <div className="block"><h4>Outcomes the evidence supports {m.evidence_strength && <span className="muted">· coverage {m.evidence_strength.toLowerCase()}</span>}</h4>
      {m.supported_outcomes.map((o, i) => <div className="pair" key={i}><div><small>SUBJECT · {o.subject.id}</small><p>{o.subject.text}</p></div><div><small>AUD COURSE · {o.target.id}</small><p>{o.target.text}</p></div><div className="why">{o.explanation}</div></div>)}</div>}
    {m.missing_or_weaker_outcomes.length > 0 && <div className="block"><h4>Missing or weaker</h4><ul>{m.missing_or_weaker_outcomes.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
    {m.concerns.length > 0 && <div className="block"><h4>Concerns</h4><ul>{m.concerns.map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
    {r.review && <div className="block"><h4>Independent review <Pill tone={r.review.status === 'SUPPORTED_FOR_HUMAN_REVIEW' ? 'green' : r.review.status === 'CHALLENGED' ? 'red' : 'amber'}>{r.review.status.replace(/_/g, ' ').toLowerCase()}</Pill></h4>
      {r.revised && <p className="muted">The matcher revised after the reviewer’s challenge{r.contested ? ' and the reviewer still disputes it.' : '.'}{m.response_to_objections ? ` Matcher’s response: ${m.response_to_objections}` : ''}</p>}
      {r.review.objections.map((o, i) => <div className="objection" key={i}><p>{o.text}</p>{o.excerpts.map(e => <blockquote key={e.id}><small>{e.id}</small>{e.text}</blockquote>)}</div>)}</div>}
    {r.original_claim && <div className="block withdrawn"><h4>The matcher first suggested {r.original_claim.target ? `${r.original_claim.target.code} · ${r.original_claim.target.title}` : 'a course'}, then withdrew</h4>
      {r.original_claim.objections.map((o, i) => <p key={i}><strong>Reviewer:</strong> {o}</p>)}
      {r.original_claim.supported_outcomes.map((o, i) => <div className="pair" key={i}><div><small>SUBJECT · {o.subject.id}</small><p>{o.subject.text}</p></div><div><small>AUD COURSE · {o.target.id}</small><p>{o.target.text}</p></div></div>)}</div>}
    {r.rules.length > 0 && <div className="block"><h4>Policy checks <span className="muted">· computed by code, not AI</span></h4><div className="rules">{r.rules.map(x => <div key={x.rule_id}><Pill tone={RULE_TONE[x.status] ?? 'neutral'}>{x.status.replace(/_/g, ' ').toLowerCase()}</Pill> <strong>{x.rule_id.replace('AUD_', '').replace(/_/g, ' ').toLowerCase()}</strong> <span className="muted">{x.reason}</span></div>)}</div>
      {r.staff_checklist.length > 0 && <details><summary>Staff checklist ({r.staff_checklist.length} items to confirm manually)</summary><ul>{r.staff_checklist.map(x => <li key={x.rule_id}>{x.reason}</li>)}</ul></details>}</div>}
    {r.syllabus_requests.length > 0 && <div className="block"><h4>Request drafted</h4><p>An in-app request for the missing {r.syllabus_requests[0].missing_fields.join(', ')} was drafted ({r.syllabus_requests[0].status.toLowerCase().replace(/_/g, ' ')}). Nothing was sent.</p></div>}
    <details className="block"><summary>How the agents worked</summary>
      <p className="muted">{r.provider} · {r.model} · {r.llm_calls} model calls · {r.duration_s}s{r.is_replay ? ' · REPLAY' : ''}</p>
      {r.case_file.queries.length > 0 && <p><strong>Searches:</strong> {r.case_file.queries.join(' · ')}</p>}
      {Object.entries(r.case_file.targets_read).map(([who, ids]) => <p key={who}><strong>{who} read:</strong> {ids.join(', ')}</p>)}
      {r.case_file.notes.map((n, i) => <p key={i}><strong>{n.by} {n.kind}:</strong> {n.text}</p>)}
      {r.match.flags.length > 0 && <p className="muted">Checks: {r.match.flags.join(', ')}</p>}</details>
  </div>;
}

function DecisionBar({ appId, r, role, onChanged }: { appId: string; r: CaseResult; role: string; onChanged: () => void }) {
  const [reason, setReason] = useState(''); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const canAccept = ['POTENTIAL_MATCH', 'POTENTIAL_GENERAL_ELECTIVE'].includes(r.match.status) && r.triage.category !== 'BLOCKED_BY_RULE';
  async function go(d: Decision['decision']) {
    setBusy(true); setErr(null);
    try { await api.decide(appId, r.course.code, role, d, reason, r.version); setReason(''); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (r.decision) return <div className="decision done"><strong>{r.decision.decision.replace(/_/g, ' ').toLowerCase()}</strong> by {r.decision.actor} · {new Date(r.decision.at).toLocaleTimeString()}{r.decision.reason && <> — “{r.decision.reason}”</>}</div>;
  return <div className="decision">
    <p className="muted">This is an AI suggestion, not a decision. Accept or decline it as the admissions officer.</p>
    <input placeholder="Reason (required to decline or request information)" value={reason} onChange={e => setReason(e.target.value)} />
    <div className="decision-buttons">
      <button className="primary-button" disabled={busy || !canAccept} onClick={() => go('ACCEPTED')} title={canAccept ? '' : 'Nothing to accept, or blocked by a policy rule'}><Check size={16} /> Accept</button>
      <button className="secondary-button" disabled={busy} onClick={() => go('DECLINED')}><X size={16} /> Decline</button>
      <button className="secondary-button" disabled={busy} onClick={() => go('MORE_INFORMATION_REQUESTED')}>Request information</button>
    </div>
    {err && <div className="eval-error"><AlertCircle size={16} /> {err}</div>}
  </div>;
}
