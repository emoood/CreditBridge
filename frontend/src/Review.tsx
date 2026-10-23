import { useState } from 'react';
import { AlertCircle, Check, ChevronDown, ChevronRight, ClipboardCopy, Download, Paperclip, ShieldCheck, X } from 'lucide-react';
import { api } from './api';
import { CLASS_LABEL, groups, isOpen, isReady, needOf, RESOLUTION_LABEL, rowLabel, type Built, type Item } from './model';

type Role = string;

/** The review overview: who, how far along, then one grouped list (core courses, general electives). Everything else is one click away. */
export function ReviewBody({ built, summary, role, appId, busy, program, approved, canApprove, selectedKey, onOpen, onRun, onCopy, onCsv, onActivity, children }: {
  built: Built; summary: string; role: Role; appId: string; busy: boolean; program: string; approved: boolean; canApprove: boolean;
  selectedKey: string | null; onOpen: (key: string) => void; onRun: (f: () => Promise<unknown>) => Promise<void>; onCopy: () => void; onCsv: () => void; onActivity: () => void; children?: React.ReactNode;
}) {
  const [naOpen, setNaOpen] = useState(false);
  const [neOpen, setNeOpen] = useState(false);
  const [allOpen, setAllOpen] = useState(false);
  const [filter, setFilter] = useState<'all' | 'open'>('all');
  const relevant = built.relevant;
  const open = relevant.filter(isOpen); const ready = relevant.filter(isReady);
  const waiting = open.filter(i => i.status === 'Waiting on document'); const others = open.filter(i => i.status !== 'Waiting on document');
  const toConfirm = built.counts.confirmable;
  const total = relevant.length;
  const pct = (n: number) => `${total ? (n / total) * 100 : 0}%`;
  const confirmThese = (items: Item[]) => onRun(async () => { for (const i of items) await api.rowAction(appId, i.rowId, role, { action: 'CONFIRM' }); });
  const gs = groups(built);
  return <div className="rv">
    <div className="rv-progress" aria-label="Progress">
      <p className="rv-count"><strong>{ready.length} of {total} courses ready</strong>{open.length > 0 && <span> · {open.length} open</span>}</p>
      <div className="rv-bar" role="img" aria-label={`${ready.length} ready, ${others.length} open, ${waiting.length} awaiting a document`}>
        <span className="seg ready" style={{ width: pct(ready.length) }} /><span className="seg needs" style={{ width: pct(others.length) }} /><span className="seg waiting" style={{ width: pct(waiting.length) }} /></div>
      <p className="rv-legend"><span className="dot ready" /> ready <span className="dot needs" /> open <span className="dot waiting" /> awaiting document</p>
      {summary && <p className="rv-summary">{summary}</p>}
    </div>

    {children}

    <div className="rv-filter" role="group" aria-label="Show">
      <button className={filter === 'all' ? 'on' : ''} aria-pressed={filter === 'all'} onClick={() => setFilter('all')}>All</button>
      <button className={filter === 'open' ? 'on' : ''} aria-pressed={filter === 'open'} onClick={() => setFilter('open')}>Open only</button></div>

    {gs.map(g => {
      const rows = filter === 'open' ? g.items.filter(isOpen) : g.items;
      if (filter === 'open' && rows.length === 0) return null;
      const confirmLabel = g.id === 'core' ? 'core' : 'electives';
      return <section className="rv-group" key={g.id} aria-labelledby={`rv-g-${g.id}`}>
        <header className="rv-group-head"><h3 id={`rv-g-${g.id}`}>{g.title} ({g.items.length})</h3>
          <span className="muted">{g.ready.length} ready · {g.open} open · {g.confirmed} confirmed</span><span className="push" />
          {g.id !== 'other' && <button className="secondary-button" disabled={busy || role !== 'admissions' || g.ready.length === 0} onClick={() => confirmThese(g.ready)}>Confirm ready {confirmLabel} ({g.ready.length})</button>}</header>
        {rows.length === 0 ? <p className="muted rv-empty">No courses here.</p> : <div className="rv-scroll"><table className="ctable rv-table"><thead><tr><th>Course</th><th>Grade</th><th>AUD equivalent</th><th className="num">Credits</th><th>Status</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>{rows.map(i => <GroupRow key={i.key + i.rowId} i={i} program={program} active={selectedKey === i.key} busy={busy} role={role} appId={appId} onOpen={() => onOpen(i.key)} onRun={onRun} />)}</tbody></table></div>}
      </section>;
    })}
    {total > 0 && open.length === 0 && !approved && toConfirm === 0 && <p className="rv-allclear"><Check size={16} /> Every course is confirmed.</p>}

    {built.notApplicable.length > 0 && <section className="rv-muted"><button className="rv-line" onClick={() => setNaOpen(!naOpen)} aria-expanded={naOpen}>{naOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      {built.notApplicable.length} course{built.notApplicable.length === 1 ? '' : 's'} not applicable to {program || 'this program'}</button>
      {naOpen && <ul>{built.notApplicable.map(i => <li key={i.key}><span><strong>{i.title}</strong> <span className="muted">· {i.state?.reason}</span></span>
        {role === 'admissions' ? <button className="secondary-button" disabled={busy} onClick={() => onRun(() => api.include(appId, i.rowId, role))}>Include anyway</button> : <span className="muted">officer only</span>}</li>)}</ul>}</section>}

    {built.notEvaluated.length > 0 && <section className="rv-muted"><button className="rv-line" onClick={() => setNeOpen(!neOpen)} aria-expanded={neOpen}>{neOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      {built.notEvaluated.length} not evaluated (school-specific or outside the accepted curriculum)</button>
      {neOpen && <ul>{built.notEvaluated.map(i => <li key={i.key + i.rowId}><span><strong>{i.title}</strong> <span className="muted">· {i.state?.kind === 'school_specific' ? 'School-specific: not mapped' : i.reason}</span></span>
        {i.canInclude ? <button className="secondary-button" disabled={busy} onClick={() => onRun(() => api.include(appId, i.rowId, role))}>Include</button> : <span className="muted">cannot be mapped</span>}</li>)}</ul>}</section>}

    <p className="rv-links"><button className="link-btn" onClick={() => setAllOpen(!allOpen)}>{allOpen ? 'Hide' : 'Show'} all courses</button> · <button className="link-btn" onClick={onActivity}>See what the agents did</button></p>
    {allOpen && <div className="rv-scroll"><table className="ctable"><thead><tr><th>Source course</th><th>Grade</th><th>AUD equivalent</th><th>Group</th><th>Status</th></tr></thead>
      <tbody>{built.items.map(i => <tr key={i.key + i.rowId} className="click" onClick={() => i.state && i.status !== 'Not evaluated' && onOpen(i.key)}><td><strong>{i.title}</strong></td><td>{i.grade || '—'}</td><td>{i.audCode ?? '—'}</td>
        <td>{i.cls ? CLASS_LABEL[i.cls.value] : '—'}</td><td>{i.state?.kind === 'not_applicable' ? 'Not applicable' : i.status === 'Not evaluated' ? 'Not evaluated' : rowLabel(i)}</td></tr>)}</tbody></table></div>}
    <p className="muted placeholder-note">AUD codes and credits shown are placeholder mapping data for this demo, not AUD&apos;s real equivalencies.</p>

    <footer className="rv-foot">
      <div className="rv-foot-main">
        <button className="primary-button" onClick={() => onRun(() => api.confirmMapped(appId, role))} disabled={busy || !toConfirm || role !== 'admissions'}>Confirm all ready{toConfirm ? ` (${toConfirm})` : ''}</button>
        <button className="primary-button rv-approve" onClick={() => onRun(() => api.approve(appId, role))} disabled={busy || !canApprove}>Approve case</button>
        {!approved && (open.length > 0 || toConfirm > 0) && <span className="rv-need">{open.length} open, {toConfirm} to confirm</span>}
        {approved && <span className="approved-tag"><Check size={13} /> Approved</span>}
        <span className="push" />
        <button className="secondary-button" onClick={onCopy} disabled={busy || !built.counts.resolved && !built.counts.mapped}><ClipboardCopy size={16} /> Copy for spreadsheet</button>
        <button className="secondary-button" onClick={onCsv} disabled={busy || !built.counts.resolved && !built.counts.mapped}><Download size={16} /> CSV</button>
      </div>
      <p className="muted rv-fine"><ShieldCheck size={13} /> Suggestions only. Nothing is final until you confirm.</p>
    </footer>
  </div>;
}

function GroupRow({ i, program, active, busy, role, appId, onOpen, onRun }: { i: Item; program: string; active: boolean; busy: boolean; role: Role; appId: string; onOpen: () => void; onRun: (f: () => Promise<unknown>) => Promise<void> }) {
  const label = rowLabel(i); const o = isOpen(i); const officer = role === 'admissions';
  const need = o ? needOf(i, program) : null;
  return <tr className={`rv-tr ${o ? 'open' : ''} ${active ? 'on' : ''}`}>
    <td><button className="link-btn rv-name" onClick={onOpen}><strong>{i.title}</strong></button></td>
    <td>{i.grade || '—'}</td><td>{i.audCode ?? '—'}</td><td className="num">{i.audCredits ?? '—'}</td>
    <td><span className={`rv-status s-${label.toLowerCase().replace(/ /g, '-')}`}>{label === 'Confirmed' && <Check size={14} />}{label}</span></td>
    <td className="rv-act">{need ? <button className="primary-button" disabled={busy || !officer} onClick={() => need.kind === 'reconfirm' ? onRun(() => api.rowAction(appId, i.rowId, role, { action: 'RECONFIRM' })) : onOpen()}>{need.primary}</button>
      : label === 'Ready' ? <button className="secondary-button" disabled={busy || !officer} onClick={() => onRun(() => api.rowAction(appId, i.rowId, role, { action: 'CONFIRM' }))}>Confirm</button> : null}</td>
  </tr>;
}

// ------------------------------------------------------------------------------------------------------------------ the row panel
export function RowPanel({ appId, item, role, program, onClose, onAdvance }: { appId: string; item: Item; role: Role; program: string; onClose: () => void; onAdvance: () => void }) {
  const s = item.state; const need = needOf(item, program); const open = isOpen(item);
  const [mode, setMode] = useState<'change' | 'reject' | null>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  if (!s) return null;
  const f = s.finding ?? item.result?.case?.finding ?? null;
  const act = async (body: { action: string; aud_code?: string; credits?: number; reason?: string }) => {
    setBusy(true); setErr(null);
    try { await api.rowAction(appId, item.rowId, role, body); setMode(null); onAdvance(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const doubts = s.doubts ?? [];
  const elective = item.cls?.value === 'General elective suggestion';
  const canAct = role === 'admissions';
  return <aside className="rv-panel" aria-label="Course">
    <header className="rv-panel-head"><div><small>COURSE</small><h3>{item.title}</h3>
      <p className="muted">Grade {item.grade || '—'} · {item.audCode ? `${item.audCode}${item.state?.mapping ? '' : ''}` : 'no AUD course'}{item.audCredits ? ` · ${item.audCredits} credits` : ''}</p></div>
      <button className="icon-button" onClick={onClose} aria-label="Close"><X size={20} /></button></header>

    <section className="rv-block"><h4>Finding</h4>
      <p>{open ? need.sentence : item.resolved && item.action ? `You ${RESOLUTION_LABEL[item.action.action].toLowerCase()} this course${item.action.chosen.aud_code ? ` as ${item.action.chosen.aud_code}` : ''}.` : `Mapped to ${item.audCode ?? 'an AUD course'} by the placeholder mapping table${item.cls ? `; ${item.cls.value === 'Core course' ? `a core course for ${program}` : 'a potential general elective'}` : ''}.`}</p>
      {doubts.map((d, i) => <p key={i} className="rv-doubt"><AlertCircle size={14} /> <strong>{d.kind.replace(/_/g, ' ')}.</strong> {d.reason} <span className="muted">{d.verified ? `Checked by code: ${d.evidence}.` : 'Not verified by code.'}</span></p>)}</section>

    <section className="rv-block"><h4>Decision</h4>
      {!canAct && <p className="muted">Only an admissions officer can decide.</p>}
      {canAct && !mode && <YourCall item={item} elective={elective} busy={busy} doubts={doubts.length > 0} need={need.kind} f={f} appId={appId} role={role} act={act} setMode={setMode} onAdvance={onAdvance} setErr={setErr} setBusy={setBusy} />}
      {canAct && mode === 'change' && <ChangeForm program={program} busy={busy} onCancel={() => setMode(null)} onSave={(code, credits, reason) => act({ action: 'CHANGE', aud_code: code || undefined, credits, reason })} />}
      {canAct && mode === 'reject' && <RejectForm busy={busy} onCancel={() => setMode(null)} onSave={reason => act({ action: 'REJECT', reason })} />}
      {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}</section>

    {canAct && ['Conditional', 'No mapping', 'Waiting on document'].includes(item.status) && !item.resolved && <NoteForAgent appId={appId} item={item} role={role} onAdvance={onAdvance} />}

    <details className="rv-block rv-evidence"><summary>Evidence</summary>
      {s.mapping && <p><strong>Mapping.</strong> {s.mapping.aud_code}, version {s.mapping.version} ({s.mapping.status.toLowerCase()}){s.mapping.programs ? `, for ${s.mapping.programs.join(', ')} only` : ''}{s.mapping.condition ? `. Condition: ${s.mapping.condition.type.replace(/_/g, ' ')} ${JSON.stringify(s.mapping.condition.value)}` : ''}.</p>}
      {s.condition_result && <p><strong>Condition check (code).</strong> {s.condition_result.reason}</p>}
      {item.cls && <p><strong>Classification (code).</strong> {item.cls.why}</p>}
      {(item.result?.rules ?? []).length > 0 && <><strong>Policy checks (code)</strong><ul>{(item.result?.rules ?? []).map(r => <li key={r.rule_id}>{r.status.replace(/_/g, ' ').toLowerCase()}: {r.reason}</li>)}</ul></>}
      {(item.result?.match.supported_outcomes ?? []).length > 0 && <><strong>Syllabus excerpts</strong>{(item.result?.match.supported_outcomes ?? []).map((o, i) => <blockquote key={i}>{o.subject.text}</blockquote>)}</>}
      {f && <><strong>Case agent&apos;s reasoning</strong><p>{f.headline}</p>{[...(f.gaps ?? []), ...(f.review_package ?? [])].length > 0 && <ul>{[...(f.gaps ?? []), ...(f.review_package ?? [])].map((x, i) => <li key={i}>{x}</li>)}</ul>}</>}
      {!s.mapping && !f && <p className="muted">Nothing else on file.</p>}</details>
  </aside>;
}

type Act = (b: { action: string; aud_code?: string; credits?: number; reason?: string }) => Promise<void>;

function YourCall({ item, elective, busy, doubts, need, f, appId, role, act, setMode, onAdvance, setErr, setBusy }: {
  item: Item; elective: boolean; busy: boolean; doubts: boolean; need: string; f: Item['state'] extends infer S ? (S extends { finding: infer F } ? F : never) : never; appId: string; role: Role; act: Act;
  setMode: (m: 'change' | 'reject') => void; onAdvance: () => void; setErr: (e: string | null) => void; setBusy: (b: boolean) => void;
}) {
  const s = item.state!;
  const row = (primary: React.ReactNode, extra?: React.ReactNode) => <div className="rv-actions">{primary}{extra}<button className="secondary-button" onClick={() => setMode('change')}>Change course</button><button className="secondary-button" onClick={() => setMode('reject')}>Reject</button></div>;
  if (item.resolved && item.action) return <><p className="muted">{RESOLUTION_LABEL[item.action.action]} by {item.action.actor}. You can still change it.</p>{row(null)}</>;
  if (s.status === 'Mapped') {
    const label = doubts ? 'Keep as is' : elective ? 'Confirm as elective' : item.cls?.value === 'Not applicable' ? 'Confirm anyway' : 'Confirm';
    return <>{doubts && <p className="muted">Keep it as suggested, or use one of the other options.</p>}
      {row(<button className="primary-button" disabled={busy} onClick={() => act({ action: 'CONFIRM' })}>{label}</button>, null)}</>;
  }
  if (s.kind === 'stale') return <div className="rv-actions"><button className="primary-button" disabled={busy} onClick={() => act({ action: 'RECONFIRM' })}>Re-confirm against catalog</button>
    <button className="secondary-button" disabled={busy} onClick={() => act({ action: 'REJECT', reason: 'Stale mapping removed by the officer' })}>Remove mapping</button><button className="secondary-button" onClick={() => setMode('change')}>Change course</button></div>;
  if (s.status === 'Waiting on document') return <WaitingForm item={item} appId={appId} role={role} onAdvance={onAdvance} setMode={setMode} setErr={setErr} setBusy={setBusy} busy={busy} />;
  if (s.status === 'Conditional' && (s.kind === 'condition_missing' || s.kind === 'condition_unmet') && need !== 'pick') return <ScoreForm item={item} appId={appId} role={role} onAdvance={onAdvance} setMode={setMode} setErr={setErr} setBusy={setBusy} busy={busy} />;
  if (s.status === 'No mapping' || s.kind === 'condition_unmet' || s.kind === 'rule_blocked') return <Candidates item={item} f={f} busy={busy} act={act} setMode={setMode} />;
  return row(null);
}

function ScoreForm({ item, appId, role, onAdvance, setMode, setErr, setBusy, busy }: { item: Item; appId: string; role: Role; onAdvance: () => void; setMode: (m: 'change' | 'reject') => void; setErr: (e: string | null) => void; setBusy: (b: boolean) => void; busy: boolean }) {
  const missing = item.state?.condition_result?.missing ?? [];
  const [vals, setVals] = useState<Record<string, string>>({}); const [file, setFile] = useState<File | null>(null);
  const fields = missing.length ? missing : [{ field: 'exam_score', evidence: 'AP exam score' }];
  async function send() {
    setBusy(true); setErr(null);
    try { const facts: Record<string, unknown> = {}; for (const [k, v] of Object.entries(vals)) if (v.trim()) facts[k] = Number.isNaN(Number(v)) ? v.trim() : Number(v);
      await api.evidence(appId, item.rowId, role, facts, file, ''); onAdvance(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="rv-form">
    {fields.map(m => <label key={m.field}><span>{m.field === 'exam_score' ? 'AP exam score' : m.field.replace(/_/g, ' ')}</span>
      <input inputMode="decimal" autoFocus value={vals[m.field] ?? ''} onChange={e => setVals({ ...vals, [m.field]: e.target.value })} placeholder={m.field === 'exam_score' ? 'for example 4' : m.evidence} /></label>)}
    <label className="file-pick"><Paperclip size={14} /> {file ? file.name : 'Attach the score report (optional)'}<input type="file" hidden onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
    <div className="rv-actions"><button className="primary-button" disabled={busy || !Object.values(vals).some(v => v.trim())} onClick={send}>Hand back to agent</button>
      <button className="secondary-button" onClick={() => setMode('change')}>Change course</button><button className="secondary-button" onClick={() => setMode('reject')}>Reject</button></div></div>;
}

function WaitingForm({ item, appId, role, onAdvance, setMode, setErr, setBusy, busy }: { item: Item; appId: string; role: Role; onAdvance: () => void; setMode: (m: 'change' | 'reject') => void; setErr: (e: string | null) => void; setBusy: (b: boolean) => void; busy: boolean }) {
  const s = item.state!; const req = (s.requests ?? []).find(q => q.status !== 'RECEIVED'); const missing = s.condition_result?.missing ?? [];
  const [preview, setPreview] = useState(false); const [file, setFile] = useState<File | null>(null); const [note, setNote] = useState(''); const [vals, setVals] = useState<Record<string, string>>({});
  async function send() {
    setBusy(true); setErr(null);
    try { const facts: Record<string, unknown> = {}; for (const [k, v] of Object.entries(vals)) if (v.trim()) facts[k] = Number.isNaN(Number(v)) ? v.trim() : Number(v);
      await api.evidence(appId, item.rowId, role, facts, file, note); onAdvance(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="rv-form">
    <p><strong>Missing:</strong> {missing.length ? missing.map(m => `${m.field.replace(/_/g, ' ')} (${m.evidence})`).join('; ') : req?.what ?? 'a document'}</p>
    {req && <div className="rv-draft"><p><strong>Drafted request</strong> to the {req.to}. <span className="muted">Nothing is sent until you approve.</span></p>
      <button className="link-btn" onClick={() => setPreview(!preview)}>{preview ? 'Hide preview' : 'Preview'}</button>{preview && <blockquote>{req.what}. {req.why}</blockquote>}</div>}
    {missing.filter(m => m.field !== 'syllabus').map(m => <label key={m.field}><span>{m.field.replace(/_/g, ' ')}</span><input value={vals[m.field] ?? ''} onChange={e => setVals({ ...vals, [m.field]: e.target.value })} placeholder={m.evidence} /></label>)}
    <label className="file-pick"><Paperclip size={14} /> {file ? file.name : 'Attach what arrived (optional)'}<input type="file" hidden onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
    <label><span>Note for the agent (optional)</span><textarea rows={2} value={note} onChange={e => setNote(e.target.value)} /></label>
    <div className="rv-actions"><button className="primary-button" disabled={busy || (!file && !note.trim() && !Object.values(vals).some(v => v.trim()))} onClick={send}>Hand back to agent</button>
      <button className="secondary-button" onClick={() => setMode('change')}>Change course</button><button className="secondary-button" onClick={() => setMode('reject')}>Reject</button></div></div>;
}

function Candidates({ item, f, busy, act, setMode }: { item: Item; f: { candidates?: { aud_code: string; why: string }[] } | null; busy: boolean; act: Act; setMode: (m: 'change' | 'reject') => void }) {
  const cands = f?.candidates ?? [];
  return <div>
    {cands.length > 0 ? <ul className="rv-cands">{cands.map(c => <li key={c.aud_code}><div><strong>{c.aud_code}</strong><p className="muted">{c.why || 'Proposed by the Case agent.'}</p></div>
      <button className="secondary-button" disabled={busy} onClick={() => act({ action: 'CHANGE', aud_code: c.aud_code, reason: 'Picked from the Case agent\'s candidates' })}>Pick</button></li>)}</ul> : <p className="muted">No candidate was proposed for {item.title}.</p>}
    <div className="rv-actions"><button className="primary-button" onClick={() => setMode('change')}>Pick a course</button><button className="secondary-button" onClick={() => setMode('reject')}>Reject</button></div></div>;
}

function ChangeForm({ program, busy, onCancel, onSave }: { program: string; busy: boolean; onCancel: () => void; onSave: (code: string, credits: number | undefined, reason: string) => void }) {
  const [q, setQ] = useState(''); const [hits, setHits] = useState<{ code: string; title: string; level: number }[]>([]); const [code, setCode] = useState(''); const [credits, setCredits] = useState(''); const [reason, setReason] = useState('');
  function search(v: string) { setQ(v); if (v.trim().length < 2) { setHits([]); return; } api.searchCatalog(v, program).then(setHits).catch(() => setHits([])); }
  return <div className="rv-form">
    <label><span>Search AUD courses</span><input autoFocus value={q} onChange={e => search(e.target.value)} placeholder="for example calculus" /></label>
    <div className="rv-hits">{hits.map(h => <button key={h.code} className={`hit ${code === h.code ? 'on' : ''}`} onClick={() => setCode(h.code)}><strong>{h.code}</strong> {h.title}</button>)}</div>
    <label><span>Credits (optional)</span><input inputMode="numeric" value={credits} onChange={e => setCredits(e.target.value.replace(/\D/g, ''))} /></label>
    <label><span>Reason (required)</span><input value={reason} onChange={e => setReason(e.target.value)} /></label>
    <div className="rv-actions"><button className="primary-button" disabled={busy || !reason.trim() || (!code && !credits)} onClick={() => onSave(code, credits ? Number(credits) : undefined, reason)}>Save change</button><button className="link-btn" onClick={onCancel}>Cancel</button></div></div>;
}

function RejectForm({ busy, onCancel, onSave }: { busy: boolean; onCancel: () => void; onSave: (reason: string) => void }) {
  const [reason, setReason] = useState('');
  return <div className="rv-form"><label><span>Why is there no credit for this course? (required)</span><input autoFocus value={reason} onChange={e => setReason(e.target.value)} /></label>
    <div className="rv-actions"><button className="primary-button" disabled={busy || !reason.trim()} onClick={() => onSave(reason)}>Reject</button><button className="link-btn" onClick={onCancel}>Cancel</button></div></div>;
}

function NoteForAgent({ appId, item, role, onAdvance }: { appId: string; item: Item; role: Role; onAdvance: () => void }) {
  const [note, setNote] = useState(''); const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  async function send() { setBusy(true); setErr(null); try { await api.evidence(appId, item.rowId, role, {}, null, note); setNote(''); onAdvance(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); } }
  return <section className="rv-block"><label><span>Note for the agent (optional)</span><textarea rows={2} value={note} onChange={e => setNote(e.target.value)} placeholder="for example: this is AP Physics C Mechanics" /></label>
    <button className="secondary-button" disabled={busy || !note.trim()} onClick={send}>Send note</button>{err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}</section>;
}
