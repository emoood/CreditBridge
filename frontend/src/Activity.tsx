import { useEffect, useState } from 'react';
import { X } from 'lucide-react';
import { api, type Activity as Act } from './api';
import type { AppData } from './useApp';

/** The agent trace: model calls, seconds, cost and what each agent looked at. Closed by default; the replay label appears once, here. */
export function Activity({ data, onClose }: { data: AppData; onClose: () => void }) {
  const { app, results, readJob, evalJob } = data;
  const [act, setAct] = useState<Act | null>(null);
  useEffect(() => { const f = () => api.activity(app.id).then(setAct).catch(() => undefined); f(); const t = setInterval(f, 1200); return () => clearInterval(t); }, [app.id, results.length, app.status]); // streams while work is happening
  const calls = results.reduce((n, r) => n + (r.replay ? r.replay.recorded_llm_calls ?? 0 : r.llm_calls), 0);
  const secs = results.reduce((n, r) => n + (r.replay ? r.replay.recorded_duration_s ?? 0 : r.duration_s), 0);
  const live = results.filter(r => !r.replay).length;
  const models = [...new Set(results.map(r => r.model).filter(Boolean))];
  const running = [readJob, evalJob].find(j => j && j.status === 'RUNNING');
  return <aside className="activity-drawer" aria-label="Activity">
    <div className="evidence-head"><div><small>ACTIVITY</small><h3>Agent trace</h3></div><button className="icon-button" onClick={onClose} aria-label="Close activity"><X size={18} /></button></div>
    <div className="evidence-body">
      {app.replay && <p className="muted">Figures are those of the recorded run{live ? `; ${live} course(s) ran live` : ''}.</p>}
      <div className="act-stats">
        <div><small>MODEL CALLS</small><strong>{act ? act.usage.calls : calls}</strong>{act && (act.usage.recorded_calls ?? 0) > 0 && <span>{act.usage.recorded_calls} from the recording · {act.usage.live_calls} live</span>}</div>
        <div><small>TOOL CALLS</small><strong>{act?.usage.tool_calls ?? 0}</strong><span>{Math.round(act?.usage.tool_seconds ?? 0)} s · every agent step is in the ledger</span></div>
        <div><small>MODEL TIME</small><strong>{Math.round(act ? act.usage.seconds : secs)} s</strong></div>
        <div><small>COST</small><strong>${(act?.usage.cost_usd ?? 0).toFixed(2)}</strong><span>{act ? `${act.usage.prompt_tokens + act.usage.output_tokens} tokens · local model` : 'local model'}</span></div>
        <div><small>MODEL</small><strong className="small-strong">{(act?.usage.models.length ? act.usage.models : models).join(', ') || '—'}</strong></div>
      </div>
      {readJob && <p className="muted">Reading: {readJob.status.toLowerCase()} · {readJob.timings?.total_secs ?? '—'} s · {(readJob.units ?? []).filter(u => u.stage === 'read').length} page blocks · {readJob.model}</p>}
      {evalJob && <p className="muted">Evaluation: {evalJob.status.toLowerCase()} · {evalJob.timings?.total_secs ?? '—'} s · {evalJob.route} · {evalJob.model}</p>}
      {running && running.activity.length > 0 && <><h4>Happening now</h4><ul className="act-live">{[...running.activity].reverse().slice(0, 8).map((a, i) => <li key={i}><span className="muted">{a.course ?? ''}</span> {a.kind === 'tool_call' ? <>{a.role} used <strong>{a.tool}</strong> {a.ok ? '✓' : '✗'}</> : a.kind === 'stage' ? <>stage <strong>{a.stage}</strong></> : a.kind === 'llm_call' ? <>model call <span className="muted">{a.duration_s}s</span></> : 'error'}</li>)}</ul></>}
      {act && act.trace.length > 0 && <><h4>Case agent actions</h4><ul className="trace">{[...act.trace].reverse().slice(0, 60).map((e, i) => <li key={i} className={e.ok ? '' : 'bad'}>
        <span className="muted">{new Date(e.at).toLocaleTimeString()}</span> {e.recorded && <em className="rec-tag">recorded</em>} <strong>{e.actor === 'code' ? 'code' : 'agent'}</strong> {e.name.replace(/_/g, ' ')}{e.ok ? '' : ' (refused)'} <span className="muted">{e.detail.slice(0, 90)}</span></li>)}</ul></>}
      <h4>Per course</h4>
      <table className="act-table"><thead><tr><th>Course</th><th>Calls</th><th>Seconds</th><th>Looked at</th></tr></thead>
        <tbody>{results.map(r => <tr key={r.course.code}><td>{r.course.title}</td><td>{r.replay ? r.replay.recorded_llm_calls : r.llm_calls}</td><td>{Math.round(r.replay ? r.replay.recorded_duration_s : r.duration_s)}</td>
          <td className="muted">{r.case_file.queries.length ? `${r.case_file.queries.length} search${r.case_file.queries.length === 1 ? '' : 'es'}` : ''}{Object.values(r.case_file.targets_read).flat().length ? ` · read ${[...new Set(Object.values(r.case_file.targets_read).flat())].join(', ')}` : ''}{r.review ? ` · reviewer: ${r.review.status.replace(/_/g, ' ').toLowerCase()}` : ''}</td></tr>)}</tbody></table>
      {results.length === 0 && <p className="muted">No evaluation has run yet.</p>}
    </div>
  </aside>;
}
