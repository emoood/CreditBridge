import { useEffect, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "./api";
import { setDemo, useDemo } from "./demo";
import { useInstitution } from "./useApp";

/** Header: the Demo mode switch (off by default; Alt+D toggles it), a small chip when it is on, the stage speed and a reset so the demo can be repeated. */
export function DemoControls() {
  const demo = useDemo();
  const inst = useInstitution();
  const [msg, setMsg] = useState<string | null>(null);
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
        title="Use the prepared run for each file: no model wait. Shortcut: Alt+D"
      >
        <input
          type="checkbox"
          checked={demo.on}
          onChange={(e) => setDemo({ on: e.target.checked })}
        />{" "}
        Demo
      </label>
      {demo.on && (
        <>
          <span className="demo-chip">Demo mode</span>
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
