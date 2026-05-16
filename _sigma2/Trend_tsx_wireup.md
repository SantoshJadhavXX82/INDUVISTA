# Trend.tsx wire-up

Add three things to `frontend/src/pages/Trend.tsx`:

## 1. State + ref at the top of the component

```ts
import { useRef, useState, useCallback } from "react";

// ... inside the Trend component:
const rawTableRef = useRef<HTMLDivElement>(null);
const [rawTableFocusTagId, setRawTableFocusTagId] = useState<number | null>(null);

const handleShowInRawTable = useCallback((tagId: number) => {
  setRawTableFocusTagId(tagId);
  // Defer scroll one frame so RawDataTable can react (auto-expand,
  // re-render with filter) before we scroll into view.
  requestAnimationFrame(() => {
    rawTableRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
}, []);
```

## 2. Pass the callback into TrendSummaryPanel

```tsx
<TrendSummaryPanel
  tagIds={selectedTagIds}
  start={range.start}
  end={range.end}
  onShowInRawTable={handleShowInRawTable}
/>
```

## 3. Wrap the RawDataTable in a ref'd div, and pass focusTagId

```tsx
<div ref={rawTableRef}>
  <RawDataTable
    tagIds={selectedTagIds}
    start={range.start}
    end={range.end}
    focusTagId={rawTableFocusTagId}
    onClearFocus={() => setRawTableFocusTagId(null)}
  />
</div>
```

## 4. Update RawDataTable to react to focusTagId (optional but recommended)

Inside `RawDataTable.tsx`:

- Accept `focusTagId?: number | null` and `onClearFocus?: () => void` props
- Auto-expand the table when `focusTagId` becomes non-null
- Filter visible rows to that tag (or sort + scroll to its rows)
- Render a dismissible badge: `Showing only: <tag name> [×]` whose × calls `onClearFocus`

This is two extra useEffects + a filter line on the rows array. If you'd
rather just scroll without filtering, omit step 4 entirely - scroll alone
gets the operator close enough.

---

After applying steps 1-3, hover any σ value → see the popover with pin
button → click pin → "View this tag in the raw data table" becomes
clickable → click it → page smooth-scrolls down to the table.
