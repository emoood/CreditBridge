import { useEffect, useMemo, useState } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronRight, ClipboardCopy, Download, History, Loader2, Paperclip, Play, ShieldCheck, X } from 'lucide-react';
import { api, type AppInfo, type Sheet } from './api';
import { Activity } from './Activity';
import { Evidence } from './Evidence';
import { ReviewBody, RowPanel } from './Review';
import { ExtractionCheck } from './ExtractionCheck';
import { appStatus, build, CLASS_LABEL, curriculumOf, isOpen, plainSummary, RESOLUTION_LABEL, toCsv, toTsv, type Item } from './model';
import { AppPill, RowStatusText } from './ui';
import { useApp, useInclude, useInstitution } from './useApp';
import { useDemo } from './demo';

export function StudentPopup({ appId, role, quals, qual, onClose, onChanged }: { appId: string; role: string; quals: string[]; qual: string; onClose: () => void; onChanged: () => void }) {
  const { data, error, refresh } = useApp(appId);
  const inst = useInstitution();
  const demoMode = useDemo();
  const [include, setInclude] = useInclude(appId);
  const [evidenceKey, setEvidenceKey] = useState<string | null>(null);
  const [activity, setActivity] = useState(false);
  const [notEvalOpen, setNotEvalOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [exported, setExported] = useState(false);
  const [adv, setAdv] = useState<string | null>(null);
  const [sheet, setSheet] = useState<Sheet | null>(null);
  const evaluating = !!data && (['CONFIRMED', 'EVALUATION_IN_PROGRESS'].includes(data.app.status) || data.evalJob?.status === 'RUNNING' || data.evalJob?.status === 'QUEUED');
  const built = useMemo(() => data ? build(data.app, data.results, data.pending, include, evaluating, data.states, data.actions) : null, [data, include, evaluating]);
  const jobs = data ? [data.readJob, data.evalJob] : [];
  const status = data && built ? appStatus(data.app, built, jobs) : null;
  const confirmed = !!data?.app.confirmed;
  useEffect(() => { if (confirmed && !evaluating) api.sheet(appId).then(setSheet).catch(() => undefined); }, [appId, confirmed, evaluating, data?.results.length, data?.actions, data?.app.status, data?.app.extraction]);
  const reload = async () => { await refresh(); onChanged(); };
  useEffect(() => { // after an action the panel moves to the next open row; with none left it closes and the overview shows
    if (adv === null || !built) return;
    const next = built.relevant.filter(isOpen).find(i => i.rowId !== adv);
    setEvidenceKey(next?.key ?? null); setAdv(null);
  }, [adv]); // eslint-disable-line react-hooks/exhaustive-deps
  async function run(f: () => Promise<unknown>) { setBusy(true); setErr(null); try { await f(); await reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }

  if (!data || !built || !status) return <Shell onClose={onClose}>{error ? <div className="eval-error"><AlertCircle size={16} /> Cannot reach the backend ({error}). Start it with scripts/start.sh</div> : <p className="muted pad"><Loader2 size={15} className="spin" /> Loading…</p>}</Shell>;

  const { app, results, readJob, evalJob } = data;
  const c = built.counts;
  const reading = app.status === 'READING';
  const gate = app.status === 'EXTRACTION_REVIEW_REQUIRED';
  const demoIdle = app.kind === 'demo' && results.length === 0 && !evaluating;
  const selected = built.items.find(i => i.key === evidenceKey) ?? null;
  const interrupted = [readJob, evalJob].find(j => j && (j.status === 'INTERRUPTED' || j.status === 'FAILED'));
  const approved = !!sheet?.approved;
  const canApprove = role === 'admissions' && !approved && c.unresolved === 0 && c.total > 0 && !!sheet?.ok && !evaluating;
  const summary = [[c.mapped, 'mapped'], [c.conditional, 'conditional'], [c.noMapping, 'no mapping'], [c.waiting, 'waiting on a document']].filter(([n]) => (n as number) > 0).map(([n, l]) => `${n} ${l}`).join(' · ');

  /** Every export goes through the Auditor first (unless it already reviewed this exact sheet and found it clean). Returns the sheet if it may be exported. */
  async function auditedSheet(what: string): Promise<Sheet | null> {
    const chk = await api.exportCheck(appId, role);
    if (!chk.ready && chk.job_id) {
      setToast('The Auditor is reviewing the sheet…');
      for (let n = 0; n < 150; n++) { const j = await api.job(chk.job_id); if (j.status === 'DONE' || j.status === 'FAILED' || j.status === 'INTERRUPTED') break; await new Promise(r => setTimeout(r, 1000)); }
      setToast(null);
    }
    const s = await api.sheet(appId); setSheet(s); await refresh();
    const reviewed = s.audit?.sheet_hash === s.sheet_hash;
    if (!s.ok || !reviewed || !s.audit?.clean) { setErr(!s.ok ? `${s.checks.length} sheet check(s) fail. Nothing was ${what}. See the Auditor box.` : !reviewed ? `The Auditor could not review the sheet. Nothing was ${what}.` : `The Auditor found differences from your decisions. Nothing was ${what}. See the Auditor box.`); return null; }
    return s;
  }
  async function copySheet() {
    setBusy(true); setErr(null);
    try {
      const s = await auditedSheet('copied'); if (!s) return;
      await navigator.clipboard.writeText(toTsv(s)); setExported(true); setToast(`Copied ${s.rows.length} rows${s.unconfirmed ? ` (${s.unconfirmed} unconfirmed, marked)` : ''}. The Auditor reviewed this sheet.`);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  async function downloadCsv() {
    setBusy(true); setErr(null);
    try {
      const s = await auditedSheet('downloaded'); if (!s) return;
      const url = URL.createObjectURL(new Blob([toCsv(s)], { type: 'text/csv' }));
      setExported(true); const a = document.createElement('a'); a.href = url; a.download = `creditbridge-${appId}.csv`; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  const isUpload = app.kind === 'upload';
  const advance = async () => { const row = selected?.rowId ?? null; await reload(); setAdv(row); };
  const newPanel = isUpload && selected && !activity ? <RowPanel key={selected.key} appId={appId} item={selected} role={role} program={app.program} onClose={() => setEvidenceKey(null)} onAdvance={advance} /> : null;
  return <Shell onClose={onClose} wide={gate} side={isUpload && (newPanel || activity) ? (newPanel ?? <Activity data={data} onClose={() => setActivity(false)} />) : selected?.result ? <Evidence appId={appId} r={selected.result} item={selected} role={role} onChanged={reload} onClose={() => setEvidenceKey(null)} />
    : activity ? <Activity data={data} onClose={() => setActivity(false)} /> : null}>
    <header className="pop-head">
      <div><small>STUDENT</small><h2>{app.student}</h2>
        <p className="muted">ID {app.student_id || app.id} · {app.program || curriculumOf(app, results)}{app.program ? ` · ${curriculumOf(app, results)}` : ''}{app.student_record?.term ? ` · ${app.student_record.term}` : ''}{app.student_record ? ` · case ${app.student_record.case_number}` : ''}</p>
        {app.student_record && <details className="rv-contact"><summary>Contact details</summary><p className="muted">{app.student_record.email} · {app.student_record.phone}</p></details>}</div>
      <div className="pop-head-right">{approved ? <span className="approved-tag"><Check size={13} /> Approved</span> : isUpload && !reading && !gate && !evaluating ? null : <AppPill s={status} />}<button className="icon-button" onClick={onClose} aria-label="Close"><X size={22} /></button></div>
    </header>
    {app.replay && <div className="replay-banner"><History size={15} /> <span><strong>{app.replay.label}</strong>. {app.replay.live.length ? `Nothing here was produced just now except ${app.replay.live.length} course(s) run live.` : 'Nothing here was produced just now: no model was called.'}</span></div>}
    {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}
    {toast && <div className="toast-line" onClick={() => setToast(null)}><Check size={14} /> {toast}</div>}
    {interrupted && <div className="eval-warn"><AlertCircle size={15} /> The {interrupted.phase === 'READING' ? 'reading' : 'evaluation'} was {interrupted.status.toLowerCase()}. Finished work is kept.
      <button className="link-btn" onClick={() => run(() => api.resume(interrupted.id, role))}>Resume</button></div>}

    {demoMode.on && app.replay === undefined && <StageStrip app={app} evalStage={evalJob?.stage} evalStatus={evalJob?.status} audited={!!sheet?.audit} approved={approved} exported={exported} />}
    {reading && <ReadingProgress done={(readJob?.units ?? []).filter(u => u.stage === 'read' && u.status === 'DONE').length} total={(readJob?.units ?? []).filter(u => u.stage === 'read').length} stage={readJob?.stage} />}
    {gate && <ExtractionCheck display={inst.display} app={app} quals={quals} qual={qual} role={role} onChanged={reload} />}
    {demoIdle && <div className="pad"><p className="muted">The demo fixtures have not been evaluated yet.</p><button className="primary-button" disabled={busy} onClick={() => run(() => api.evaluate(appId, role))}><Play size={16} /> Run AI evaluation</button></div>}
    {evaluating && <p className="muted pad"><Loader2 size={14} className="spin" /> Working: {results.length} course{results.length === 1 ? '' : 's'} done so far. Results appear as they finish.</p>}

    {!reading && !gate && !demoIdle && isUpload && <div className="pop-body"><ReviewBody built={built} summary={plainSummary(built, app.program, app.extraction?.case_summary?.text ?? '')} role={role} appId={appId} busy={busy} program={app.program} approved={approved} canApprove={canApprove}
      selectedKey={evidenceKey} onOpen={setEvidenceKey} onRun={run} onCopy={copySheet} onCsv={downloadCsv} onActivity={() => { setActivity(true); setEvidenceKey(null); }}>
      {sheet?.planted && <section className="planted"><strong>PLANTED FOR DEMO</strong> One confirmed AUD equivalent ({sheet.planted.source_course}: {sheet.planted.from} → {sheet.planted.to}) was changed in the output stage only. Your decisions are untouched. The sheet checks below should catch it.{' '}
        <button className="link-btn" disabled={busy} onClick={() => run(() => api.plantCopyError(appId, role, false))}>Remove</button></section>}
      {sheet && !sheet.ok && <section className="checks"><h4>Sheet checks failed ({sheet.checks.length})</h4>
        <ul>{sheet.checks.map((k, i) => <li key={i}><strong>{k.source_course}</strong> · {k.check.replace(/_/g, ' ')}: {k.message}</li>)}</ul>
        <button className="secondary-button" disabled={busy} onClick={() => run(() => api.runAudit(appId, role))}>Explain with the Auditor</button></section>}
      {sheet?.audit && (!sheet.audit.clean || sheet.audit.proposals.length > 0 || (sheet.audit.warnings ?? []).length > 0) && <AuditBox appId={appId} role={role} audit={sheet.audit} onChanged={reload} />}
    </ReviewBody></div>}
    {!reading && !gate && !demoIdle && !isUpload && <div className="pop-body">
      <div className="selection"><div><strong>{built.relevant.length} of {c.total} courses relevant</strong> <button className="link-btn" onClick={() => setNotEvalOpen(true)}>Adjust</button>
        {data.selectionRule && <p className="muted rule">{data.selectionRule}</p>}
        {app.extraction?.case_summary?.text && <p className="case-summary"><strong>{app.extraction.case_summary.by === 'code' ? 'Summary' : 'Case agent'}:</strong> {app.extraction.case_summary.text}</p>}</div>
        <span className="muted push">{summary}{c.resolved ? `${summary ? ' · ' : ''}${c.resolved} resolved` : ''}</span></div>

      {built.relevant.length > 0 && <table className="ctable"><thead><tr><th>Source course</th><th>Grade</th><th>AUD equivalent</th>{inst.display.aud_credits && <th className="num">AUD credits</th>}<th>Program</th><th>Status</th></tr></thead>
        <tbody>{built.relevant.map(i => <tr key={i.key} className={`${evidenceKey === i.key ? 'sel' : ''} ${i.result ? 'click' : ''}`} onClick={() => i.result && setEvidenceKey(i.key)}>
          <td><strong>{i.title}</strong></td><td>{i.grade || '—'}</td>
          <td>{equivalent(i)}</td>{inst.display.aud_credits && <td className="num">{i.audCode ? i.audCredits ?? '—' : '—'}</td>}
          <td className={`prog ${i.cls?.value === 'Not applicable' ? 'prog-off' : ''}`} title={i.cls ? `${i.cls.why}${i.cls.by === 'case_agent' ? ' (judged by the Case agent)' : ''}` : ''}>{i.cls ? CLASS_LABEL[i.cls.value] : '—'}
            {i.doubts > 0 && <span className="obj-badge" title={(i.state?.doubts ?? []).map(d => `${d.kind.replace(/_/g, ' ')}: ${d.reason}${d.verified ? '' : ' (not verified by code)'}`).join('\n')}>{i.doubts} doubt{i.doubts === 1 ? '' : 's'}</span>}</td>
          <td className="status-cell"><RowStatusText s={i.status} />{i.resolved && i.action && <span className="res-tag">{RESOLUTION_LABEL[i.action.action]}</span>}
            {i.action?.action === 'SEND_FOR_REVIEW' && !i.resolved && <span className="res-tag">Sent for review</span>}
            {i.objections > 0 && <span className="obj-badge" title="The reviewer objects. Open the row for the full text.">{i.objections} objection{i.objections === 1 ? '' : 's'}</span>}</td></tr>)}</tbody></table>}

      {sheet?.planted && <section className="planted"><strong>PLANTED FOR DEMO</strong> One confirmed AUD equivalent ({sheet.planted.source_course}: {sheet.planted.from} → {sheet.planted.to}) was changed in the output stage only. Your decisions are untouched. The sheet checks below should catch it.{' '}
        <button className="link-btn" disabled={busy} onClick={() => run(() => api.plantCopyError(appId, role, false))}>Remove</button></section>}
      {sheet && !sheet.ok && <section className="checks"><h4>Sheet checks failed ({sheet.checks.length})</h4>
        <ul>{sheet.checks.map((k, i) => <li key={i}><strong>{k.source_course}</strong> · {k.check.replace(/_/g, ' ')}: {k.message}</li>)}</ul>
        <button className="secondary-button" disabled={busy} onClick={() => run(() => api.runAudit(appId, role))}>Explain with the Auditor</button></section>}
      {sheet?.audit && (!sheet.audit.clean || sheet.audit.proposals.length > 0 || (sheet.audit.warnings ?? []).length > 0) && <AuditBox appId={appId} role={role} audit={sheet.audit} onChanged={reload} />}

      {built.relevant.length > 0 && <p className="muted placeholder-note">AUD codes and credits shown are placeholder mapping data for this demo, not AUD&apos;s real equivalencies.</p>}

      {built.needsInput.length > 0 && <section className="needs"><h4>Open items <span className="count">{built.needsInput.length}</span></h4>
        <div className="needs-cards">{built.needsInput.map(i => <NeedCard key={i.key} i={i} appId={appId} role={role} busy={busy} onOpen={() => i.result && setEvidenceKey(i.key)} onChanged={reload} />)}</div></section>}

      {built.notEvaluated.length > 0 && <details className="noteval" open={notEvalOpen} onToggle={e => setNotEvalOpen((e.target as HTMLDetailsElement).open)}>
        <summary>{notEvalOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />} Not evaluated ({built.notEvaluated.length})</summary>
        <ul>{[...built.notEvaluated.filter(i => i.state?.kind !== 'school_specific'), ...built.notEvaluated.filter(i => i.state?.kind === 'school_specific')].map(i => <li key={i.key + i.rowId}><span><strong>{i.title}</strong> <span className="muted">· {i.state?.kind === 'school_specific' ? 'School-specific: not mapped' : i.reason}</span></span>
          {i.canInclude ? <button className="link-btn" disabled={busy} onClick={() => i.state ? run(() => api.include(appId, i.rowId, role)) : setInclude(i.rowId, true)}>Include</button> : <span className="muted">cannot be mapped</span>}</li>)}</ul></details>}
    </div>}

    {!reading && !gate && !isUpload && <footer className="pop-foot">
      <span className="muted"><ShieldCheck size={13} /> Suggestions only. Nothing is final until you confirm.{c.unresolved > 0 && !approved ? ` ${c.unresolved} row${c.unresolved === 1 ? '' : 's'} left to resolve.` : ''}</span>
      <div className="pop-foot-actions">
        <button className="secondary-button" onClick={copySheet} disabled={busy || !c.resolved && !c.mapped} title="Tab-separated, ready to paste into a spreadsheet. Unconfirmed rows are marked."><ClipboardCopy size={15} /> Copy for spreadsheet</button>
        <button className="secondary-button" onClick={downloadCsv} disabled={busy || !c.resolved && !c.mapped}><Download size={15} /> CSV</button>
        <button className="secondary-button" onClick={() => run(() => api.confirmMapped(appId, role))} disabled={busy || !c.confirmable || role !== 'admissions'}>Confirm mapped{c.confirmable ? ` (${c.confirmable})` : ''}</button>
        <button className="primary-button" onClick={() => run(() => api.approve(appId, role))} disabled={busy || !canApprove}
          title={approved ? 'Already approved' : c.unresolved ? `${c.unresolved} relevant row(s) are not resolved yet` : !sheet?.ok ? 'The sheet checks must pass' : role !== 'admissions' ? 'Only an admissions officer can approve' : ''}>Approve</button>
        {inst.demo_mode && confirmed && !approved && c.resolved > 0 && !sheet?.planted && <button className="link-btn demo-switch" disabled={busy} title="Changes one confirmed AUD equivalent in the output stage so the checks and the Auditor have something to catch" onClick={() => run(() => api.plantCopyError(appId, role, true))}>Demo: plant a copy error</button>}
        <button className="secondary-button" onClick={() => { setActivity(!activity); setEvidenceKey(null); }}>Activity</button>
      </div></footer>}
  </Shell>;
}

function equivalent(i: Item): string {
  if (i.action && ['REJECT', 'NO_CREDIT'].includes(i.action.action)) return 'No credit';
  if (i.audCode) return i.audCode;
  if (i.status === 'Waiting on document') return 'Waiting on the document';
  if (i.result?.match.status === 'POTENTIAL_GENERAL_ELECTIVE') return 'General elective (AI flag)';
  return '—';
}

/** One unresolved row: what the Case agent found, what is needed, and the ways to move it forward. The only amber on the screen. */
function NeedCard({ i, appId, role, busy, onOpen, onChanged }: { i: Item; appId: string; role: string; busy: boolean; onOpen: () => void; onChanged: () => void }) {
  const [mode, setMode] = useState<'note' | 'nocredit' | null>(null);
  const [text, setText] = useState(''); const [file, setFile] = useState<File | null>(null);
  const [working, setWorking] = useState(false); const [err, setErr] = useState<string | null>(null);
  const s = i.state; const f = s?.finding;
  const needed = (f?.missing_evidence?.length ? f.missing_evidence : s?.condition_result?.missing?.map(m => `${m.field.replace(/_/g, ' ')} (${m.evidence})`)) ?? [];
  const open = (s?.requests ?? []).filter(q => q.status !== 'RECEIVED');
  async function go(fn: () => Promise<unknown>) { setWorking(true); setErr(null); try { await fn(); setMode(null); setText(''); setFile(null); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setWorking(false); } }
  return <div className="need-card">
    <div className="need-head"><strong>{i.title}</strong><small>{i.grade}</small>{i.action?.action === 'SEND_FOR_REVIEW' && <small className="res-tag">Sent for review</small>}</div>
    <p className="what"><span className="lbl">{i.status}</span> {i.finding ?? i.reason}</p>
    {needed.length > 0 && <p className="need-list"><span className="lbl">Needed</span> {needed.join('; ')}</p>}
    {open.length > 0 && <p className="need-list"><span className="lbl">Draft request</span> {open[0].what}</p>}
    {f?.candidates && f.candidates.length > 0 && <p className="need-list"><span className="lbl">Candidates</span> {f.candidates.map(c => c.aud_code).join(', ')}</p>}
    {mode === 'note' && <div className="card-form"><textarea autoFocus rows={2} value={text} onChange={e => setText(e.target.value)} placeholder="Note for the agent, e.g. this is AP Physics C Mechanics" />
      <label className="file-pick"><Paperclip size={13} /> {file ? file.name : 'Attach a document (optional)'}<input type="file" hidden onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <div className="card-actions"><button className="secondary-button" disabled={working || (!text.trim() && !file)} onClick={() => go(() => api.evidence(appId, i.rowId, role, {}, file, text))}>{working ? 'Handing back…' : 'Hand back to the agent'}</button>
        <button className="link-btn" onClick={() => setMode(null)}>Cancel</button></div></div>}
    {mode === 'nocredit' && <div className="card-form"><input autoFocus value={text} onChange={e => setText(e.target.value)} placeholder="Why no credit? (required)" />
      <div className="card-actions"><button className="secondary-button" disabled={working || !text.trim()} onClick={() => go(() => api.rowAction(appId, i.rowId, role, { action: 'NO_CREDIT', reason: text }))}>No credit</button>
        <button className="link-btn" onClick={() => setMode(null)}>Cancel</button></div></div>}
    {!mode && <div className="card-actions">
      <button className="secondary-button" disabled={busy || working} onClick={onOpen}>{i.status === 'No mapping' ? 'Search an AUD course' : 'Open evidence'}</button>
      <button className="secondary-button" disabled={busy || working} onClick={() => setMode('note')}>Add note or document</button>
      <button className="secondary-button" disabled={busy || working || i.action?.action === 'SEND_FOR_REVIEW'} onClick={() => go(() => api.rowAction(appId, i.rowId, role, { action: 'SEND_FOR_REVIEW' }))}>Send for review</button>
      <button className="link-btn" disabled={busy || working} onClick={() => setMode('nocredit')}>No credit…</button></div>}
    {err && <div className="eval-error"><AlertCircle size={14} /> {err}</div>}
  </div>;
}

function AuditBox({ appId, role, audit, onChanged }: { appId: string; role: string; audit: NonNullable<Sheet['audit']>; onChanged: () => void }) {
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const pending = audit.proposals.filter(p => !audit.applied.includes(p.row_id));
  async function apply(ids: string[]) { setBusy(true); setErr(null); try { await api.applyAudit(appId, role, ids); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }
  return <section className="checks audit"><h4>Auditor</h4><p>{audit.summary}</p>
    {(audit.warnings ?? []).map((w, i) => <p key={i} className="muted">Note: <strong>{w.source_course}</strong>: {w.message}</p>)}
    {audit.explanations.map(e => <p key={e.row_id}>{e.explanation}</p>)}
    {pending.map(p => <div className="proposal" key={p.row_id}><strong>{p.source_course}</strong>: {p.from.aud_code ?? '—'} ({p.from.credits ?? '—'} cr) → <strong>{p.to.aud_code}</strong> ({p.to.credits} cr)
      <span className="muted"> · {p.rechecked ? 're-checked by code · ' : ''}{p.restores_decision ? 'restores your decision · ' : ''}{p.why}{p.touches_confirmed ? ' · changes a row you already confirmed' : ''}</span>
      <button className="secondary-button" disabled={busy} onClick={() => apply([p.row_id])}>Apply this change</button></div>)}
    {pending.length === 0 && audit.proposals.length > 0 && <p className="muted">All proposed changes were applied.</p>}
    {!audit.clean && audit.proposals.length === 0 && <p className="muted">No automatic correction was proposed; this needs your judgment.</p>}
    {audit.agent_comment && <p className="muted"><em>Auditor&apos;s own comment (not verified by code): {audit.agent_comment}</em></p>}
    {err && <div className="eval-error"><AlertCircle size={14} /> {err}</div>}</section>;
}

/** The stages of a case, one after another: shown in Demo mode so the audience can follow where the work is. */
function StageStrip({ app, evalStage, evalStatus, audited, approved, exported }: { app: AppInfo; evalStage?: string; evalStatus?: string; audited: boolean; approved: boolean; exported: boolean }) {
  const reading = app.status === 'READING';
  const gate = app.status === 'EXTRACTION_REVIEW_REQUIRED';
  const evaluating = evalStatus === 'RUNNING' || evalStatus === 'QUEUED' || app.status === 'CONFIRMED' || app.status === 'EVALUATION_IN_PROGRESS';
  const past = !reading && !gate;
  const rank = { Intake: reading ? 1 : 2, 'Relevance gate': !past ? 0 : evaluating && (evalStage === 'lookup' || !evalStage) ? 1 : 2, 'Mapping lookup': !past ? 0 : evaluating && (evalStage === 'lookup' || !evalStage) ? 1 : 2,
    'Case agent': !past ? 0 : evaluating && evalStage !== 'lookup' && !!evalStage ? 1 : evaluating ? 0 : 2, 'Officer review': past && !evaluating ? (approved || exported ? 2 : 1) : 0, Auditor: audited ? 2 : exported ? 1 : 0, 'Copy / CSV': exported ? 2 : 0 };
  return <div className="stage-strip" aria-label="Stages">{Object.entries(rank).map(([k, v]) => <span key={k} className={v === 2 ? 'done' : v === 1 ? 'active' : ''}>{v === 2 ? '✓ ' : ''}{k}</span>)}</div>;
}

function ReadingProgress({ done, total, stage }: { done: number; total: number; stage?: string }) {
  return <div className="pad"><p><Loader2 size={15} className="spin" /> Reading the transcript{stage ? ` (${stage})` : ''}…</p>
    <div className="bar"><div style={{ width: `${total ? Math.round((done / total) * 100) : 5}%` }} /></div><p className="muted">{done} of {total || '…'} page blocks read. Nothing is evaluated until you confirm the extraction.</p></div>;
}

function Shell({ children, onClose, side, wide }: { children: React.ReactNode; onClose: () => void; side?: React.ReactNode; wide?: boolean }) {
  return <div className="pop-backdrop" onClick={onClose}><div className={`pop ${wide ? 'pop-wide' : ''} ${side ? 'pop-with-side' : ''}`} onClick={e => e.stopPropagation()} role="dialog" aria-label="Student">
    <div className="pop-main">{children}</div>{side}</div></div>;
}
