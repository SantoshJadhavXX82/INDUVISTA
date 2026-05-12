/**
 * Phase 7 C4 — Register Browser.
 *
 * Scans a contiguous address range on a chosen device and presents each
 * register with every plausible interpretation: raw hex, uint16, int16,
 * ASCII, and float32 (combined with the next address). Engineers use this
 * to discover what's actually at unknown addresses when working from a
 * vendor manual whose map they don't trust yet — or to verify their own
 * tag mappings against the wire data.
 *
 * "Create tag" on any row jumps to the Tag Explorer with form pre-filled
 * for the chosen address + interpretation.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router";
import {
  ScanLine, AlertCircle, Plus, Eye, EyeOff,
} from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { type ScanRangeResponse } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";

type Device = {
  id: number;
  name: string;
  host: string;
  port: number;
  unit_id: number;
};

type ByteOrder = "ABCD" | "CDAB" | "BADC" | "DCBA";

export default function RegisterBrowser() {
  const navigate = useNavigate();

  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [functionCode, setFunctionCode] = useState("3");
  const [startAddr, setStartAddr] = useState("0");
  const [endAddr, setEndAddr] = useState("99");
  const [byteOrder, setByteOrder] = useState<ByteOrder>("ABCD");
  const [hideZeros, setHideZeros] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
  });

  // Default-select first device
  if (selectedDeviceId === null && devices.data && devices.data.length > 0) {
    setSelectedDeviceId(devices.data[0].id);
  }

  const scan = useMutation({
    mutationFn: (params: {
      deviceId: number; fc: number; start: number; end: number;
    }) => api.post<ScanRangeResponse>(
      `/devices/${params.deviceId}/scan-range`,
      {
        function_code: params.fc,
        start_address: params.start,
        end_address: params.end,
      },
    ),
    onMutate: () => setError(null),
    onError: (e: Error) =>
      setError(e instanceof ApiError ? e.detail : e.message),
  });

  const doScan = () => {
    if (selectedDeviceId === null) return;
    const fc = parseInt(functionCode, 10);
    const start = parseInt(startAddr, 10);
    const end = parseInt(endAddr, 10);
    if (isNaN(fc) || isNaN(start) || isNaN(end)) {
      setError("Enter valid FC, start, and end values.");
      return;
    }
    if (end < start) {
      setError("End address must be ≥ start.");
      return;
    }
    if (end - start + 1 > 1000) {
      setError("Range too large — max 1000 addresses per scan.");
      return;
    }
    scan.mutate({ deviceId: selectedDeviceId, fc, start, end });
  };

  // Build display rows. For 16-bit FCs (3/4), each address shows
  // interpretations including a "float32 with next address" column. For bit
  // FCs (1/2), just show the bit value — no multi-type interpretation needed.
  const rows = scan.data?.rows ?? [];
  const fc = scan.data?.function_code ?? 3;
  const isBits = fc === 1 || fc === 2;

  const interpreted = useMemo(() => {
    if (isBits) {
      return rows.map((r) => ({
        ...r,
        uint16: r.value,
        int16: r.value,
        ascii: "—",
        float32: null as number | null,
        int32: null as number | null,
        uint32: null as number | null,
        float64: null as number | null,
      }));
    }
    return rows.map((r, idx) => {
      const next = rows[idx + 1];
      const next2 = rows[idx + 2];
      const next3 = rows[idx + 3];
      let float32: number | null = null;
      let int32: number | null = null;
      let uint32: number | null = null;
      let float64: number | null = null;
      if (next) {
        // Need 2 consecutive registers for any 32-bit interpretation.
        float32 = interpretFloat32(r.value, next.value, byteOrder);
        int32 = interpretInt32(r.value, next.value, byteOrder);
        uint32 = interpretUint32(r.value, next.value, byteOrder);
      }
      if (next && next2 && next3) {
        // Need 4 consecutive registers for float64.
        float64 = interpretFloat64(
          r.value, next.value, next2.value, next3.value, byteOrder,
        );
      }
      return {
        ...r,
        uint16: r.value & 0xFFFF,
        int16: r.value > 0x7FFF ? r.value - 0x10000 : r.value,
        ascii: hexToAscii(r.hex),
        float32, int32, uint32, float64,
      };
    });
  }, [rows, isBits, byteOrder]);

  const displayed = useMemo(() => {
    if (!hideZeros) return interpreted;
    return interpreted.filter((r) => r.value !== 0);
  }, [interpreted, hideZeros]);

  const stats = useMemo(() => {
    const total = interpreted.length;
    const nonZero = interpreted.filter((r) => r.value !== 0).length;
    return { total, nonZero, zero: total - nonZero };
  }, [interpreted]);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight flex items-center gap-2">
          <ScanLine className="h-6 w-6" />
          Register Browser
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Scan a device's address range and discover what data lives there.
        </p>
      </div>

      {/* Scan form */}
      <Card>
        <CardContent className="pt-5 pb-4">
          <div className="grid grid-cols-2 md:grid-cols-6 gap-3">
            <div className="space-y-1.5 col-span-2">
              <Label>Device</Label>
              <select
                value={selectedDeviceId ?? ""}
                onChange={(e) => setSelectedDeviceId(parseInt(e.target.value, 10))}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              >
                {devices.data?.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} — {d.host}:{d.port}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1.5">
              <Label>FC</Label>
              <select
                value={functionCode}
                onChange={(e) => setFunctionCode(e.target.value)}
                className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm"
              >
                <option value="1">1 — Coils</option>
                <option value="2">2 — Discrete in</option>
                <option value="3">3 — Holding</option>
                <option value="4">4 — Input regs</option>
              </select>
            </div>
            <div className="space-y-1.5">
              <Label>From</Label>
              <Input
                type="number" min="0" max="65535"
                value={startAddr}
                onChange={(e) => setStartAddr(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>To</Label>
              <Input
                type="number" min="0" max="65535"
                value={endAddr}
                onChange={(e) => setEndAddr(e.target.value)}
              />
            </div>
            <div className="space-y-1.5">
              <Label>&nbsp;</Label>
              <Button
                onClick={doScan}
                disabled={scan.isPending || selectedDeviceId === null}
                className="w-full"
              >
                {scan.isPending ? "Scanning…" : "Scan"}
              </Button>
            </div>
          </div>
          {error && (
            <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800 flex gap-2">
              <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Results */}
      {scan.data && (
        <>
          <div className="flex flex-wrap items-center gap-4 px-1 text-sm">
            <span className="text-muted-foreground">
              <strong className="text-foreground tabular-nums">{stats.total}</strong> addresses scanned
              {" · "}
              <strong className="text-foreground tabular-nums">{stats.nonZero}</strong> non-zero
              {" · "}
              <strong className="text-foreground tabular-nums">{stats.zero}</strong> zero
              {" · "}
              <span className="tabular-nums">{scan.data.elapsed_ms.toFixed(1)}ms</span> ({scan.data.chunks} chunk{scan.data.chunks !== 1 ? "s" : ""})
            </span>
            <div className="ml-auto flex items-center gap-3">
              {!isBits && (
                <div className="flex items-center gap-2">
                  <Label className="text-xs text-muted-foreground">
                    Multi-register byte order:
                  </Label>
                  <select
                    value={byteOrder}
                    onChange={(e) => setByteOrder(e.target.value as ByteOrder)}
                    className="h-7 rounded-md border border-input bg-background px-2 text-xs"
                  >
                    <option value="ABCD">Big-endian (ABCD)</option>
                    <option value="DCBA">Little-endian (DCBA)</option>
                    <option value="CDAB">Word swap (CDAB)</option>
                    <option value="BADC">Byte swap (BADC)</option>
                  </select>
                </div>
              )}
              <Button
                variant="outline" size="sm"
                onClick={() => setHideZeros(!hideZeros)}
              >
                {hideZeros
                  ? <><Eye className="h-3.5 w-3.5 mr-1.5" />Show all</>
                  : <><EyeOff className="h-3.5 w-3.5 mr-1.5" />Hide zeros</>}
              </Button>
            </div>
          </div>

          <Card>
            <CardContent className="p-0 overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-24">Address</TableHead>
                    <TableHead className="w-24">Modicon</TableHead>
                    <TableHead className="w-20 font-mono">Hex</TableHead>
                    {!isBits && (
                      <>
                        <TableHead className="text-right">uint16</TableHead>
                        <TableHead className="text-right">int16</TableHead>
                        <TableHead className="text-center w-16">ASCII</TableHead>
                        <TableHead className="text-right">int32</TableHead>
                        <TableHead className="text-right">uint32</TableHead>
                        <TableHead className="text-right">float32</TableHead>
                        <TableHead className="text-right">float64</TableHead>
                      </>
                    )}
                    {isBits && <TableHead>Value</TableHead>}
                    <TableHead className="w-20"></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {displayed.length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={isBits ? 4 : 11} className="text-center text-sm text-muted-foreground py-8">
                        {hideZeros
                          ? "All scanned addresses are zero. Toggle 'Show all' to reveal them."
                          : "No rows returned."}
                      </TableCell>
                    </TableRow>
                  ) : (
                    displayed.map((r) => (
                      <TableRow
                        key={r.address}
                        className={cn(
                          r.value === 0 && "opacity-50",
                          r.float32 !== null && isPlausibleFloat(r.float32) && "bg-emerald-50/40",
                        )}
                      >
                        <TableCell className="font-mono tabular-nums">{r.address}</TableCell>
                        <TableCell className="font-mono tabular-nums text-muted-foreground text-xs">
                          {modiconFor(r.address, fc)}
                        </TableCell>
                        <TableCell className="font-mono">{r.hex}</TableCell>
                        {!isBits && (
                          <>
                            <TableCell className="text-right tabular-nums text-sm">{r.uint16}</TableCell>
                            <TableCell className="text-right tabular-nums text-sm">{r.int16}</TableCell>
                            <TableCell className="text-center font-mono text-xs">{r.ascii}</TableCell>
                            <TableCell className="text-right tabular-nums text-sm text-muted-foreground">
                              {r.int32 === null ? "—" : r.int32}
                            </TableCell>
                            <TableCell className="text-right tabular-nums text-sm text-muted-foreground">
                              {r.uint32 === null ? "—" : r.uint32}
                            </TableCell>
                            <TableCell className={cn(
                              "text-right tabular-nums text-sm",
                              r.float32 !== null && isPlausibleFloat(r.float32) && "font-medium",
                            )}>
                              {r.float32 === null ? "—" : formatFloat(r.float32)}
                            </TableCell>
                            <TableCell className={cn(
                              "text-right tabular-nums text-sm",
                              r.float64 !== null && isPlausibleFloat(r.float64) && "font-medium text-foreground",
                            )}>
                              {r.float64 === null ? "—" : formatFloat(r.float64)}
                            </TableCell>
                          </>
                        )}
                        {isBits && (
                          <TableCell className="font-mono">{r.value === 1 ? "TRUE" : "false"}</TableCell>
                        )}
                        <TableCell>
                          <Button
                            variant="ghost" size="sm"
                            className="h-7 px-2 text-xs"
                            onClick={() => navigate(
                              `/tags?create_from=${r.address}&fc=${fc}&byte_order=${byteOrder}&device_id=${selectedDeviceId}`,
                            )}
                            title="Create a tag at this address"
                          >
                            <Plus className="h-3 w-3" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}

      {!scan.data && !scan.isPending && (
        <div className="text-center py-16 text-sm text-muted-foreground">
          <ScanLine className="h-8 w-8 mx-auto mb-3 opacity-30" />
          <p>Pick a device, FC, and address range above, then click Scan.</p>
          <p className="text-xs mt-1">Max 1000 addresses per scan.</p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Multi-register byte order helpers — interpret 2 or 4 consecutive registers
// as a typed numeric value per the user's selected byte ordering.
// ---------------------------------------------------------------------------

/** Apply byte order to a pair of 16-bit registers and return the 4 raw bytes. */
function rearrange32(reg0: number, reg1: number, order: ByteOrder): Uint8Array {
  const b = new Uint8Array(4);
  const a0 = (reg0 >> 8) & 0xFF, b0 = reg0 & 0xFF;
  const c0 = (reg1 >> 8) & 0xFF, d0 = reg1 & 0xFF;
  switch (order) {
    case "ABCD": b[0] = a0; b[1] = b0; b[2] = c0; b[3] = d0; break;
    case "CDAB": b[0] = c0; b[1] = d0; b[2] = a0; b[3] = b0; break;
    case "BADC": b[0] = b0; b[1] = a0; b[2] = d0; b[3] = c0; break;
    case "DCBA": b[0] = d0; b[1] = c0; b[2] = b0; b[3] = a0; break;
  }
  return b;
}

/** Apply byte order to 4 consecutive 16-bit registers, return the 8 raw bytes.
 *  Same four canonical transformations as 32-bit; the byte sequence each
 *  produces:
 *    ABCD → A B C D E F G H  (big-endian)
 *    CDAB → G H E F C D A B  (word swap)
 *    BADC → B A D C F E H G  (byte swap)
 *    DCBA → H G F E D C B A  (little-endian)
 */
function rearrange64(
  reg0: number, reg1: number, reg2: number, reg3: number,
  order: ByteOrder,
): Uint8Array {
  const b = new Uint8Array(8);
  const A = (reg0 >> 8) & 0xFF, B = reg0 & 0xFF;
  const C = (reg1 >> 8) & 0xFF, D = reg1 & 0xFF;
  const E = (reg2 >> 8) & 0xFF, F = reg2 & 0xFF;
  const G = (reg3 >> 8) & 0xFF, H = reg3 & 0xFF;
  switch (order) {
    case "ABCD":
      b[0]=A; b[1]=B; b[2]=C; b[3]=D; b[4]=E; b[5]=F; b[6]=G; b[7]=H; break;
    case "CDAB":
      b[0]=G; b[1]=H; b[2]=E; b[3]=F; b[4]=C; b[5]=D; b[6]=A; b[7]=B; break;
    case "BADC":
      b[0]=B; b[1]=A; b[2]=D; b[3]=C; b[4]=F; b[5]=E; b[6]=H; b[7]=G; break;
    case "DCBA":
      b[0]=H; b[1]=G; b[2]=F; b[3]=E; b[4]=D; b[5]=C; b[6]=B; b[7]=A; break;
  }
  return b;
}

/** Apply byte-order to a pair of 16-bit registers and return float32. */
function interpretFloat32(reg0: number, reg1: number, order: ByteOrder): number {
  return new DataView(rearrange32(reg0, reg1, order).buffer).getFloat32(0, false);
}

function interpretInt32(reg0: number, reg1: number, order: ByteOrder): number {
  return new DataView(rearrange32(reg0, reg1, order).buffer).getInt32(0, false);
}

function interpretUint32(reg0: number, reg1: number, order: ByteOrder): number {
  return new DataView(rearrange32(reg0, reg1, order).buffer).getUint32(0, false);
}

function interpretFloat64(
  reg0: number, reg1: number, reg2: number, reg3: number,
  order: ByteOrder,
): number {
  return new DataView(rearrange64(reg0, reg1, reg2, reg3, order).buffer)
    .getFloat64(0, false);
}

/** Distinguish plausible engineering values from byte-order garbage. */
function isPlausibleFloat(f: number): boolean {
  if (!isFinite(f) || isNaN(f)) return false;
  const a = Math.abs(f);
  if (a === 0) return false;
  return a >= 1e-3 && a < 1e6;
}

function formatFloat(f: number): string {
  if (!isFinite(f) || isNaN(f)) return "—";
  const a = Math.abs(f);
  if (a === 0) return "0";
  if (a < 1e-3 || a > 1e6) return f.toExponential(2);
  if (a >= 1000) return f.toFixed(2);
  if (a >= 1) return f.toFixed(4);
  return f.toFixed(6);
}

function hexToAscii(hex: string): string {
  const bytes = hex.trim().split(/\s+/).map((s) => parseInt(s, 16));
  return bytes
    .map((b) => (b >= 32 && b <= 126 ? String.fromCharCode(b) : "·"))
    .join("");
}

function modiconFor(addr: number, fc: number): string {
  const base: Record<number, number> = { 1: 1, 2: 10001, 3: 40001, 4: 30001 };
  return String((base[fc] ?? 40001) + addr);
}
