import { useEffect, useState } from 'react';
import { AlertCircle, Check, Loader2, Plus, X } from 'lucide-react';
import { api, type AppInfo, type Row, type Subject } from './api';

/** Step before the worklist view: the scan beside the table, only low-confidence cells highlighted, one Confirm button. */
export function ExtractionCheck({ app, quals, qual, role, onChanged }: { app: AppInfo; quals: string[]; qual: string; role: string; onChanged: () => void }) {
  const [page, setPage] = useState(1);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [flagsOpen, setFlagsOpen] = useState(false);
  const rows = app.rows ?? [];
  const ex = app.extraction;
  const wrap = async (f: () => Promise<unknown>) => { setErr(null); try { await f(); await onChanged(); } catch (e) { setErr((e as Error).message); } };
  const lowRows = rows.filter(r => r.uncertain_fields.length || r.flags?.length || (r.included && !r.subject)).length;
  const included = rows.filter(r => r.included).length;
  async function confirm() { setBusy(true); await wrap(() => api.confirm(app.id, role)); setBusy(false); }
  return <div className="xcheck">
    <div className="xcheck-top">
      <div><h3>Check the extraction</h3>
        <p className="muted">{included} courses read. {lowRows ? `${lowRows} row${lowRows === 1 ? '' : 's'} with a highlighted cell: check against the scan.` : 'Nothing looks uncertain.'} Edit any cell by clicking it.</p></div>
      <button className="primary-button" onClick={confirm} disabled={busy || !included}>{busy ? <Loader2 size={16} className="spin" /> : <Check size={16} />} Confirm extraction</button>
    </div>
    {(ex?.failed_blocks ?? 0) > 0 && <div className="eval-warn"><AlertCircle size={15} /> A part of the page with no course on it (a footer, legend or signature block) could not be read and was skipped. This is not a missing course. Compare the table with the scan; if a course really is missing, add it by hand.</div>}
    {ex?.flags && ex.flags.length > 0 && <div className="eval-warn"><AlertCircle size={15} /> {ex.flags.length} cross-check note{ex.flags.length === 1 ? '' : 's'} (marks, points, credits or GPA do not add up).
      <button className="link-btn" onClick={() => setFlagsOpen(!flagsOpen)}>{flagsOpen ? 'Hide' : 'Show'}</button></div>}
    {flagsOpen && ex?.flags && <ul className="flag-list">{ex.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>}
    {app.curriculum && !app.curriculum.available && <div className="eval-error"><AlertCircle size={15} /> The curriculum database is unreachable ({app.curriculum.error}). Subjects cannot be linked.</div>}
    <div className="xcheck-grid">
      <div className="scan"><div className="scan-tabs">{Array.from({ length: app.document?.page_count ?? 1 }, (_, i) => <button key={i} className={page === i + 1 ? 'on' : ''} onClick={() => setPage(i + 1)}>Page {i + 1}</button>)}</div>
        {app.document ? <img src={api.previewUrl(app.id, page)} alt={`Transcript page ${page}`} /> : <p className="muted">No scan is stored for this application.</p>}</div>
      <div className="vtable-wrap">
        <table className="xtable"><thead><tr><th /><th>Course (as written)</th><th>Mark</th><th>Grade</th><th>Credits</th><th>Term</th><th>Curriculum subject</th><th /></tr></thead>
          <tbody>{rows.map(r => <XRow key={r.id} appId={app.id} r={r} quals={quals} qual={qual} wrap={wrap} />)}</tbody></table>
        {rows.length === 0 && <div className="empty-state">No courses were recognised. Add rows manually.</div>}
        <div className="verify-actions">
          <button className="link-btn" onClick={() => wrap(() => api.addRow(app.id))}><Plus size={13} /> Add a course</button>
          <button className="link-btn" onClick={() => wrap(() => api.link(app.id, qual || null, true))}>Re-run auto-link</button>
          <span className="muted push">Edits are saved when you leave a cell and are logged.</span></div>
        {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}
      </div>
    </div>
  </div>;
}

type Field = 'title' | 'credits' | 'grade' | 'term';
function XRow({ appId, r, quals, qual, wrap }: { appId: string; r: Row; quals: string[]; qual: string; wrap: (f: () => Promise<unknown>) => Promise<void> }) {
  const [picker, setPicker] = useState(false);
  const flagged = (r.flags?.length ?? 0) > 0;
  const cell = (k: Field, low: boolean, w?: number) =>
    <input defaultValue={r[k]} className={`cell ${low ? 'low' : ''}`} style={w ? { width: w } : undefined} aria-label={k}
      onBlur={e => { if (e.target.value !== r[k]) wrap(() => api.patchRow(appId, r.id, { [k]: e.target.value })); }} />;
  return <>
    <tr className={r.included ? '' : 'off'}>
      <td><input type="checkbox" checked={r.included} aria-label="Include" onChange={e => wrap(() => api.patchRow(appId, r.id, { included: e.target.checked }))} /></td>
      <td>{cell('title', r.uncertain_fields.includes('title'))}{flagged && <small className="rowflag" title={r.flags?.join('\n')}>Check: {r.flags?.[0]}</small>}</td>
      <td className={`num ${flagged ? 'lowtext' : ''}`}>{r.mark ?? ''}</td>
      <td>{cell('grade', r.uncertain_fields.includes('grade') || flagged, 56)}</td>
      <td>{cell('credits', r.uncertain_fields.includes('credits'), 56)}</td>
      <td>{cell('term', r.uncertain_fields.includes('term'), 90)}</td>
      <td className={r.subject ? 'subj' : 'subj lowcell'}>{r.subject ? <span className="subj-chosen"><strong>{r.subject.name}</strong><small>{r.subject.qualification}</small></span> : <span className="unrec">Not in the curriculum</span>}
        <button className="link-btn" onClick={() => setPicker(!picker)}>{r.subject ? 'Change' : 'Choose'}</button></td>
      <td><button className="icon-button" aria-label="Remove row" onClick={() => wrap(() => api.deleteRow(appId, r.id))}><X size={14} /></button></td></tr>
    {picker && <tr><td colSpan={8}><Picker r={r} quals={quals} qual={qual} onPick={id => { setPicker(false); wrap(() => api.patchRow(appId, r.id, { subject_id: id })); }} /></td></tr>}
  </>;
}

function Picker({ r, quals, qual, onPick }: { r: Row; quals: string[]; qual: string; onPick: (id: string) => void }) {
  const [q, setQ] = useState(''); const [filter, setFilter] = useState(qual); const [res, setRes] = useState<Subject[]>(r.candidates);
  useEffect(() => { if (!q && !filter) { setRes(r.candidates); return; } const t = setTimeout(() => api.subjects(q, filter).then(setRes).catch(() => setRes([])), 200); return () => clearTimeout(t); }, [q, filter, r.candidates]);
  return <div className="picker"><div className="picker-controls"><input autoFocus placeholder="Search curriculum subjects…" value={q} onChange={e => setQ(e.target.value)} />
    <select value={filter} onChange={e => setFilter(e.target.value)}><option value="">All curricula</option>{quals.map(x => <option key={x}>{x}</option>)}</select>
    {r.subject && <button className="link-btn" onClick={() => onPick('')}>Unlink</button>}</div>
    <div className="picker-list">{res.length === 0 && <span className="muted">No matches.</span>}{res.map(s => <button key={s.id} onClick={() => onPick(s.id)}><strong>{s.name}</strong><small>{s.qualification} · {s.code}</small></button>)}</div></div>;
}
