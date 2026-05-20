/**
 * Phase 17.0b Chunk C - Timeline simulator for stateful blocks.
 *
 * Drives /api/computed-tags/preview step-by-step to evolve a stateful
 * block's output over a virtual time axis. The user:
 *   1. Toggles each input HIGH/LOW (the named-input semantics for each
 *      block come from STATEFUL_INPUT_LABELS - e.g. TON has one "input",
 *      SR has "set" and "reset", CTU has "count_up" and "reset").
 *   2. Presses Step +100ms / +500ms / +1s to advance sim time.
 *   3. The backend evaluates with the current state, returns new state.
 *      We thread new_state into the next call so the timer/counter/latch
 *      remembers what it was doing.
 *
 * History pane shows the last 30 steps with input states and the
 * resulting output - making it easy to verify "TON Q goes HIGH after
 * preset_ms" or "CTU CV increments on rising edge of count_up".
 *
 * Single source of truth: the actual Python block code runs server-side,
 * so the simulator can never diverge from production behavior.
 */
import { useEffect, useMemo, useState } from "react";
import { RotateCcw, Loader2 } from "lucide-react";
import { blockInputs, GOOD_NON_SPECIFIC } from "@/lib/blockPreview";


/** Named-input ordering per stateful block code. Matches the order
 *  block.inputs() classmethod returns (mirrors Python). */
const STATEFUL_INPUT_LABELS: Record<string, string[]> = {
  TON: ["input"],
  TOF: ["input"],
  TP: ["input"],
  R_TRIG: ["input"],
  F_TRIG: ["input"],
  SR: ["set", "reset"],
  RS: ["set", "reset"],
  CTU: ["count_up", "reset"],
  CTD: ["count_down", "load"],
};


interface TimelineStep {
  t: number;
  inputBits: Record<number, boolean>;
  outputValue: number | null;
  outputQuality: number;
  state: any;
}


interface StatefulBlockSimulatorProps {
  blockCode: string;
  blockConfig: any;
}


export function StatefulBlockSimulator({
  blockCode, blockConfig,
}: StatefulBlockSimulatorProps) {
  const tagIds = useMemo(
    () => blockInputs(blockCode, blockConfig),
    [blockCode, blockConfig],
  );
  const labels = STATEFUL_INPUT_LABELS[blockCode] ?? [];

  const [steps, setSteps] = useState<TimelineStep[]>([]);
  const [simTime, setSimTime] = useState(0);
  const [currentState, setCurrentState] = useState<any>(null);
  const [inputStates, setInputStates] = useState<Record<number, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Keep inputStates aligned with current tagIds. Preserve toggles for
  // tags that survived a config edit; drop ones that no longer apply.
  const tagIdsKey = tagIds.join(",");
  useEffect(() => {
    setInputStates(prev => {
      const next: Record<number, boolean> = {};
      for (const tid of tagIds) next[tid] = prev[tid] ?? false;
      return next;
    });
  }, [tagIdsKey]);

  // Reset on blockCode change (different block = fresh simulation).
  useEffect(() => {
    setSteps([]);
    setSimTime(0);
    setCurrentState(null);
    setError(null);
  }, [blockCode]);


  async function step(deltaMs: number) {
    if (loading || tagIds.length === 0) return;
    setLoading(true);
    setError(null);
    const nextT = simTime + deltaMs / 1000;

    try {
      const input_values = tagIds.map(tid => ({
        tag_id: tid,
        value: inputStates[tid] ? 1.0 : 0.0,
        quality: GOOD_NON_SPECIFIC,
      }));

      const res = await fetch("/api/computed-tags/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          block_type: blockCode,
          block_config: blockConfig,
          input_values,
          state: currentState,
          now: nextT,
        }),
      });

      if (!res.ok) {
        setError(`HTTP ${res.status}: ${await res.text()}`);
        return;
      }

      const data = await res.json();

      if (data.status !== "ok") {
        setError(data.error || data.status);
        return;
      }

      const bits: Record<number, boolean> = {};
      for (const tid of tagIds) bits[tid] = inputStates[tid] ?? false;

      const stepRow: TimelineStep = {
        t: nextT,
        inputBits: bits,
        outputValue: data.value,
        outputQuality: data.quality,
        state: data.new_state,
      };

      setSteps(prev => [...prev, stepRow].slice(-30));
      setCurrentState(data.new_state);
      setSimTime(nextT);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }

  function reset() {
    setSteps([]);
    setSimTime(0);
    setCurrentState(null);
    setInputStates(Object.fromEntries(tagIds.map(t => [t, false])));
    setError(null);
  }

  function toggleInput(tagId: number) {
    setInputStates(prev => ({ ...prev, [tagId]: !prev[tagId] }));
  }


  // --- Render ---

  if (tagIds.length === 0) {
    return (
      <p className="text-[11px] italic text-muted-foreground">
        Configure block inputs above to start the simulator.
      </p>
    );
  }

  const latest = steps[steps.length - 1];
  const outputHigh = latest?.outputValue !== null && (latest?.outputValue ?? 0) > 0;

  return (
    <div className="space-y-2">
      {/* Status row */}
      <div className="flex items-center gap-2 flex-wrap text-[11px]">
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground">Sim time</span>
        <span className="font-mono font-medium">{simTime.toFixed(3)}s</span>
        <span className="text-[10px] uppercase tracking-wider text-muted-foreground ml-2">Output</span>
        <span
          className={`font-mono font-medium px-1.5 py-0.5 rounded
            ${latest === undefined
              ? "text-muted-foreground"
              : outputHigh
              ? "bg-emerald-100 text-emerald-800"
              : "bg-slate-100 text-slate-700"}`}
        >
          {latest === undefined
            ? "—"
            : latest.outputValue === null
            ? "BAD"
            : formatVal(latest.outputValue)}
        </span>
        {loading && <Loader2 className="h-3 w-3 animate-spin text-muted-foreground" />}
      </div>

      {/* Input toggles */}
      <div className="space-y-1">
        {tagIds.map((tid, i) => {
          const label = labels[i] ?? `input${i}`;
          const isHigh = inputStates[tid] ?? false;
          return (
            <button
              key={tid}
              type="button"
              onClick={() => toggleInput(tid)}
              disabled={loading}
              className={`w-full text-left text-[11px] px-2 py-1 rounded border flex items-center gap-2
                transition-colors disabled:opacity-50
                ${isHigh
                  ? "bg-emerald-50 border-emerald-300 text-emerald-900 hover:bg-emerald-100"
                  : "bg-slate-50 border-slate-300 text-slate-600 hover:bg-slate-100"}`}
            >
              <span className="font-mono font-semibold">{label}</span>
              <span className="text-[10px] text-muted-foreground">tag #{tid}</span>
              <span className="ml-auto font-mono font-bold text-[10px]">
                {isHigh ? "HIGH (1)" : "LOW (0)"}
              </span>
            </button>
          );
        })}
      </div>

      {/* Step controls */}
      <div className="flex flex-wrap gap-1">
        <StepBtn onClick={() => step(100)} disabled={loading} label="+100ms" />
        <StepBtn onClick={() => step(500)} disabled={loading} label="+500ms" />
        <StepBtn onClick={() => step(1000)} disabled={loading} label="+1s" />
        <button
          type="button"
          onClick={reset}
          disabled={loading || (steps.length === 0 && simTime === 0)}
          className="text-[11px] px-2 py-1 rounded border border-border bg-card
                     hover:bg-secondary disabled:opacity-50
                     ml-auto inline-flex items-center gap-1"
          title="Reset simulator"
        >
          <RotateCcw className="h-2.5 w-2.5" />
          Reset
        </button>
      </div>

      {error && (
        <div className="text-[11px] text-destructive bg-destructive/10 rounded px-2 py-1
                        border border-destructive/30 break-words">
          {error}
        </div>
      )}

      {/* History */}
      {steps.length > 0 && (
        <details className="text-[11px]" open={steps.length > 0 && steps.length <= 5}>
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground select-none">
            History · {steps.length} step{steps.length !== 1 ? "s" : ""}
          </summary>
          <div className="mt-1 font-mono space-y-0.5 max-h-32 overflow-y-auto
                          bg-card/40 border border-border rounded p-1.5">
            {[...steps].reverse().map((s, idx) => {
              const isHigh = s.outputValue !== null && s.outputValue > 0;
              return (
                <div
                  key={`${s.t}-${idx}`}
                  className="grid grid-cols-[60px_1fr_auto] gap-2 text-[10px]"
                >
                  <span className="text-muted-foreground">{s.t.toFixed(2)}s</span>
                  <span className="truncate">
                    {tagIds.map((tid, i) =>
                      `${labels[i] ?? `in${i}`}=${s.inputBits[tid] ? "1" : "0"}`
                    ).join("  ")}
                  </span>
                  <span className={isHigh ? "text-emerald-700 font-bold" : "text-muted-foreground"}>
                    → {s.outputValue === null ? "null" : formatVal(s.outputValue)}
                  </span>
                </div>
              );
            })}
          </div>
        </details>
      )}

      {/* State inspector */}
      {currentState && Object.keys(currentState).length > 0 && (
        <details className="text-[11px]">
          <summary className="cursor-pointer text-muted-foreground hover:text-foreground select-none">
            Block state
          </summary>
          <pre className="mt-1 font-mono bg-card/40 border border-border rounded p-1.5
                          text-[10px] overflow-x-auto whitespace-pre-wrap break-words">
{JSON.stringify(currentState, null, 2)}
          </pre>
        </details>
      )}
    </div>
  );
}


function StepBtn({
  onClick, disabled, label,
}: { onClick: () => void; disabled: boolean; label: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="text-[11px] px-2 py-1 rounded border border-border bg-card
                 hover:bg-secondary disabled:opacity-50 font-mono"
    >
      Step {label}
    </button>
  );
}


function formatVal(v: number): string {
  if (!Number.isFinite(v)) return String(v);
  if (Number.isInteger(v) && Math.abs(v) < 1e6) return String(v);
  if (Math.abs(v) >= 0.001 && Math.abs(v) < 1e6) {
    return v.toFixed(3).replace(/\.?0+$/, "");
  }
  return v.toExponential(3);
}
