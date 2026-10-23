import { useEffect, useState } from 'react';
import { AlertCircle, Check, Edit2, Loader2, Mail, Plus, Send, Trash2, X } from 'lucide-react';
import { outboxApi } from './api';

export type RequestItem = {
  id: string;
  row_id: string;
  course_title: string;
  what: string;
  why: string;
  type: string;
  status: 'DRAFT' | 'SENT' | 'RECEIVED' | 'CANCELLED';
  token?: string;
};

export type Template = {
  type: string;
  what: string;
  why: string;
};

export type EmailPreview = {
  student_name: string;
  recipient: string;
  subject: string;
  body: string;
  items: RequestItem[];
  simulated: boolean;
};

export type OutboxHistory = {
  id: number;
  recipient: string;
  original_recipient?: string;
  subject: string;
  body: string;
  items: RequestItem[];
  status: string;
  error?: string;
  simulated: boolean;
  sent_at: string;
};

export function OutboxModal({
  appId,
  role,
  onClose,
  onChanged,
}: {
  appId: string;
  role: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [items, setItems] = useState<RequestItem[]>([]);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [types, setTypes] = useState<string[]>([]);
  const [preview, setPreview] = useState<EmailPreview | null>(null);
  const [history, setHistory] = useState<OutboxHistory[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Form state for adding/editing item
  const [editingId, setEditingId] = useState<string | null>(null);
  const [formRowId, setFormRowId] = useState<string>('');
  const [formType, setFormType] = useState<string>('syllabus or outline');
  const [formWhat, setFormWhat] = useState('');
  const [formWhy, setFormWhy] = useState('');

  const loadData = async () => {
    try {
      setLoading(true);
      const reqRes = await outboxApi.requests(appId);
      setItems(reqRes.items || []);
      setTemplates(reqRes.templates || []);
      setTypes(reqRes.request_types || []);
      if ((reqRes.items || []).length > 0 && !formRowId) {
        setFormRowId(reqRes.items[0].row_id);
      }

      const prevRes = await outboxApi.preview(appId);
      setPreview(prevRes);

      const histRes = await outboxApi.history(appId);
      setHistory(histRes || []);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, [appId]);

  const handleApplyTemplate = (tmpl: Template) => {
    setFormType(tmpl.type);
    setFormWhat(tmpl.what);
    setFormWhy(tmpl.why);
  };

  const handleSaveItem = async () => {
    if (!formWhat.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (editingId) {
        const row = items.find(i => i.id === editingId)?.row_id || formRowId;
        await outboxApi.editRequest(appId, row, editingId, role, {
          what: formWhat,
          why: formWhy,
          type: formType,
        });
        setToast('Request item updated.');
      } else {
        await outboxApi.addRequest(appId, formRowId, role, {
          what: formWhat,
          why: formWhy,
          type: formType,
        });
        setToast('Request item added.');
      }
      setEditingId(null);
      setFormWhat('');
      setFormWhy('');
      await loadData();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleRemove = async (rowId: string, reqId: string) => {
    setBusy(true);
    setError(null);
    try {
      await outboxApi.removeRequest(appId, rowId, reqId, role);
      setToast('Item removed.');
      await loadData();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const handleSend = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await outboxApi.send(appId, role, { recipient: preview?.recipient });
      setToast(res.simulated ? 'Simulated send complete.' : 'Email sent successfully via SMTP.');
      await loadData();
      onChanged();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="pop-backdrop" onClick={onClose}>
      <div className="pop pop-wide" onClick={e => e.stopPropagation()} role="dialog" aria-label="Requests Outbox">
        <header className="pop-head">
          <div>
            <small>OUTBOX & REQUESTS</small>
            <h2>Student Documentation Request</h2>
            <p className="muted">Review open items, preview email, and dispatch to applicant.</p>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close">
            <X size={22} />
          </button>
        </header>

        {error && (
          <div className="eval-error">
            <AlertCircle size={15} /> {error}
          </div>
        )}
        {toast && (
          <div className="toast-line" onClick={() => setToast(null)}>
            <Check size={14} /> {toast}
          </div>
        )}

        {loading ? (
          <p className="muted pad">
            <Loader2 size={15} className="spin" /> Loading outbox…
          </p>
        ) : (
          <div className="pop-body" style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '24px' }}>
            {/* Left column: Request items management */}
            <div>
              <h3>Request Items ({items.length})</h3>
              <p className="muted" style={{ marginBottom: '12px' }}>
                Items are grouped into a single notification email per student.
              </p>

              {items.length === 0 ? (
                <p className="muted">No open or past requests for this applicant.</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '16px' }}>
                  {items.map(it => (
                    <div
                      key={it.id}
                      style={{
                        border: '1px solid var(--border, #e2e8f0)',
                        padding: '10px 12px',
                        borderRadius: '6px',
                        background: 'var(--bg-subtle, #f8fafc)',
                      }}
                    >
                      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                        <strong>{it.course_title || it.row_id}</strong>
                        <span style={{ fontSize: '11px', padding: '2px 6px', borderRadius: '4px', background: it.status === 'RECEIVED' ? '#dcfce7' : it.status === 'SENT' ? '#e0f2fe' : '#fef3c7' }}>
                          {it.status}
                        </span>
                      </div>
                      <p style={{ margin: '4px 0 2px', fontSize: '13px' }}>{it.what}</p>
                      {it.why && <p className="muted" style={{ fontSize: '12px', margin: '0 0 6px' }}>Why: {it.why}</p>}
                      <div style={{ display: 'flex', gap: '8px', fontSize: '12px' }}>
                        <button
                          className="link-btn"
                          disabled={busy}
                          onClick={() => {
                            setEditingId(it.id);
                            setFormRowId(it.row_id);
                            setFormType(it.type);
                            setFormWhat(it.what);
                            setFormWhy(it.why);
                          }}
                        >
                          <Edit2 size={12} /> Edit
                        </button>
                        <button
                          className="link-btn"
                          disabled={busy}
                          onClick={() => handleRemove(it.row_id, it.id)}
                          style={{ color: '#ef4444' }}
                        >
                          <Trash2 size={12} /> Remove
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Add / Edit Form */}
              <div style={{ border: '1px dashed var(--border, #cbd5e1)', padding: '12px', borderRadius: '6px', marginTop: '12px' }}>
                <h4 style={{ margin: '0 0 8px' }}>{editingId ? 'Edit Item' : 'Add Request Item'}</h4>
                {templates.length > 0 && !editingId && (
                  <div style={{ marginBottom: '8px' }}>
                    <small className="muted">Templates: </small>
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>
                      {templates.map((t, idx) => (
                        <button
                          key={idx}
                          type="button"
                          className="link-btn"
                          style={{ fontSize: '11px', border: '1px solid #cbd5e1', padding: '2px 6px', borderRadius: '4px' }}
                          onClick={() => handleApplyTemplate(t)}
                        >
                          {t.type}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                  <select
                    value={formType}
                    onChange={e => setFormType(e.target.value)}
                    style={{ padding: '6px', borderRadius: '4px', border: '1px solid #cbd5e1' }}
                  >
                    {types.map(t => (
                      <option key={t} value={t}>
                        {t}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    placeholder="What document or fact is needed"
                    value={formWhat}
                    onChange={e => setFormWhat(e.target.value)}
                    style={{ padding: '6px', borderRadius: '4px', border: '1px solid #cbd5e1' }}
                  />
                  <input
                    type="text"
                    placeholder="Why it is needed (one concise line)"
                    value={formWhy}
                    onChange={e => setFormWhy(e.target.value)}
                    style={{ padding: '6px', borderRadius: '4px', border: '1px solid #cbd5e1' }}
                  />
                  <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                    <button className="secondary-button" disabled={busy || !formWhat.trim()} onClick={handleSaveItem}>
                      <Plus size={14} /> {editingId ? 'Update item' : 'Add item'}
                    </button>
                    {editingId && (
                      <button
                        className="link-btn"
                        onClick={() => {
                          setEditingId(null);
                          setFormWhat('');
                          setFormWhy('');
                        }}
                      >
                        Cancel
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Right column: Email Preview & Send */}
            <div>
              <h3>Email Preview</h3>
              {preview ? (
                <div style={{ border: '1px solid var(--border, #cbd5e1)', padding: '14px', borderRadius: '6px', background: '#fff' }}>
                  <div style={{ borderBottom: '1px solid #e2e8f0', paddingBottom: '8px', marginBottom: '8px', fontSize: '12px' }}>
                    <div><strong>To:</strong> {preview.recipient || <span style={{ color: '#ef4444' }}>Missing student email</span>}</div>
                    <div><strong>Subject:</strong> {preview.subject}</div>
                    {preview.simulated && (
                      <span style={{ display: 'inline-block', marginTop: '4px', fontSize: '11px', background: '#fef3c7', padding: '2px 6px', borderRadius: '4px' }}>
                        Simulated mode (SMTP sending disabled in settings)
                      </span>
                    )}
                  </div>
                  <pre style={{ whiteSpace: 'pre-wrap', fontFamily: 'inherit', fontSize: '13px', margin: 0, maxHeight: '280px', overflowY: 'auto' }}>
                    {preview.body}
                  </pre>

                  <div style={{ marginTop: '16px', display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
                    <button
                      className="primary-button"
                      disabled={busy || !preview.recipient || items.length === 0}
                      onClick={handleSend}
                    >
                      <Send size={15} /> {preview.simulated ? 'Simulated send' : 'Send email'}
                    </button>
                  </div>
                </div>
              ) : (
                <p className="muted">No preview available.</p>
              )}

              {/* History */}
              {history.length > 0 && (
                <div style={{ marginTop: '20px' }}>
                  <h4>Sent History ({history.length})</h4>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', fontSize: '12px' }}>
                    {history.map(h => (
                      <div key={h.id} style={{ border: '1px solid #e2e8f0', padding: '6px 10px', borderRadius: '4px' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between' }}>
                          <span>To: {h.recipient}</span>
                          <span className="muted">{new Date(h.sent_at).toLocaleDateString()}</span>
                        </div>
                        <div className="muted">{h.status} {h.simulated ? '(simulated)' : ''} · {h.items?.length || 0} item(s)</div>
                        {h.error && <div style={{ color: '#ef4444' }}>{h.error}</div>}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export function OutboxButton({
  appId,
  role,
  onChanged,
}: {
  appId: string;
  role: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [count, setCount] = useState<number>(0);

  useEffect(() => {
    outboxApi.requests(appId).then(r => {
      const openCount = (r.items || []).filter((i: RequestItem) => i.status === 'DRAFT' || i.status === 'SENT').length;
      setCount(openCount);
    }).catch(() => undefined);
  }, [appId, open]);

  return (
    <>
      <button
        className="secondary-button"
        onClick={() => setOpen(true)}
        title="Open Outbox to draft, preview and dispatch document requests to student"
      >
        <Mail size={15} /> Request documents{count > 0 ? ` (${count})` : ''}
      </button>
      {open && (
        <OutboxModal
          appId={appId}
          role={role}
          onClose={() => setOpen(false)}
          onChanged={() => {
            onChanged();
          }}
        />
      )}
    </>
  );
}
