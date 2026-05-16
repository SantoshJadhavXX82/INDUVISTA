/**
 * Phase 14.4 — Shelve dialog.
 *
 * Simple overlay (no shadcn Dialog dependency) for picking a shelve
 * duration. Duration presets cover common operator scenarios:
 *   15 min   — quick mute during a known disturbance
 *   1 hr     — extended troubleshooting window
 *   4 hr     — half-shift
 *   8 hr     — one operator shift
 *   24 hr    — full day (mute through a planned changeover)
 *   Custom   — anything else, in minutes
 *
 * Backdrop click and Escape both close. Apply triggers a parent-supplied
 * onConfirm with the chosen minutes + comment.
 */
import { useEffect, useState } from "react";
import { Pause, X, AlertTriangle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

interface Props {
  open: boolean;
  ruleLabel: string;             // e.g. "Pressure-101 (High)"
  onConfirm: (durationMinutes: number, comment: string | null) => void;
  onCancel: () => void;
  pending?: boolean;
  error?: string | null;
}

interface Preset {
  label: string;
  minutes: number;
}

const PRESETS: Preset[] = [
  { label: "15 min", minutes: 15 },
  { label: "1 hr",   minutes: 60 },
  { label: "4 hr",   minutes: 240 },
  { label: "8 hr",   minutes: 480 },
  { label: "24 hr",  minutes: 1440 },
];

export default function ShelveDialog({
  open, ruleLabel, onConfirm, onCancel, pending, error,
}: Props) {
  const [selected, setSelected] = useState<number>(60);
  const [custom, setCustom] = useState<string>("");
  const [comment, setComment] = useState<string>("");

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [open, onCancel]);

  // Reset state when re-opened so a previous custom value doesn't leak
  useEffect(() => {
    if (open) {
      setSelected(60);
      setCustom("");
      setComment("");
    }
  }, [open]);

  if (!open) return null;

  const isCustom = selected === -1;
  const effective = isCustom ? parseInt(custom, 10) : selected;
  const valid = isFinite(effective) && effective >= 1 && effective <= 43_200;

  const handleConfirm = () => {
    if (!valid) return;
    onConfirm(effective, comment.trim() || null);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onCancel}
    >
      <div
        className="bg-card border border-border rounded-lg shadow-xl w-full max-w-md mx-4"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <div className="flex items-center gap-2">
            <Pause className="h-4 w-4 text-indigo-600" />
            <h3 className="text-sm font-medium">Shelve alarm</h3>
          </div>
          <button type="button" onClick={onCancel}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Body */}
        <div className="p-4 space-y-4">
          <div className="text-xs text-muted-foreground">
            Muting <strong className="text-foreground">{ruleLabel}</strong>.
            The evaluator will skip this rule until the shelve expires.
            You can unshelve early from the Shelved tab.
          </div>

          {/* Duration presets */}
          <div>
            <div className="text-xs text-muted-foreground mb-1.5">Duration</div>
            <div className="flex flex-wrap gap-1.5">
              {PRESETS.map((p) => (
                <button
                  key={p.minutes}
                  type="button"
                  onClick={() => setSelected(p.minutes)}
                  className={`px-2.5 py-1 text-xs rounded border ${
                    selected === p.minutes
                      ? "bg-indigo-50 text-indigo-800 border-indigo-300 font-medium"
                      : "bg-card text-foreground border-border hover:bg-secondary/40"
                  }`}
                >
                  {p.label}
                </button>
              ))}
              <button
                type="button"
                onClick={() => setSelected(-1)}
                className={`px-2.5 py-1 text-xs rounded border ${
                  isCustom
                    ? "bg-indigo-50 text-indigo-800 border-indigo-300 font-medium"
                    : "bg-card text-foreground border-border hover:bg-secondary/40"
                }`}
              >
                Custom
              </button>
            </div>
            {isCustom && (
              <div className="mt-2 flex items-center gap-2">
                <Input
                  type="number"
                  inputMode="numeric"
                  value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                  placeholder="minutes"
                  className="h-8 text-xs w-32"
                  min={1}
                  max={43200}
                  autoFocus
                />
                <span className="text-[10px] text-muted-foreground">
                  minutes (max 30 days)
                </span>
              </div>
            )}
          </div>

          {/* Comment */}
          <div>
            <label className="text-xs text-muted-foreground block mb-1">
              Comment (optional)
            </label>
            <Input
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="e.g. planned changeover, working on PT-101"
              maxLength={500}
              className="h-8 text-xs"
            />
          </div>

          {error && (
            <div className="flex items-start gap-2 text-xs text-destructive">
              <AlertTriangle className="h-4 w-4 flex-shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          <Button variant="outline" size="sm" className="h-7 text-xs"
                  onClick={onCancel} disabled={pending}>
            Cancel
          </Button>
          <Button size="sm" className="h-7 text-xs gap-1"
                  onClick={handleConfirm} disabled={!valid || pending}>
            <Pause className="h-3 w-3" />
            {pending ? "Shelving…" : `Shelve for ${valid ? effective : "?"} min`}
          </Button>
        </div>
      </div>
    </div>
  );
}
