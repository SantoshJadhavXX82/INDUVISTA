# Phase 13.12 — ROC in Trend · integration guide

## What's in this drop

| File | Action | Purpose |
|---|---|---|
| `frontend/src/lib/trendRoc.ts` | **NEW — drop in** | Computation, formatting, localStorage helpers |
| `frontend/src/components/RocUnitSelector.tsx` | **NEW — drop in** | Segmented `/s` `/min` `/hr` toggle |
| `frontend/tests-e2e/trend-roc.spec.ts` | **NEW — drop in** | Playwright smoke test |
| `frontend/src/components/TrendSummaryPanel.tsx` | **MODIFY** (snippets below) | Adds ROC column + header selector |
| `frontend/src/components/LiveValuePanel.tsx` | **MODIFY** (snippets below) | Adds ROC line under live value |

No backend changes. No new dependencies. No new database migration.

---

## 1 · TrendSummaryPanel.tsx — five small additions

### (a) Imports

Near the top, alongside your existing imports:

```tsx
import { useState, useMemo } from "react"; // useMemo may already be imported
import {
  computeROC,
  formatROC,
  loadRocUnit,
  saveRocUnit,
  type RocUnit,
  type RocSample,
} from "@/lib/trendRoc";
import RocUnitSelector from "./RocUnitSelector";
```

### (b) State — once per panel, near your existing useState calls

```tsx
const [rocUnit, setRocUnit] = useState<RocUnit>(() => loadRocUnit());

const handleRocUnitChange = (u: RocUnit) => {
  setRocUnit(u);
  saveRocUnit(u);
};
```

### (c) Header — render the selector next to other controls

Wherever the panel header sits (the row above the per-tag stats), add:

```tsx
<RocUnitSelector value={rocUnit} onChange={handleRocUnitChange} />
```

### (d) Per-tag ROC computation — inside whatever loop renders one row per tag

You'll already have the tag's samples in scope (the array driving μ / σ / range). Convert it once per tag and memoise:

```tsx
const rocSamples: RocSample[] = useMemo(
  () =>
    (tagHistory?.ts ?? []).map((t, i) => ({
      t,
      v: tagHistory.values[i],
      q: tagHistory.quality?.[i],
    })),
  [tagHistory],
);

const roc = useMemo(
  () => computeROC(rocSamples, rocUnit),
  [rocSamples, rocUnit],
);
```

Adjust the field names (`tagHistory.ts`, `.values`, `.quality`) to match whatever you actually destructure from `/trends/history`. If the panel already uses `samples`/`values`/`quality`, just swap accordingly.

### (e) Render the cell

Add a new column header (`ROC`) and a cell. Neutral colour — sign tinting waits for Phase 14.10:

```tsx
<td
  className="text-right tabular-nums text-slate-700"
  data-roc-cell={tag.id}
  title={
    roc.isValid
      ? `${roc.samplesUsed} samples over ${roc.windowSec.toFixed(0)}s`
      : "Not enough good samples in the last 5 min"
  }
>
  {formatROC(roc, tag.eu)}
</td>
```

The `data-roc-cell={tag.id}` attribute is what the Playwright smoke uses to find cells — keep it on the element that actually renders the value text.

---

## 2 · LiveValuePanel.tsx — three small additions

### (a) Imports

```tsx
import { useState, useMemo } from "react";
import {
  computeROC,
  formatROC,
  loadRocUnit,
  type RocSample,
} from "@/lib/trendRoc";
```

(LiveValuePanel doesn't need the selector — it reads the unit the summary panel already wrote to localStorage. If you'd rather keep the unit in React state and pass it down via props from `Trend.tsx`, that's cleaner; below assumes the simpler localStorage-read approach.)

### (b) Read the unit

Inside the component body:

```tsx
const rocUnit = useMemo(() => loadRocUnit(), []);
```

For per-tile reactivity to unit changes, lift `rocUnit` to `Trend.tsx` and pass it as a prop instead of reading it once on mount. Either works; the prop version is preferred for Phase 14 where the σ-popover sliders will also depend on the unit.

### (c) Per-tile ROC display

Inside the tile-render loop, after the live value line:

```tsx
const rocSamples: RocSample[] = useMemo(
  () =>
    (recentHistory?.ts ?? []).map((t, i) => ({
      t,
      v: recentHistory.values[i],
      q: recentHistory.quality?.[i],
    })),
  [recentHistory],
);
const roc = useMemo(() => computeROC(rocSamples, rocUnit), [rocSamples, rocUnit]);

return (
  <div className="...">
    {/* existing live value line */}
    <div className="text-xs text-slate-500 mt-0.5" data-live-roc={tag.id}>
      ROC: <span className="font-medium text-slate-700">{formatROC(roc, tag.eu)}</span>
    </div>
  </div>
);
```

`recentHistory` here is whatever rolling buffer the live tile already keeps (last ~60s of Valkey-streamed values). If the tile only has the current value and not a buffer, ROC will always show `—` until you accumulate samples — that's the correct behaviour.

---

## 3 · Running the smoke test

```powershell
cd D:\INDUVISTA\frontend
npx playwright test trend-roc.spec.ts
npx playwright show-report ..\test-results-ui\playwright-report
```

Expected: **7 PASS / 0 FAIL** across four describe blocks.

If the math-sanity test (Group 4) fails, you have a real regression in either `lib/trendRoc.ts` or the bundle's module resolution — start there. If only the persistence test (Group 3) fails, check that `saveRocUnit` is being called from `handleRocUnitChange` and not just `setRocUnit`.

---

## 4 · Quick visual verification (1 min)

1. Open `/trend`, add a couple of tags
2. ROC column should appear in the summary panel with values like `+0.42 °C/min` or `—`
3. Click `/hr` in the header selector — every cell should switch suffix to `/hr` and the numbers should rescale by ×60
4. Reload the page — the `/hr` selection should still be active
5. Open DevTools → Application → Local Storage; you should see `induvista.rocUnit: "/hr"`

That's the whole feature.

---

## What this sets up for Phase 14

The `computeROC` slope is the same number a future ROC alarm rule will compare against its threshold. When 14.10 lands and the σ popover grows drag-to-set sliders, the slider for an `roc_pos` / `roc_neg` rule type just reads the current displayed ROC value as its starting point — operators set the threshold against what they're already seeing. Same unit selection, same window, same quality filter.

The `data-roc-cell` attributes also become the natural anchor point for future "alarm armed" badges (a small ⚠ in the cell when the rule is enabled, red when active). No layout rework needed when Phase 14 ships.
