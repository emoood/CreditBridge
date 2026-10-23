// Demo mode: one switch for the whole app, kept in the browser. Off by default. When on, uploads are matched to a prepared run (the model's replies were recorded earlier);
// every step of the code still runs live. Stage speed: instant, or fast (about half a second per stage).
import { useSyncExternalStore } from "react";

export type DemoState = { on: boolean; speed: "instant" | "fast" };
const KEY = "cb-demo";
function read(): DemoState {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "{}");
    return {
      on: v.on === true,
      speed: v.speed === "instant" ? "instant" : "fast",
    };
  } catch {
    return { on: false, speed: "fast" };
  }
}
let state: DemoState = read();
const subs = new Set<() => void>();
export function setDemo(p: Partial<DemoState>) {
  state = { ...state, ...p };
  try {
    localStorage.setItem(KEY, JSON.stringify(state));
  } catch {
    /* storage unavailable: the switch still works for this tab */
  }
  subs.forEach((f) => f());
}
export const getDemo = () => state;
export function useDemo(): DemoState {
  return useSyncExternalStore(
    (f) => {
      subs.add(f);
      return () => subs.delete(f);
    },
    () => state,
  );
}
