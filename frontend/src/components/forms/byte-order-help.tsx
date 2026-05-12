/**
 * Phase 7 C3 — Byte-order glossary.
 *
 * Tiny ? icon next to the byte-order dropdown. Click/focus shows a popover
 * with vendor-friendly explanations. Uses <details>/<summary> for keyboard
 * accessibility and zero dependencies.
 */
import { HelpCircle } from "lucide-react";

export function ByteOrderHelp() {
  return (
    <details className="relative inline-block ml-1 align-middle">
      <summary
        className="cursor-pointer list-none text-muted-foreground hover:text-foreground"
        aria-label="Byte-order help"
      >
        <HelpCircle className="h-3.5 w-3.5 inline" />
      </summary>
      <div className="absolute left-0 top-full mt-1 z-20 w-72 p-3 bg-background border rounded shadow-lg text-xs space-y-1.5">
        <div className="font-medium text-sm mb-1">Byte order naming</div>
        <div className="grid grid-cols-[60px_1fr] gap-x-2 gap-y-1">
          <code className="font-mono font-medium">ABCD</code>
          <span>Big-endian, MSW first. Most modern devices, IEEE-754 default.</span>
          <code className="font-mono font-medium">CDAB</code>
          <span>Little-endian word-swap. Common in legacy PLCs and many Schneider devices.</span>
          <code className="font-mono font-medium">BADC</code>
          <span>Big-endian byte-swap. Rare; some specialised serial gateways.</span>
          <code className="font-mono font-medium">DCBA</code>
          <span>Little-endian. Some Allen-Bradley / Rockwell controllers.</span>
        </div>
        <p className="text-muted-foreground pt-1.5 border-t">
          Unsure? Use the <strong>Test read</strong> button — it shows the value
          decoded in all four orders so you can pick the right one by eye.
        </p>
      </div>
    </details>
  );
}
