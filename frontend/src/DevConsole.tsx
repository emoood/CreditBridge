import { useEffect, useMemo, useState } from 'react';
import { Activity, Cpu, Trash2 } from 'lucide-react';
import { api, type DevEvent } from './api';

type Model = Awaited<ReturnType<typeof api.devModel>>;
const KINDS = ['all', 'llm_call', 'tool_call', 'stage', 'error'];

export default function DevConsole() {
  const [events, setEvents] = useState<DevEvent[]>([]);
  const [model, setModel] = useState<Model | null>(null);
  const [kind, setKind] = useState('all');
  const [open, setOpen] = useState<number | null>(null);
  const [live, setLive] = useState(false);

  useEffect(() => {
    api.devEvents().then(setEvents).catch(() => undefined);
    const es = new EventSource('/api/v1/dev/stream');
    es.onopen = () => setLive(true); es.onerror = () => setLive(false);
    es.onmessage = m => setEvents(prev => [...prev.slice(-299), JSON.parse(m.data)]);
    return () => es.close();
  }, []);
  useEffect(() => { const f = () => api.devModel().then(setModel).catch(() => setModel(null)); f(); const t = setInterval(f, 4000); return () => clearInterval(t); }, []);

  const llm = events.filter(e => e.kind === 'llm_call');
  const stats = useMemo(() => {
    const dur = llm.map(e => e.duration_s as number);
    const tps = llm.map(e => e.tok_per_s as number).filter(Boolean);
    return { calls: llm.length, avg: dur.length ? (dur.reduce((a, b) => a + b, 0) / dur.length).toFixed(1) : '—',
      tps: tps.length ? (tps.reduce((a, b) => a + b, 0) / tps.length).toFixed(0) : '—',
      tools: events.filter(e => e.kind === 'tool_call').length, toolFails: events.filter(e => e.kind === 'tool_call' && !e.ok).length,
      errors: events.filter(e => e.kind === 'error').length };
  }, [events, llm]);
  const shown = events.filter(e => kind === 'all' || e.kind === kind).slice().reverse();

  return <div className="dev">
    <div className="dev-grid">
      <section className="panel dev-card"><div className="section-overline"><Cpu size={13} /> MODEL</div>
        <h3>{model?.model ?? '—'}</h3><p className="muted">{model ? model.provider : 'backend unreachable'}</p>
        <p>{model?.ok ? <span className="ok">● reachable</span> : <span className="bad">● {model?.detail ?? 'down'}</span>} {model?.loaded ? `· loaded ${model.size_mb} MB` : model?.ok ? '· idle (not loaded)' : ''}</p></section>
      <section className="panel dev-card"><div className="section-overline"><Activity size={13} /> LIVE STREAM</div><h3>{live ? 'Connected' : 'Disconnected'}</h3><p className="muted">{events.length} events buffered</p></section>
      <Stat l="Model calls" v={String(stats.calls)} /><Stat l="Avg latency" v={`${stats.avg}s`} /><Stat l="Avg tokens/s" v={stats.tps} />
      <Stat l="Tool calls" v={`${stats.tools}${stats.toolFails ? ` (${stats.toolFails} failed)` : ''}`} /><Stat l="Errors" v={String(stats.errors)} />
    </div>
    <section className="panel">
      <div className="panel-header"><div><div className="section-overline">DEVELOPER ONLY</div><h2>AI activity</h2></div>
        <div className="list-controls"><select aria-label="Filter events" value={kind} onChange={e => setKind(e.target.value)}>{KINDS.map(k => <option key={k}>{k}</option>)}</select>
          <button className="secondary-button" onClick={() => api.reset().then(() => setEvents([]))}><Trash2 size={15} /> Reset demo</button></div></div>
      <div className="feed">{shown.length === 0 && <div className="empty-state">No events yet. Run an evaluation.</div>}
        {shown.map(e => <div key={e.id} className={`feed-row feed-${e.kind}`}>
          <button onClick={() => setOpen(open === e.id ? null : e.id)}>
            <span className="muted">{new Date(e.ts * 1000).toLocaleTimeString()}</span>
            <span className="kind">{e.kind}</span>
            <span>{e.course ?? ''}</span>
            <span className="grow">{e.kind === 'llm_call' ? `${e.stage} · ${e.duration_s}s · ${e.output_tokens ?? '?'} tok${e.tool_calls?.length ? ' → ' + e.tool_calls.map((t: { name: string }) => t.name).join(', ') : ''}`
              : e.kind === 'tool_call' ? `${e.role} · ${e.tool} ${e.ok ? '✓' : '✗ ' + (e.error ?? '')}` : e.kind === 'stage' ? `${e.stage}${e.triage ? ' → ' + e.triage : ''}` : e.error}</span>
          </button>
          {open === e.id && <pre>{JSON.stringify(e, null, 2)}</pre>}</div>)}</div>
    </section>
  </div>;
}
const Stat = ({ l, v }: { l: string; v: string }) => <section className="panel dev-card"><div className="section-overline">{l.toUpperCase()}</div><h3>{v}</h3></section>;
