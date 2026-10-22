import { useState } from 'react';
import { AlertCircle, Check, X } from 'lucide-react';
import { api, type CaseResult, type Decision } from './api';
import { Pill } from './ui';

const RULE_TONE: Record<string, string> = { PASS: 'green', FAIL: 'red', NEEDS_REVIEW: 'amber', NEEDS_HUMAN_VERIFICATION: 'amber', NOT_APPLICABLE: 'neutral' };

/** The evidence panel: why the AI suggested this, what the reviewer objected to, the rules, and the officer's decision. */
export function Evidence({ appId, r, role, onChanged, onClose }: { appId: string; r: CaseResult; role: string; onChanged: () => void; onClose: () => void }) {
  const m = r.match;
  const head = m.status === 'POTENTIAL_MATCH' && m.target ? `${m.target.code} · ${m.target.title}` : m.status === 'POTENTIAL_GENERAL_ELECTIVE' ? 'Possible general elective' : r.triage.reasons[0];
  return <aside className="evidence" aria-label="Evidence">
    <div className="evidence-head"><div><small>EVIDENCE</small><h3>{r.course.title}</h3><p className="muted">Grade {r.course.grade || '—'} · {head}</p></div>
      <button className="icon-button" onClick={onClose} aria-label="Close evidence"><X size={18} /></button></div>
    <div className="evidence-body">
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
    <DecisionBar appId={appId} r={r} role={role} onChanged={onChanged} />
  </aside>;
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
