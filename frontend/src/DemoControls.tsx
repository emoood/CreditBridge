import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "./api";
import { setDemo, useDemo } from "./demo";
import { useInstitution } from "./useApp";

/** Header: the Live AI / Replay switch (Live AI by default; Alt+D toggles it), a small chip when Replay is on, the stage speed and a reset so the demo can be repeated.
 *  Replay answers from a recorded run; a recording that is out of date (data or page reading changed since it was made) is flagged here and refused at upload. */
export function DemoControls() {
  const demo = useDemo();
  const inst = useInstitution();
  const [msg, setMsg] = useState<string | null>(null);
  const [stale, setStale] = useState<{ name: string; reason: string | null }[]>([]);
  useEffect(() => {
    if (!demo.on) return;
    api
      .demoRecordings()
      .then((r) => setStale(r.filter((x) => !x.current)))
      .catch(() => setStale([]));
  }, [demo.on]);
  useEffect(() => {
    const f = (e: KeyboardEvent) => {
      if (e.altKey && e.code === "KeyD") {
        e.preventDefault();
        setDemo({ on: !demo.on });
      }
    };
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [demo.on]);
  if (inst.demo_mode === false) return null;
  async function reset() {
    if (
      !window.confirm(
        "Clear every application so the demo can start again from the beginning?",
      )
    )
      return;
    try {
      await api.reset();
      window.location.reload();
    } catch (e) {
      setMsg((e as Error).message);
    }
  }
  return (
    <div className="demo-controls">
      <label
        className="demo-toggle"
        title="Live AI: the model reads and decides now. Replay: the model's answers come from a recorded run, so there is no wait. Shortcut: Alt+D"
      >
        <select
          aria-label="AI mode"
          value={demo.on ? "replay" : "live"}
          onChange={(e) => setDemo({ on: e.target.value === "replay" })}
        >
          <option value="live">Live AI</option>
          <option value="replay">Replay</option>
        </select>
      </label>
      {demo.on && (
        <>
          <span className="demo-chip">Replay</span>
          {stale.length > 0 && (
            <span
              className="demo-chip"
              style={{ background: "#fef3c7", color: "#92400e" }}
              title={stale.map((x) => `${x.name}: ${x.reason}`).join("\n")}
            >
              {stale.length} recording{stale.length === 1 ? "" : "s"} out of date
            </span>
          )}
          <select
            aria-label="Stage speed"
            value={demo.speed}
            onChange={(e) =>
              setDemo({ speed: e.target.value as "instant" | "fast" })
            }
          >
            <option value="fast">Fast (~0.5 s per stage)</option>
            <option value="instant">Instant</option>
          </select>
          <button
            className="link-btn"
            onClick={reset}
            title="Clear all applications"
          >
            <RotateCcw size={13} /> Reset
          </button>
        </>
      )}
      {msg && <span className="muted">{msg}</span>}
    </div>
  );
}
