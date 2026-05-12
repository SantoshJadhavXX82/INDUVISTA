/**
 * Phase 7 C1 — Live address translator.
 *
 * Shown below the address input on tag forms. Two jobs:
 *   1. When the user types a clearly-Modicon value (>=10000 with a known
 *      prefix), surface that and offer a one-click "convert to PDU".
 *   2. When the user types a normal PDU value, show the Modicon equivalent
 *      for cross-referencing against vendor manuals.
 *
 * Internal storage stays PDU (0-based) — this is purely UX.
 */
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

type Props = {
  /** Raw user input as string (allow empty/non-numeric without crashing). */
  address: string;
  /** Currently-selected function code (1..4). */
  functionCode: number;
  /** Callback when the user clicks "convert to PDU". Receives the PDU
   *  number to store AND optionally the FC implied by the Modicon prefix. */
  onConvert?: (pduAddress: number, impliedFc: number) => void;
};

export function AddressHelper({ address, functionCode, onConvert }: Props) {
  const num = parseInt(address, 10);
  if (isNaN(num) || num < 0) return null;

  // Modicon prefixes occupy the 10000+ ranges. PDU addresses 1-9999 overlap
  // with Modicon Coils (1-9999) but that's an edge case — assume PDU below
  // 10000 since most fiscal/process devices use 7000s/8000s in PDU space.
  const modicon = detectModicon(num);
  if (modicon && num >= 10000) {
    return (
      <div
        className={cn(
          "mt-1 text-xs flex items-center gap-2 flex-wrap",
          "text-amber-800 bg-amber-50 border border-amber-200 rounded px-2 py-1.5",
        )}
      >
        <span>
          <strong>{num}</strong> looks like Modicon style ({modicon.fcName}, 1-based).
          Wire address would be <strong>{modicon.pduAddress}</strong>.
        </span>
        {onConvert && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="h-6 text-xs"
            onClick={() => onConvert(modicon.pduAddress, modicon.fc)}
          >
            Use {modicon.pduAddress}{modicon.fc !== functionCode && `, FC${modicon.fc}`}
          </Button>
        )}
      </div>
    );
  }

  // Normal PDU range — show the Modicon equivalent so engineers can cross
  // reference manuals that quote 5-digit Modicon addresses.
  const equiv = pduToModicon(num, functionCode);
  return (
    <p className="mt-1 text-xs text-muted-foreground">
      <span className="opacity-80">PDU (on-the-wire) · </span>
      <span className="opacity-80">Modicon equivalent: </span>
      <span className="font-mono">{equiv}</span>
    </p>
  );
}

// --------------------------------------------------------------------------

const MODICON_BASES: Record<number, { base: number; name: string }> = {
  1: { base: 1, name: "FC1 coils" },
  2: { base: 10001, name: "FC2 discrete inputs" },
  3: { base: 40001, name: "FC3 holding registers" },
  4: { base: 30001, name: "FC4 input registers" },
};

function detectModicon(n: number): { pduAddress: number; fc: number; fcName: string } | null {
  if (n >= 1 && n <= 9999) {
    return { pduAddress: n - 1, fc: 1, fcName: "FC1 coils" };
  }
  if (n >= 10001 && n <= 19999) {
    return { pduAddress: n - 10001, fc: 2, fcName: "FC2 discrete inputs" };
  }
  if (n >= 30001 && n <= 39999) {
    return { pduAddress: n - 30001, fc: 4, fcName: "FC4 input registers" };
  }
  if (n >= 40001 && n <= 49999) {
    return { pduAddress: n - 40001, fc: 3, fcName: "FC3 holding registers" };
  }
  return null;
}

function pduToModicon(pdu: number, fc: number): string {
  const entry = MODICON_BASES[fc] ?? MODICON_BASES[3];
  return String(entry.base + pdu);
}
