import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, History, Loader2, UploadCloud } from "lucide-react";
import {
  api,
  type AppInfo,
  type DemoCase,
  type Recording,
  type StudentRecord,
} from "./api";
import { useDemo } from "./demo";
import {
  APP_ORDER,
  appStatus,
  build,
  curriculumOf,
  type AppStatus,
  type Built,
} from "./model";
import { StudentPopup } from "./StudentPopup";
import { AppPill } from "./ui";
import { loadApp, useInstitution } from "./useApp";

type Entry = {
  app: AppInfo;
  status: AppStatus;
  built: Built;
  curriculum: string;
};

function includeSet(id: string): Set<string> {
  try {
    return new Set<string>(
      JSON.parse(localStorage.getItem(`cb-include-${id}`) ?? "[]"),
    );
  } catch {
    return new Set<string>();
  }
}

/** Home: one row per application, what needs the officer first. */
export default function Worklist() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [open, setOpen] = useState<string | null>(null);
  const [role, setRole] = useState("admissions");
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [qual, setQual] = useState("");
  const [name, setName] = useState("");
  const [sid, setSid] = useState("");
  const [caseNo, setCaseNo] = useState("");
  const [rec, setRec] = useState<StudentRecord | null>(null);
  const [plant, setPlant] = useState(false);
  const inst = useInstitution();
  const demo = useDemo();
  const [cases, setCases] = useState<DemoCase[]>([]);
  const [selCase, setSelCase] = useState("");
  const [quals, setQuals] = useState<string[]>([]);
  const [recordings, setRecordings] = useState<Recording[]>([]);
  const [replayOpen, setReplayOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      const list = await api.applications();
      const loaded = await Promise.all(
        list.slice(0, 30).map(async (a) => {
          const d = await loadApp(a.id);
          const evaluating =
            ["CONFIRMED", "EVALUATION_IN_PROGRESS"].includes(d.app.status) ||
            d.evalJob?.status === "RUNNING" ||
            d.evalJob?.status === "QUEUED";
          const built = build(
            d.app,
            d.results,
            d.pending,
            includeSet(a.id),
            evaluating,
            d.states,
            d.actions,
          );
          return {
            app: { ...d.app, replay: a.replay ?? d.app.replay },
            status: appStatus(d.app, built, [d.readJob, d.evalJob]),
            built,
            curriculum: curriculumOf(d.app, d.results),
          } as Entry;
        }),
      );
      setEntries(
        loaded.sort(
          (a, b) =>
            APP_ORDER[a.status] - APP_ORDER[b.status] ||
            b.app.created_at.localeCompare(a.app.created_at),
        ),
      );
      setError(null);
    } catch (e) {
      setError(
        `Cannot reach the backend (${(e as Error).message}). Start it with: scripts/start.sh`,
      );
    }
  }, []);
  useEffect(() => {
    refresh();
  }, [refresh]);
  const anyBusy = entries.some((e) => e.status === "Processing");
  useEffect(() => {
    const t = setInterval(refresh, anyBusy ? 2500 : 10000);
    return () => clearInterval(t);
  }, [refresh, anyBusy]);
  useEffect(() => {
    api
      .qualifications()
      .then((q) => setQuals(q.qualifications))
      .catch(() => undefined);
    api
      .recordings()
      .then(setRecordings)
      .catch(() => undefined);
  }, [entries.length]);

  useEffect(() => {
    if (!demo.on) return;
    api
      .demoCases()
      .then((d) => {
        setCases(d.cases);
        setSelCase((c) => c || d.cases[0]?.case_number || "");
      })
      .catch(() => setCases([]));
  }, [demo.on]);
  const picked = cases.find((c) => c.case_number === selCase);
  useEffect(() => {
    if (demo.on && !picked?.misread_run) setPlant(false); // a misread can only be played for a case that has a prepared misread run
  }, [demo.on, picked]);

  async function upload(f: File | undefined) {
    if (!f) return;
    setUploading(true);
    setError(null);
    try {
      const a = await api.upload(
        f,
        name,
        qual,
        sid,
        plant,
        demo.on ? selCase : rec ? caseNo : "",
        demo.on ? demo.speed : "",
      );
      setPlant(false);
      setCaseNo("");
      setRec(null);
      setName("");
      setSid("");
      setOpen(a.id);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  return (
    <div className="worklist">
      {error && (
        <div className="eval-error">
          <AlertCircle size={17} /> {error}
        </div>
      )}
      <section className="panel wl-head">
        <p className="muted wl-hint">
          Open applications come first. Click a row to open the student.
        </p>
        <div className="wl-actions">
          {!demo.on && (
            <span
              className="muted demo-off-note"
              title="Tick Demo in the header, or open the app with ?demo=1"
            >
              Demo is off: an upload now uses the live model and takes minutes.
            </span>
          )}
          {demo.on ? (
            <label className="role-pick" title="Cases that have a prepared run">
              Case{" "}
              <select
                value={selCase}
                onChange={(e) => setSelCase(e.target.value)}
              >
                {cases.length === 0 && (
                  <option value="">No prepared runs</option>
                )}
                {cases.map((c) => (
                  <option key={c.case_number} value={c.case_number}>
                    {c.case_number} · {c.name}
                  </option>
                ))}
              </select>
              {picked && (
                <span className="muted">
                  {" "}
                  {picked.program} · file {picked.filename}
                </span>
              )}
            </label>
          ) : (
            <>
              <label className="role-pick">
                Curriculum{" "}
                <select value={qual} onChange={(e) => setQual(e.target.value)}>
                  <option value="">Detect from transcript</option>
                  {quals.map((q) => (
                    <option key={q}>{q}</option>
                  ))}
                </select>
              </label>
              <label
                className="role-pick"
                title="The case number loads name, contact, program, term and curriculum from the demo student data"
              >
                Case number{" "}
                <input
                  className="mini"
                  placeholder="e.g. CB-2026-0101"
                  value={caseNo}
                  style={{ width: 130 }}
                  onChange={(e) => {
                    const v = e.target.value;
                    setCaseNo(v);
                    setRec(null);
                    if (v.trim().length >= 8)
                      api
                        .studentRecord(v.trim())
                        .then(setRec)
                        .catch(() => setRec(null));
                  }}
                />
                {rec ? (
                  <span className="muted">
                    {" "}
                    {rec.name} · {rec.program} · {rec.term}
                  </span>
                ) : caseNo.trim().length >= 8 ? (
                  <span className="muted"> no record</span>
                ) : null}
              </label>
              <label className="role-pick">
                Student{" "}
                <input
                  className="mini"
                  placeholder="Name (synthetic)"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
                <input
                  className="mini"
                  placeholder="ID"
                  value={sid}
                  onChange={(e) => setSid(e.target.value)}
                  style={{ width: 90 }}
                />
              </label>
            </>
          )}
          {inst.demo_mode && (!demo.on || picked?.misread_run) && (
            <label
              className="role-pick demo-switch"
              title="Demo only: replaces the AP Chemistry mark with a wrong value before the extraction checks run, so the Intake recheck has something to fix (Transcript B)"
            >
              <input
                type="checkbox"
                checked={plant}
                onChange={(e) => setPlant(e.target.checked)}
              />{" "}
              Demo: plant a misread (Transcript B)
            </label>
          )}
          <input
            ref={fileRef}
            type="file"
            accept=".pdf,.png,.jpg,.jpeg"
            hidden
            onChange={(e) => upload(e.target.files?.[0])}
          />
          <button
            className="primary-button wl-btn"
            onClick={() => fileRef.current?.click()}
            disabled={uploading}
          >
            {uploading ? (
              <Loader2 size={16} className="spin" />
            ) : (
              <UploadCloud size={16} />
            )}{" "}
            Upload transcript
          </button>
          <button
            className="secondary-button"
            onClick={() => setReplayOpen(!replayOpen)}
            disabled={!recordings.length || demo.on}
            title={recordings.length ? "" : "No recorded run saved yet"}
          >
            <History size={16} /> Replay a recorded run
          </button>
          <label className="role-pick">
            Acting as{" "}
            <select value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="admissions">Admissions officer</option>
              <option value="registrar">Registrar (cannot decide)</option>
            </select>
          </label>
        </div>
      </section>
      {replayOpen && (
        <ReplayPicker
          recordings={recordings}
          role={role}
          onLoaded={(a) => {
            setReplayOpen(false);
            setOpen(a.id);
            refresh();
          }}
        />
      )}

      <section className="panel wl-table-wrap">
        <table className="wl-table">
          <thead>
            <tr>
              <th>Student</th>
              <th>Curriculum</th>
              <th>Status</th>
              <th className="num">Mapped</th>
              <th className="num">Needs action</th>
              <th className="num">Not evaluated</th>
            </tr>
          </thead>
          <tbody>
            {entries.map((e) => (
              <tr
                key={e.app.id}
                onClick={() => setOpen(e.app.id)}
                tabIndex={0}
                onKeyDown={(ev) => ev.key === "Enter" && setOpen(e.app.id)}
              >
                <td>
                  <strong>{e.app.student}</strong>
                  <small>
                    {e.app.id}
                    {e.app.replay ? " · replay" : ""}
                  </small>
                </td>
                <td>{e.curriculum}</td>
                <td>
                  <AppPill s={e.status} />
                </td>
                <td className="num">{e.built.counts.mapped || ""}</td>
                <td className={`num ${e.built.needsInput.length ? "hot" : ""}`}>
                  {e.built.needsInput.length || ""}
                </td>
                <td className="num">{e.built.counts.notEvaluated || ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {entries.length === 0 && !error && (
          <p className="muted pad">
            No transcripts yet. Upload one, or replay a recorded run.
          </p>
        )}
      </section>

      {open && (
        <StudentPopup
          appId={open}
          role={role}
          quals={quals}
          qual={qual}
          onClose={() => {
            setOpen(null);
            refresh();
          }}
          onChanged={refresh}
        />
      )}
    </div>
  );
}

function ReplayPicker({
  recordings,
  role,
  onLoaded,
}: {
  recordings: Recording[];
  role: string;
  onLoaded: (a: AppInfo) => void;
}) {
  const [name, setName] = useState(recordings[0]?.name ?? "");
  const [live, setLive] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const rec = recordings.find((r) => r.name === name);
  const toggle = (id: string) =>
    setLive((l) =>
      l.includes(id)
        ? l.filter((x) => x !== id)
        : l.length < 2
          ? [...l, id]
          : l,
    );
  async function go() {
    setBusy(true);
    setErr(null);
    try {
      onLoaded(await api.replay(name, live, role));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="panel replay-picker">
      <div className="section-overline">REPLAY A RECORDED RUN</div>
      <p className="muted">
        Loads a saved real run without calling the model. You can still run up
        to two courses live.
      </p>
      <select
        value={name}
        onChange={(e) => {
          setName(e.target.value);
          setLive([]);
        }}
      >
        {recordings.map((r) => (
          <option key={r.name} value={r.name}>
            {r.student?.name ? `${r.student.name} · ` : ""}
            {r.label}
            {r.case_path
              ? ` · ${r.model_calls ?? 0} recorded model calls`
              : " · older format"}
          </option>
        ))}
      </select>
      {rec && (
        <div className="live-pick">
          <small>Run live with the model (optional, max 2):</small>
          {rec.subjects.map((s) => (
            <label key={s.row_id}>
              <input
                type="checkbox"
                checked={live.includes(s.row_id)}
                onChange={() => toggle(s.row_id)}
              />{" "}
              {s.title}{" "}
              <span className="muted">({s.duration_s}s when recorded)</span>
            </label>
          ))}
        </div>
      )}
      {err && (
        <div className="eval-error">
          <AlertCircle size={16} /> {err}
        </div>
      )}
      <button
        className="primary-button wl-btn"
        onClick={go}
        disabled={busy || !name}
      >
        {busy ? <Loader2 size={16} className="spin" /> : <History size={16} />}{" "}
        Load replay
      </button>
    </section>
  );
}
