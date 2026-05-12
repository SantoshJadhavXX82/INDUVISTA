/**
 * Phase 7 C2 — Test-read button + decoded matrix.
 *
 * Issues a one-shot read at the configured address and shows the response
 * decoded in every type/byte-order combination. Engineer scans the matrix
 * for the value that matches the device's local display and clicks that
 * cell to apply (data_type, byte_order) to the form.
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Zap, AlertCircle, MousePointerClick } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type TestReadResponse = {
  raw_bytes_hex: string;
  register_count: number;
  function_code: number;
  decoded: Record<string, Record<string, string>>; // {data_type: {byte_order_label: value}}
};

type Props = {
  deviceId: number;
  functionCode: string;
  address: string;
  registerCount: string;
  /** Called when the user clicks a value in the matrix. The chosen
   *  data_type + byte_order code (e.g. "ABCD") get back to the form. */
  onPick?: (dataType: string, byteOrder: string) => void;
};

const BYTE_ORDER_CODES: Record<string, string> = {
  // map the verbose labels back to the 4-char codes the form uses
  "ABCD (big-endian)": "ABCD",
  "CDAB (word-swap)": "CDAB",
  "BADC (byte-swap)": "BADC",
  "DCBA (little-endian)": "DCBA",
  "AB (big-endian)": "ABCD",
  "BA (little-endian)": "DCBA",
  "ABCD…HG (big-endian)": "ABCD",
  "DCBA…FE (little-endian)": "DCBA",
};

export function TestReadPanel({
  deviceId, functionCode, address, registerCount, onPick,
}: Props) {
  const [result, setResult] = useState<TestReadResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const test = useMutation({
    mutationFn: () => {
      const fc = parseInt(functionCode, 10);
      const addr = parseInt(address, 10);
      const cnt = parseInt(registerCount, 10);
      if (isNaN(fc) || isNaN(addr) || isNaN(cnt)) {
        throw new Error("Fill in FC, address, and register count first.");
      }
      // For 16-bit FC reads (1/2), the read returns bits — cnt can be 1+.
      // For register FCs (3/4), we cap at 4 to limit decode matrix size.
      return api.post<TestReadResponse>(
        `/devices/${deviceId}/test-read`,
        {
          function_code: fc,
          address: addr,
          register_count: Math.min(Math.max(cnt, 1), 4),
        },
      );
    },
    onMutate: () => { setError(null); setResult(null); },
    onSuccess: setResult,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const disabled = !address || !functionCode || !registerCount || test.isPending;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => test.mutate()}
          disabled={disabled}
        >
          <Zap className="h-3.5 w-3.5 mr-1.5" />
          {test.isPending ? "Reading…" : "Test read"}
        </Button>
        <span className="text-xs text-muted-foreground">
          One-shot read; click any cell to apply that combination to the form.
        </span>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 flex gap-2">
          <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div className="rounded-md border bg-secondary/30 p-3 space-y-3">
          <div className="text-xs">
            <span className="text-muted-foreground">Raw bytes: </span>
            <code className="font-mono font-medium">{result.raw_bytes_hex}</code>
            <span className="text-muted-foreground ml-2">
              ({result.register_count} register{result.register_count > 1 ? "s" : ""},
              FC{result.function_code})
            </span>
          </div>

          <div className="space-y-2">
            {Object.entries(result.decoded).map(([dataType, byOrder]) => (
              <div key={dataType} className="grid grid-cols-[80px_1fr] gap-2 items-start">
                <div className="text-xs font-medium text-muted-foreground pt-0.5">
                  {dataType}
                </div>
                <div className="grid grid-cols-2 gap-1">
                  {Object.entries(byOrder).map(([label, value]) => {
                    const byteOrderCode = BYTE_ORDER_CODES[label] ?? "ABCD";
                    return (
                      <button
                        key={label}
                        type="button"
                        onClick={() => onPick?.(dataType, byteOrderCode)}
                        className={cn(
                          "text-left px-2 py-1 rounded border text-xs",
                          "hover:border-foreground hover:bg-background transition-colors",
                          "border-input bg-background/60",
                        )}
                        title={`Click to set data_type=${dataType}, byte_order=${byteOrderCode}`}
                      >
                        <div className="text-[10px] text-muted-foreground">{label}</div>
                        <div className="font-mono tabular-nums">{value}</div>
                      </button>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>

          {onPick && (
            <p className="text-[10px] text-muted-foreground flex items-center gap-1">
              <MousePointerClick className="h-3 w-3" />
              Click a cell to apply that data type + byte order
            </p>
          )}
        </div>
      )}
    </div>
  );
}
