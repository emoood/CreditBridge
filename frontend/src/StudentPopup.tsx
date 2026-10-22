import { useMemo, useState } from 'react';
import { AlertCircle, ChevronDown, ChevronRight, Download, History, Loader2, Play, ShieldCheck, X } from 'lucide-react';
import { api } from './api';
import { Activity } from './Activity';
import { Evidence } from './Evidence';
import { ExtractionCheck } from './ExtractionCheck';
import { appStatus, build, curriculumOf, toCsv, type Item } from './model';
import { AppPill, RowPill } from './ui';
import { useApp, useInclude } from './useApp';

export function StudentPopup({ appId, role, quals, qual, onClose, onChanged }: { appId: string; role: string; quals: string[]; qual: string; onClose: () => void; onChanged: () => void }) {
  const { data, error, refresh } = useApp(appId);
  const [include, setInclude] = useInclude(appId);
  const [evidenceKey, setEvidenceKey] = useState<string | null>(null);
  const [activity, setActivity] = useState(false);
  const [notEvalOpen, setNotEvalOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const evaluating = !!data && (['CONFIRMED', 'EVALUATION_IN_PROGRESS'].includes(data.app.status) || data.evalJob?.status === 'RUNNING' || data.evalJob?.status === 'QUEUED');
  const built = useMemo(() => data ? build(data.app, data.results, data.pending, include, evaluating) : null, [data, include, evaluating]);
  const jobs = data ? [data.readJob, data.evalJob] : [];
  const status = data && built ? appStatus(data.app, built, jobs) : null;
  const reload = async () => { await refresh(); onChanged(); };
  async function run(f: () => Promise<unknown>) { setBusy(true); setErr(null); try { await f(); await reload(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }

  if (!data || !built || !status) return <Shell onClose={onClose}>{error ? <div className="eval-error"><AlertCircle size={16} /> Cannot reach the backend ({error}). Start it with scripts/start.sh</div> : <p className="muted pad"><Loader2 size={15} className="spin" /> Loading…</p>}</Shell>;

  const { app, results, readJob, evalJob } = data;
  const reading = app.status === 'READING';
  const gate = app.status === 'EXTRACTION_REVIEW_REQUIRED';
  const demoIdle = app.kind === 'demo' && results.length === 0 && !evaluating;
  const selected = results.find(r => r.course.code === evidenceKey) ?? null;
  const approvable = built.items.filter(i => i.status === 'Mapped' && i.result && !i.decided && i.objections === 0);
  const interrupted = [readJob, evalJob].find(j => j && (j.status === 'INTERRUPTED' || j.status === 'FAILED'));

  async function act(i: Item) {
    const r = i.result; const a = i.action;
    if (!a) return;
    if (a.kind === 'open' && r) { setEvidenceKey(r.course.code); return; }
    if (a.kind === 'retry' && evalJob) return run(() => api.resume(evalJob.id, role));
    if (!r) return;
    if (a.kind === 'accept_ge') return run(() => api.decide(appId, r.course.code, role, 'ACCEPTED', 'Accepted as a general elective', r.version));
    if (a.kind === 'confirm_no_credit') return run(() => api.decide(appId, r.course.code, role, 'DECLINED', 'No AUD equivalent (confirmed by the officer)', r.version));
  }
  async function approveAll() {
    if (!approvable.length || !window.confirm(`Accept ${approvable.length} mapped course${approvable.length === 1 ? '' : 's'} with no reviewer objection? Courses with objections stay for you to decide one by one.`)) return;
    await run(async () => { for (const i of approvable) await api.decide(appId, i.result!.course.code, role, 'ACCEPTED', '', i.result!.version); });
  }
  function exportCsv() {
    const url = URL.createObjectURL(new Blob([toCsv(built!.relevant)], { type: 'text/csv' }));
    const a = document.createElement('a'); a.href = url; a.download = `creditbridge-${appId}.csv`; a.click(); URL.revokeObjectURL(url);
  }

  return <Shell onClose={onClose} wide={gate} side={selected ? <Evidence appId={appId} r={selected} role={role} onChanged={reload} onClose={() => setEvidenceKey(null)} />
    : activity ? <Activity data={data} onClose={() => setActivity(false)} /> : null}>
    <header className="pop-head">
      <div><small>STUDENT</small><h2>{app.student}</h2>
        <p className="muted">{curriculumOf(app, results)} curriculum · {app.id}{app.program ? ` · ${app.program}` : ''}</p></div>
      <div className="pop-head-right"><AppPill s={status} /><button className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button></div>
    </header>
    {app.replay && <div className="replay-banner"><History size={15} /> <span><strong>{app.replay.label}</strong>. Nothing here was produced just now except {app.replay.live.length ? `${app.replay.live.length} course(s) run live` : 'nothing (no course was run live)'}.</span></div>}
    {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}
    {interrupted && <div className="eval-warn"><AlertCircle size={15} /> The {interrupted.phase === 'READING' ? 'reading' : 'evaluation'} was {interrupted.status.toLowerCase()}. Finished work is kept.
      <button className="link-btn" onClick={() => run(() => api.resume(interrupted.id, role))}>Resume</button></div>}

    {reading && <ReadingProgress done={(readJob?.units ?? []).filter(u => u.stage === 'read' && u.status === 'DONE').length} total={(readJob?.units ?? []).filter(u => u.stage === 'read').length} stage={readJob?.stage} />}
    {gate && <ExtractionCheck app={app} quals={quals} qual={qual} role={role} onChanged={reload} />}
    {demoIdle && <div className="pad"><p className="muted">The demo fixtures have not been evaluated yet.</p><button className="primary-button" disabled={busy} onClick={() => run(() => api.evaluate(appId, role))}><Play size={16} /> Run AI evaluation</button></div>}
    {evaluating && <p className="muted pad"><Loader2 size={14} className="spin" /> Evaluating: {results.length} course{results.length === 1 ? '' : 's'} done so far. Results appear as they finish.</p>}

    {!reading && !gate && !demoIdle && <div className="pop-body">
      <div className="selection"><strong>{built.relevant.length} of {built.counts.total} courses relevant</strong>
        <button className="link-btn" onClick={() => setNotEvalOpen(true)}>Adjust</button>
        <span className="muted push">{built.counts.mapped} mapped · {built.counts.needsYou} need you · {built.counts.waiting} waiting on a document</span></div>

      {built.relevant.length > 0 && <table className="ctable"><thead><tr><th>Source course</th><th>Grade</th><th>AUD equivalent</th><th>Credits</th><th>Status</th></tr></thead>
        <tbody>{built.relevant.map(i => <tr key={i.key} className={`${evidenceKey === i.key ? 'sel' : ''} ${i.result ? 'click' : ''}`} onClick={() => i.result && setEvidenceKey(i.result.course.code)}>
          <td><strong>{i.title}</strong></td><td>{i.grade || '—'}</td>
          <td>{equivalent(i)}</td><td>{i.result?.match.status === 'POTENTIAL_MATCH' ? i.result.match.target?.credits ?? '—' : '—'}</td>
          <td className="status-cell"><RowPill s={i.status} />{i.objections > 0 && <span className="obj-badge" title="The reviewer objects. Open the row for the full text.">{i.objections} objection{i.objections === 1 ? '' : 's'}</span>}{i.decided === 'ACCEPTED' && <span className="ok-mark">accepted</span>}</td></tr>)}</tbody></table>}

      {built.needsYou.length > 0 && <section className="needs"><h4>Needs your input <span className="count">{built.needsYou.length}</span></h4>
        <div className="needs-cards">{built.needsYou.map(i => <div className="need-card" key={i.key}><strong>{i.title}</strong><p>{i.finding ?? i.reason}</p>
          {i.action && <button className="secondary-button" disabled={busy} onClick={() => act(i)}>{i.action.label}</button>}</div>)}</div></section>}

      {built.notEvaluated.length > 0 && <details className="noteval" open={notEvalOpen} onToggle={e => setNotEvalOpen((e.target as HTMLDetailsElement).open)}>
        <summary>{notEvalOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />} Not evaluated ({built.notEvaluated.length})</summary>
        <ul>{built.notEvaluated.map(i => <li key={i.key + i.rowId}><span><strong>{i.title}</strong> <span className="muted">· {i.reason}</span></span>
          {i.canInclude ? <button className="link-btn" onClick={() => setInclude(i.rowId, true)}>Include</button> : <span className="muted">cannot be mapped</span>}</li>)}</ul></details>}
    </div>}

    {!reading && !gate && <footer className="pop-foot">
      <span className="muted"><ShieldCheck size={13} /> Suggestions only. Nothing is final until you decide.</span>
      <div className="pop-foot-actions">
        <button className="secondary-button" onClick={exportCsv} disabled={!built.relevant.length}><Download size={15} /> Export spreadsheet</button>
        <button className="primary-button" onClick={approveAll} disabled={busy || !approvable.length || role !== 'admissions'} title={role !== 'admissions' ? 'Only an admissions officer can approve' : approvable.length ? '' : 'No mapped course is waiting for approval'}>Approve{approvable.length ? ` (${approvable.length})` : ''}</button>
        <button className="secondary-button" onClick={() => { setActivity(!activity); setEvidenceKey(null); }}>Activity</button>
      </div></footer>}
  </Shell>;
}

function equivalent(i: Item): string {
  const m = i.result?.match;
  if (!m) return '—';
  if (m.target && m.status === 'POTENTIAL_MATCH') return `${m.target.code} · ${m.target.title}`;
  if (m.status === 'POTENTIAL_GENERAL_ELECTIVE') return 'General elective (AI flag)';
  if (i.status === 'Waiting on document') return 'Waiting on the syllabus';
  return '—';
}

function ReadingProgress({ done, total, stage }: { done: number; total: number; stage?: string }) {
  return <div className="pad"><p><Loader2 size={15} className="spin" /> Reading the transcript{stage ? ` (${stage})` : ''}…</p>
    <div className="bar"><div style={{ width: `${total ? Math.round((done / total) * 100) : 5}%` }} /></div><p className="muted">{done} of {total || '…'} page blocks read. Nothing is evaluated until you confirm the extraction.</p></div>;
}

function Shell({ children, onClose, side, wide }: { children: React.ReactNode; onClose: () => void; side?: React.ReactNode; wide?: boolean }) {
  return <div className="pop-backdrop" onClick={onClose}><div className={`pop ${wide ? 'pop-wide' : ''} ${side ? 'pop-with-side' : ''}`} onClick={e => e.stopPropagation()} role="dialog" aria-label="Student">
    <div className="pop-main">{children}</div>{side}</div></div>;
}
