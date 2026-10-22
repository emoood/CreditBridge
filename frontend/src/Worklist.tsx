import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertCircle, History, Loader2, UploadCloud } from 'lucide-react';
import { api, type AppInfo, type Recording } from './api';
import { APP_ORDER, appStatus, build, curriculumOf, type AppStatus, type Built } from './model';
import { StudentPopup } from './StudentPopup';
import { AppPill } from './ui';
import { loadApp } from './useApp';

type Entry = { app: AppInfo; status: AppStatus; built: Built; curriculum: string };

function includeSet(id: string): Set<string> {
  try { return new Set<string>(JSON.parse(localStorage.getItem(`cb-include-${id}`) ?? '[]')); } catch { return new Set<string>(); }
}

/** Home: one row per application, what needs the officer first. */
export default function Worklist() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [role, setRole] = useState('admissions');
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [qual, setQual] = useState('');
  const [quals, setQuals] = useState<string[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [replayOpen, setReplayOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.applications();
      const loaded = await Promise.all(list.slice(0, 30).map(async a => {
        const d = await loadApp(a.id);
        const evaluating = ['CONFIRMED', 'EVALUATION_IN_PROGRESS'].includes(d.app.status) || d.evalJob?.status === 'RUNNING' || d.evalJob?.status === 'QUEUED';
        const built = build(d.app, d.results, d.pending, includeSet(a.id), evaluating);
        return { app: { ...d.app, replay: a.replay ?? d.app.replay }, status: appStatus(d.app, built, [d.readJob, d.evalJob]), built, curriculum: curriculumOf(d.app, d.results) } as Entry;
      }));
      setEntries(loaded.sort((a, b) => APP_ORDER[a.status] - APP_ORDER[b.status] || b.app.created_at.localeCompare(a.app.created_at)));
      setError(null);
    } catch (e) { setError(`Cannot reach the backend (${(e as Error).message}). Start it with: scripts/start.sh`); }
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  const anyBusy = entries.some(e => e.status === 'Processing');
  useEffect(() => { const t = setInterval(refresh, anyBusy ? 2500 : 10000); return () => clearInterval(t); }, [refresh, anyBusy]);
  useEffect(() => { api.qualifications().then(q => setQuals(q.qualifications)).catch(() => undefined); api.recordings().then(setRecordings).catch(() => undefined); }, [entries.length]);

  async function upload(f: File | undefined) {
    if (!f) return;
    setUploading(true); setError(null);
    try { const a = await api.upload(f, '', qual); setOpen(a.id); await refresh(); } catch (e) { setError((e as Error).message); }
    finally { setUploading(false); if (fileRef.current) fileRef.current.value = ''; }
  }

  return <div className="worklist">
    {error && <div className="eval-error"><AlertCircle size={17} /> {error}</div>}
    <section className="panel wl-head">
      <p className="muted wl-hint">What needs you comes first. Click a row to open the student.</p>
      <div className="wl-actions">
        <label className="role-pick">Curriculum <select value={qual} onChange={e => setQual(e.target.value)}><option value="">Detect from transcript</option>{quals.map(q => <option key={q}>{q}</option>)}</select></label>
        <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg" hidden onChange={e => upload(e.target.files?.[0])} />
        <button className="primary-button wl-btn" onClick={() => fileRef.current?.click()} disabled={uploading}>{uploading ? <Loader2 size={16} className="spin" /> : <UploadCloud size={16} />} Upload transcript</button>
        <button className="secondary-button" onClick={() => setReplayOpen(!replayOpen)} disabled={!recordings.length} title={recordings.length ? '' : 'No recorded run saved yet'}><History size={16} /> Replay a recorded run</button>
        <label className="role-pick">Acting as <select value={role} onChange={e => setRole(e.target.value)}><option value="admissions">Admissions officer</option><option value="registrar">Registrar (cannot decide)</option></select></label>
      </div>
    </section>
    {replayOpen && <ReplayPicker recordings={recordings} role={role} onLoaded={a => { setReplayOpen(false); setOpen(a.id); refresh(); }} />}

    <section className="panel wl-table-wrap">
      <table className="wl-table"><thead><tr><th>Student</th><th>Curriculum</th><th>Status</th><th className="num">Mapped</th><th className="num">Needs you</th><th className="num">Not evaluated</th></tr></thead>
        <tbody>{entries.map(e => <tr key={e.app.id} onClick={() => setOpen(e.app.id)} tabIndex={0} onKeyDown={ev => ev.key === 'Enter' && setOpen(e.app.id)}>
          <td><strong>{e.app.student}</strong><small>{e.app.id}{e.app.replay ? ' · replay' : ''}</small></td>
          <td>{e.curriculum}</td>
          <td><AppPill s={e.status} /></td>
          <td className="num">{e.built.counts.mapped}</td>
          <td className={`num ${e.built.counts.needsYou ? 'hot' : ''}`}>{e.built.counts.needsYou}</td>
          <td className="num">{e.built.counts.notEvaluated}</td></tr>)}</tbody></table>
      {entries.length === 0 && !error && <p className="muted pad">No transcripts yet. Upload one, or replay a recorded run.</p>}
    </section>

    {open && <StudentPopup appId={open} role={role} quals={quals} qual={qual} onClose={() => { setOpen(null); refresh(); }} onChanged={refresh} />}
  </div>;
}

function ReplayPicker({ recordings, role, onLoaded }: { recordings: Recording[]; role: string; onLoaded: (a: AppInfo) => void }) {
  const [name, setName] = useState(recordings[0]?.name ?? '');
  const [live, setLive] = useState<string[]>([]);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const rec = recordings.find(r => r.name === name);
  const toggle = (id: string) => setLive(l => l.includes(id) ? l.filter(x => x !== id) : l.length < 2 ? [...l, id] : l);
  async function go() { setBusy(true); setErr(null); try { onLoaded(await api.replay(name, live, role)); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }
  return <section className="panel replay-picker">
    <div className="section-overline">REPLAY A RECORDED RUN</div>
    <p className="muted">Loads a saved real run without calling the model. You can still run up to two courses live.</p>
    <select value={name} onChange={e => { setName(e.target.value); setLive([]); }}>{recordings.map(r => <option key={r.name} value={r.name}>{r.label}</option>)}</select>
    {rec && <div className="live-pick"><small>Run live with the model (optional, max 2):</small>{rec.subjects.map(s => <label key={s.row_id}><input type="checkbox" checked={live.includes(s.row_id)} onChange={() => toggle(s.row_id)} /> {s.title} <span className="muted">({s.duration_s}s when recorded)</span></label>)}</div>}
    {err && <div className="eval-error"><AlertCircle size={16} /> {err}</div>}
    <button className="primary-button wl-btn" onClick={go} disabled={busy || !name}>{busy ? <Loader2 size={16} className="spin" /> : <History size={16} />} Load replay</button>
  </section>;
}
