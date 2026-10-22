import { useCallback, useEffect, useState } from 'react';
import { api, type AppInfo, type CaseResult, type Institution, type Job } from './api';
import { isBusy } from './model';

export type AppData = { app: AppInfo; results: CaseResult[]; pending: { key: string; status: string; error_code: string }[]; readJob: Job | null; evalJob: Job | null };

export async function loadApp(id: string): Promise<AppData> {
  const [app, r] = await Promise.all([api.application(id), api.results(id)]);
  const [readJob, evalJob] = await Promise.all([app.reading_job_id ? api.job(app.reading_job_id) : null, app.evaluation_job_id ? api.job(app.evaluation_job_id) : null]);
  return { app, results: r.results, pending: r.pending_units ?? [], readJob, evalJob };
}

/** One application, refreshed every 1.5 s while the backend is working on it. */
export function useApp(id: string | null) {
  const [data, setData] = useState<AppData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(async () => {
    if (!id) return;
    try { setData(await loadApp(id)); setError(null); } catch (e) { setError((e as Error).message); }
  }, [id]);
  useEffect(() => { setData(null); refresh(); }, [refresh]);
  const busy = !!data && isBusy(data.app, [data.readJob, data.evalJob]);
  useEffect(() => { if (!busy) return; const t = setInterval(refresh, 1500); return () => clearInterval(t); }, [busy, refresh]);
  return { data, error, refresh, busy };
}

const NOTHING: Institution = { institution: '', transfer_credits: 3, display: { mark: false, school_credits: false, aud_credits: false, grade_year: false } };
/** What this institution wants shown (policies/institution.yaml). Everything optional is hidden until the file says otherwise. */
export function useInstitution(): Institution {
  const [inst, setInst] = useState<Institution>(NOTHING);
  useEffect(() => { api.institution().then(setInst).catch(() => undefined); }, []);
  return inst;
}

/** Per-application "include" choices live in the browser only for now (the backend gate comes later). */
export function useInclude(appId: string | null): [Set<string>, (id: string, on: boolean) => void] {
  const key = `cb-include-${appId}`;
  const read = () => { try { return new Set<string>(JSON.parse(localStorage.getItem(key) ?? '[]')); } catch { return new Set<string>(); } };
  const [set, setSet] = useState<Set<string>>(read);
  useEffect(() => { setSet(read()); }, [key]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id: string, on: boolean) => setSet(prev => {
    const n = new Set(prev); if (on) n.add(id); else n.delete(id);
    try { localStorage.setItem(key, JSON.stringify([...n])); } catch { /* storage unavailable */ }
    return n;
  });
  return [set, toggle];
}
