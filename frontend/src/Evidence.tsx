import { useState } from 'react';
import { AlertCircle, Check, X } from 'lucide-react';
import { api, type CaseResult, type Decision, type DraftedRequest, type Finding, type RowStateInfo } from './api';
import { RESOLUTION_LABEL, type Item } from './model';
import { Pill } from './ui';

const RULE_TONE: Record<string, string> = { PASS: 'green', FAIL: 'red', NEEDS_REVIEW: 'amber', NEEDS_HUMAN_VERIFICATION: 'amber', NOT_APPLICABLE: 'neutral' };

/** The evidence panel: why the AI suggested this, what the reviewer objected to, the rules, and the officer's decision. */
export function Evidence({ appId, r, item, role, onChanged, onClose }: { appId: string; r: CaseResult; item?: Item; role: string; onChanged: () => void; onClose: () => void }) {
  const m = r.match;
  const head = m.status === 'POTENTIAL_MATCH' && m.target ? `${m.target.code} · ${m.target.title}` : m.status === 'POTENTIAL_GENERAL_ELECTIVE' ? 'Possible general elective' : r.triage.reasons[0];
  return <aside className="evidence" aria-label="Evidence">
    <div className="evidence-head"><div><small>EVIDENCE</small><h3>{r.course.title}</h3><p className="muted">Grade {r.course.grade || '—'} · {head}</p></div>
      <button className="icon-button" onClick={onClose} aria-label="Close evidence"><X size={18} /></button></div>
    <div className="evidence-body">
      {r.row_state && <CaseBlock appId={appId} r={r} s={r.row_state} role={role} onChanged={onChanged} />}
      {r.review && r.review.status === 'CHALLENGED' && <div className="block objection-block"><h4>Reviewer objects <Pill tone="amber">{(r.review.objections ?? []).length} objection{r.review.objections.length === 1 ? '' : 's'}</Pill></h4>
        {(r.review.objections ?? []).map((o, i) => <div className="objection" key={i}><p>{o.text}</p>{o.excerpts.map(e => <blockquote key={e.id}><small>{e.id}</small>{e.text}</blockquote>)}</div>)}
        {(r.review.notes ?? []).length > 0 && <div className="minor"><small>Minor remarks from the reviewer</small><ul>{(r.review.notes ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
        <p className="muted">The reviewer only advises. It does not change the suggestion.</p></div>}
      {r.review && r.review.status !== 'CHALLENGED' && <div className="block"><h4>Independent review <Pill tone={r.review.status === 'SUPPORTED_FOR_HUMAN_REVIEW' ? 'green' : 'amber'}>{r.review.status.replace(/_/g, ' ').toLowerCase()}</Pill></h4>
        {(r.review.notes ?? []).length > 0 && <div className="minor"><small>Minor remarks</small><ul>{(r.review.notes ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}</div>}
      {(m.supported_outcomes ?? []).length > 0 && <div className="block"><h4>Outcomes the evidence supports {m.evidence_strength && <span className="muted">· coverage {m.evidence_strength.toLowerCase()}</span>}</h4>
        {(m.supported_outcomes ?? []).map((o, i) => <div className="pair" key={i}><div><small>SUBJECT · {o.subject.id}</small><p>{o.subject.text}</p></div><div><small>AUD COURSE · {o.target.id}</small><p>{o.target.text}</p></div><div className="why">{o.explanation}</div></div>)}
        {m.target?.basis === 'catalog description only' && <p className="muted">The AUD side uses the catalog description only: the catalog publishes no learning outcomes.</p>}</div>}
      {(m.title_only_suggestions ?? []).length > 0 && <div className="block title-only"><h4>AI judgment from title only, no syllabus on file <Pill tone="amber">unverified</Pill></h4>
        <p className="muted">These guesses use only the subject’s title and level. There is no evidence behind them and nothing here can be accepted. Confirm manually once the syllabus arrives.</p>
        <ul>{(m.title_only_suggestions ?? []).map((s, i) => <li key={i}><strong>{s.target ? `${s.target.code} · ${s.target.title}` : '?'}</strong> <small>{s.target ? `level ${s.target.level}` : ''}</small><br />{s.reason}</li>)}</ul></div>}
      {(m.missing_or_weaker_outcomes ?? []).length > 0 && <div className="block"><h4>Missing or weaker</h4><ul>{(m.missing_or_weaker_outcomes ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
      {(m.concerns ?? []).length > 0 && <div className="block"><h4>Concerns</h4><ul>{(m.concerns ?? []).map((x, i) => <li key={i}>{x}</li>)}</ul></div>}
      {(r.rules ?? []).length > 0 && <div className="block"><h4>Policy checks <span className="muted">· computed by code, not AI</span></h4><div className="rules">{(r.rules ?? []).map(x => <div key={x.rule_id}><Pill tone={RULE_TONE[x.status] ?? 'neutral'}>{x.status.replace(/_/g, ' ').toLowerCase()}</Pill> <strong>{x.rule_id.replace('AUD_', '').replace(/_/g, ' ').toLowerCase()}</strong> <span className="muted">{x.reason}</span></div>)}</div>
        {(r.staff_checklist ?? []).length > 0 && <details><summary>Staff checklist ({(r.staff_checklist ?? []).length} items to confirm manually)</summary><ul>{(r.staff_checklist ?? []).map(x => <li key={x.rule_id}>{x.reason}</li>)}</ul></details>}</div>}
      {(r.syllabus_requests ?? []).length > 0 && <div className="block"><h4>Request drafted</h4><p>An in-app request for the missing {r.syllabus_requests[0].missing_fields.join(', ')} was drafted ({r.syllabus_requests[0].status.toLowerCase().replace(/_/g, ' ')}). Nothing was sent.</p></div>}
      {(r.case_file?.notes ?? []).length > 0 && <div className="block"><h4>Agent notes</h4>{(r.case_file?.notes ?? []).map((n, i) => <p key={i}><strong>{n.by} {n.kind}:</strong> {n.text}</p>)}</div>}
    </div>
    {r.row_state && item ? <ActionBar appId={appId} item={item} role={role} onChanged={onChanged} /> : <DecisionBar appId={appId} r={r} role={role} onChanged={onChanged} />}
  </aside>;
}

function CaseBlock({ appId, r, s, role, onChanged }: { appId: string; r: CaseResult; s: RowStateInfo; role: string; onChanged: () => void }) {
  const f: Finding | null = r.case?.finding ?? s.finding;
  const reqs: DraftedRequest[] = r.case?.requests ?? s.requests;
  const missing = s.condition_result?.status === 'MISSING' ? s.condition_result.missing : [];
  const [vals, setVals] = useState<Record<string, string>>({}); const [file, setFile] = useState<File | null>(null); const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  async function send() {
    setBusy(true); setErr(null);
    try { const facts: Record<string, unknown> = {}; for (const [k, v] of Object.entries(vals)) if (v.trim()) facts[k] = Number.isNaN(Number(v)) ? v.trim() : Number(v);
      await api.evidence(appId, s.row_id, role, facts, file, note); setVals({}); setFile(null); setNote(''); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  return <div className="block case-block"><h4>Case finding <Pill tone={s.status === 'Mapped' ? 'green' : s.status === 'Waiting on document' ? 'blue' : 'amber'}>{s.status}</Pill></h4>
    <p>{f?.headline ?? s.reason}</p>
    {s.mapping && <p className="muted">Placeholder mapping to {s.mapping.aud_code} (version {s.mapping.version}, {s.mapping.status.toLowerCase()}){s.mapping.condition ? ` · condition: ${s.mapping.condition.type.replace(/_/g, ' ')} ${JSON.stringify(s.mapping.condition.value)}` : ''}</p>}
    {s.condition_result && s.condition_result.status !== 'MET' && <p className="muted">Condition check: {s.condition_result.reason}</p>}
    {f && f.missing_evidence.length > 0 && <><small className="lbl">Missing evidence</small><ul>{f.missing_evidence.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
    {f && f.gaps.length > 0 && <><small className="lbl">Gaps</small><ul>{f.gaps.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
    {f && f.candidates.length > 0 && <><small className="lbl">Candidates the agent proposes</small><ul>{f.candidates.map((c, i) => <li key={i}><strong>{c.aud_code}</strong> {c.why}</li>)}</ul></>}
    {f && f.review_package.length > 0 && <><small className="lbl">Review package</small><ul>{f.review_package.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
    {reqs.length > 0 && <><small className="lbl">Requests (drafts, nothing is sent)</small>{reqs.map(q => <div className="req" key={q.id}><strong>{q.what}</strong><span className="muted"> · {q.status.toLowerCase()} · to the {q.to}</span><br /><span className="muted">{q.why}</span></div>)}</>}
    {f && (f.questions_for_chair?.length ?? 0) > 0 && <><small className="lbl">Questions for the chair</small><ul>{f.questions_for_chair!.map((x, i) => <li key={i}>{x}</li>)}</ul></>}
    {s.status !== 'Mapped' && s.status !== 'Not evaluated' && <div className="evidence-form"><small className="lbl">Add a note or evidence for the agent</small>
      <textarea value={note} onChange={e => setNote(e.target.value)} placeholder="e.g. this is AP Physics C Mechanics, or the student sent the score report" rows={2} />
      {missing.map(m => <label key={m.field}>{m.field.replace(/_/g, ' ')} <input value={vals[m.field] ?? ''} onChange={e => setVals({ ...vals, [m.field]: e.target.value })} placeholder={m.evidence} /></label>)}
      <label>Document (optional) <input type="file" onChange={e => setFile(e.target.files?.[0] ?? null)} /></label>
      <button className="secondary-button" disabled={busy || (!file && !note.trim() && !Object.values(vals).some(v => v.trim()))} onClick={send}>{busy ? 'Handing back…' : 'Hand back to the agent'}</button>
      {err && <div className="eval-error"><AlertCircle size={15} /> {err}</div>}</div>}
  </div>;
}

function ActionBar({ appId, item, role, onChanged }: { appId: string; item: Item; role: string; onChanged: () => void }) {
  const [mode, setMode] = useState<'change' | 'reject' | 'nocredit' | null>(null);
  const [reason, setReason] = useState(''); const [q, setQ] = useState(''); const [hits, setHits] = useState<{ code: string; title: string; level: number }[]>([]);
  const [code, setCode] = useState(''); const [credits, setCredits] = useState('');
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const a = item.action;
  const hasSuggestion = !!item.state?.mapping;
  async function go(body: { action: string; aud_code?: string; credits?: number; reason?: string }) {
    setBusy(true); setErr(null);
    try { await api.rowAction(appId, item.rowId, role, body); setMode(null); setReason(''); setCode(''); setCredits(''); setQ(''); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  function search(v: string) { setQ(v); if (v.trim().length < 2) { setHits([]); return; } api.searchCatalog(v).then(setHits).catch(() => setHits([])); }
  return <div className="decision">
    {a && ['CONFIRM', 'CHANGE', 'REJECT', 'NO_CREDIT', 'CORRECT'].includes(a.action) && <p className="resolved-note"><strong>{RESOLUTION_LABEL[a.action]}</strong> by {a.actor} · {new Date(a.at).toLocaleTimeString()}
      {a.action !== 'CONFIRM' && a.chosen.aud_code ? ` → ${a.chosen.aud_code}${a.chosen.credits ? ` (${a.chosen.credits} cr)` : ''}` : ''}{a.reason ? ` — “${a.reason}”` : ''}
      {a.original.aud_code ? <span className="muted"> · the system suggested {a.original.aud_code} ({a.original.status})</span> : <span className="muted"> · the system had no suggestion</span>}</p>}
    {!mode && <div className="decision-buttons">
      {hasSuggestion && item.status === 'Mapped' && <button className="primary-button" disabled={busy || item.action?.action === 'CONFIRM'} onClick={() => go({ action: 'CONFIRM' })}><Check size={16} /> Confirm</button>}
      <button className="secondary-button" onClick={() => setMode('change')}>Change…</button>
      {hasSuggestion ? <button className="secondary-button" onClick={() => setMode('reject')}><X size={16} /> Reject…</button> : <button className="link-btn" onClick={() => setMode('nocredit')}>No credit…</button>}
    </div>}
    {mode === 'change' && <div className="change-box"><input autoFocus placeholder="Search AUD courses (e.g. calculus)" value={q} onChange={e => search(e.target.value)} />
      {hits.map(h => <button key={h.code} className={`hit ${code === h.code ? 'on' : ''}`} onClick={() => setCode(h.code)}><strong>{h.code}</strong> {h.title}</button>)}
      <input placeholder="Credits (optional)" value={credits} onChange={e => setCredits(e.target.value.replace(/\D/g, ''))} style={{ width: 130 }} />
      <input placeholder="Reason (required)" value={reason} onChange={e => setReason(e.target.value)} />
      <div className="decision-buttons"><button className="primary-button" disabled={busy || !reason.trim() || (!code && !credits)} onClick={() => go({ action: 'CHANGE', aud_code: code || undefined, credits: credits ? Number(credits) : undefined, reason })}>Save change</button>
        <button className="link-btn" onClick={() => setMode(null)}>Cancel</button></div></div>}
    {(mode === 'reject' || mode === 'nocredit') && <div className="change-box"><input autoFocus placeholder={mode === 'reject' ? 'Why reject this suggestion? (required)' : 'Why no credit? (required)'} value={reason} onChange={e => setReason(e.target.value)} />
      <div className="decision-buttons"><button className="secondary-button" disabled={busy || !reason.trim()} onClick={() => go({ action: mode === 'reject' ? 'REJECT' : 'NO_CREDIT', reason })}>{mode === 'reject' ? 'Reject' : 'No credit'}</button>
        <button className="link-btn" onClick={() => setMode(null)}>Cancel</button></div></div>}
    {err && <div className="eval-error"><AlertCircle size={16} /> {err}</div>}
  </div>;
}

function DecisionBar({ appId, r, role, onChanged }: { appId: string; r: CaseResult; role: string; onChanged: () => void }) {
  const [reason, setReason] = useState(''); const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const canAccept = ['POTENTIAL_MATCH', 'POTENTIAL_GENERAL_ELECTIVE'].includes(r.match.status) && r.triage.category !== 'BLOCKED_BY_RULE';
  async function go(d: Decision['decision']) {
    setBusy(true); setErr(null);
    try { await api.decide(appId, r.course.code, role, d, reason, r.version); setReason(''); onChanged(); } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }
  if (r.decision) return <div className="decision done"><strong>{r.decision.decision.replace(/_/g, ' ').toLowerCase()}</strong> by {r.decision.actor} · {new Date(r.decision.at).toLocaleTimeString()}{r.decision.reason && <> — “{r.decision.reason}”</>}{r.decision.stale && <p className="muted">This decision answered an earlier suggestion; the subject was re-evaluated since. Please review again.</p>}</div>;
  return <div className="decision">
    <p className="muted">This is an AI suggestion, not a decision.</p>
    <input placeholder="Reason (required to decline or request information)" value={reason} onChange={e => setReason(e.target.value)} />
    <div className="decision-buttons">
      <button className="primary-button" disabled={busy || !canAccept} onClick={() => go('ACCEPTED')} title={canAccept ? '' : 'Nothing to accept, or blocked by a policy rule'}><Check size={16} /> Accept</button>
      <button className="secondary-button" disabled={busy} onClick={() => go('DECLINED')}><X size={16} /> Decline</button>
      <button className="secondary-button" disabled={busy} onClick={() => go('MORE_INFORMATION_REQUESTED')}>Request information</button>
    </div>
    {err && <div className="eval-error"><AlertCircle size={16} /> {err}</div>}
  </div>;
}
