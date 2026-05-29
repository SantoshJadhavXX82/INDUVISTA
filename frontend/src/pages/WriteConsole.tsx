/**
 * Phase 8.5.1 — Modbus Write Console.
 *
 * Interactive write panel. A tag appears here when it is:
 *   1. enabled
 *   2. on FC 1 (Coil) or FC 3 (Holding Register) — protocol-level constraint
 *   3. tag.writable = true                       — explicit per-tag opt-in
 *   4. block.writable = true (when blocked)      — parent block must allow
 *
 * The write FC (5/6/15/16) is derived automatically from register_count
 * and never shown in the UI — engineers think in areas ("Coil",
 * "Holding Register"), not raw function codes. Engineers wanting wire-
 * level detail use the Frame Inspector.
 *
 * Device tabs at the top filter the table to one device at a time —
 * mirrors Tag Explorer's pattern. Locale-aware timestamps via format.ts.
 */
import { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Search,
  Zap,
  CheckCircle2,
  XCircle,
  Loader2,
  AlertTriangle,
  Info,
  Settings,
} from "lucide-react";
import { Link } from "react-router";
import { api, ApiError } from "@/lib/api";
import { type LiveTag } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Gate } from "@/lib/rbac";
import {
  Table,
  TableHeader,
  TableBody,
  TableRow,
  TableHead,
  TableCell,
} from "@/components/ui/table";
import { DeviceTabs } from "@/components/ui/device-tabs";
import { cn } from "@/lib/utils";

type WriteResponse = {
  success: boolean;
  error: string | null;
  function_code: number | null;
  latency_ms: number | null;
  verify_value: string | null;
  journal_id: number | null;
};

type WriteState = {
  pending: boolean;
  result: WriteResponse | null;
  at: number;
};

function areaLabel(fc: number): string {
  switch (fc) {
    case 1: return "Coil";
    case 2: return "Discrete Input";
    case 3: return "Holding Register";
    case 4: return "Input Register";
    default: return `FC ${fc}`;
  }
}

function formatLiveValue(t: LiveTag): string {
  if (t.value_text != null) return t.value_text;
  if (t.value_double == null) return "—";
  if (t.data_type === "bool") return t.value_double ? "true" : "false";
  if (t.data_type.startsWith("float")) return t.value_double.toFixed(4);
  return String(Math.round(t.value_double));
}

/** A tag is writable in the Write Console sense iff all four conditions hold. */
function isWritable(t: LiveTag): boolean {
  if (!t.enabled) return false;
  if (t.function_code !== 1 && t.function_code !== 3) return false;
  if (!t.writable) return false;
  // block_writable is null for unblocked writable tags — that's fine
  if (t.block_writable === false) return false;
  return true;
}

export default function WriteConsole() {
  const qc = useQueryClient();
  const [search, setSearch] = useState("");
  const [verify, setVerify] = useState(true);
  const [selectedDeviceId, setSelectedDeviceId] = useState<number | null>(null);
  const [pendingValues, setPendingValues] = useState<Record<number, string>>({});
  const [writeStates, setWriteStates] = useState<Record<number, WriteState>>({});

  const tags = useQuery({
    queryKey: ["live"],
    queryFn: () => api.get<LiveTag[]>("/live"),
    refetchInterval: 2000,
    staleTime: 1000,
  });

  // All writable tags (Phase 8.5.1 — both writable AND block_writable required)
  const allWritable = useMemo(
    () => (tags.data ?? []).filter(isWritable),
    [tags.data],
  );

  // Device list derived from writable tags — empty-device tabs are filtered out
  const deviceList = useMemo(() => {
    const m = new Map<number, string>();
    allWritable.forEach((t) => m.set(t.device_id, t.device_name));
    return Array.from(m, ([id, name]) => ({ id, name })).sort((a, b) =>
      a.name.localeCompare(b.name),
    );
  }, [allWritable]);

  const deviceCounts = useMemo(() => {
    const c: Record<number | "all", number> = { all: allWritable.length };
    for (const t of allWritable) {
      c[t.device_id] = (c[t.device_id] ?? 0) + 1;
    }
    return c;
  }, [allWritable]);

  // Apply device filter, then search filter
  const filtered = useMemo(() => {
    let list = allWritable;
    if (selectedDeviceId !== null) {
      list = list.filter((t) => t.device_id === selectedDeviceId);
    }
    const q = search.trim().toLowerCase();
    if (q) {
      list = list.filter(
        (t) =>
          t.tag_name.toLowerCase().includes(q) ||
          t.device_name.toLowerCase().includes(q) ||
          String(t.address).includes(q),
      );
    }
    return list;
  }, [allWritable, selectedDeviceId, search]);

  const writeMutation = useMutation({
    mutationFn: async ({ tagId, value }: { tagId: number; value: string }) => {
      return api.post<WriteResponse>(`/tags/${tagId}/write`, { value, verify });
    },
    onMutate: ({ tagId }) => {
      setWriteStates((s) => ({
        ...s,
        [tagId]: { pending: true, result: null, at: Date.now() },
      }));
    },
    onSuccess: (result, { tagId }) => {
      setWriteStates((s) => ({
        ...s,
        [tagId]: { pending: false, result, at: Date.now() },
      }));
      qc.invalidateQueries({ queryKey: ["live"] });
      // Also refresh the audit journal in case the user pops over to it
      qc.invalidateQueries({ queryKey: ["writes"] });
    },
    onError: (err: Error, { tagId }) => {
      const errResult: WriteResponse = {
        success: false,
        error: err instanceof ApiError ? err.detail : err.message,
        function_code: null,
        latency_ms: null,
        verify_value: null,
        journal_id: null,
      };
      setWriteStates((s) => ({
        ...s,
        [tagId]: { pending: false, result: errResult, at: Date.now() },
      }));
    },
  });

  function handleWrite(tagId: number) {
    const value = pendingValues[tagId]?.trim();
    if (!value) return;
    writeMutation.mutate({ tagId, value });
  }

  // Empty state when zero writable tags configured anywhere
  const noWritableAnywhere = !tags.isLoading && allWritable.length === 0;

  return (
    <div className="space-y-4 max-w-7xl mx-auto">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Write Console</h1>
        <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
          Send writes to Coils and Holding Registers that have been explicitly
          marked Writable. Discrete Inputs and Input Registers are read-only
          per the Modbus spec and don't appear here. The system picks the
          right write function code based on register count — Frame Inspector
          shows the wire-level detail if you need it.
        </p>
      </header>

      {noWritableAnywhere && (
        <Card>
          <CardContent className="p-6 text-center space-y-3">
            <Info className="h-8 w-8 text-muted-foreground mx-auto" />
            <div className="space-y-1">
              <p className="text-sm font-medium">No writable tags configured.</p>
              <p className="text-xs text-muted-foreground max-w-md mx-auto">
                Mark a Register Block as <span className="font-mono">Read+Write</span> in
                Configuration → Register Blocks, then mark individual tags as
                Writable in their tag editor.
              </p>
            </div>
            <div className="flex gap-2 justify-center pt-1">
              <Button asChild size="sm" variant="secondary">
                <Link to="/config/blocks">
                  <Settings className="h-3.5 w-3.5 mr-1.5" />
                  Configure Blocks
                </Link>
              </Button>
              <Button asChild size="sm" variant="secondary">
                <Link to="/tags">Open Tag Explorer</Link>
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {!noWritableAnywhere && (
        <>
          {/* Device tabs + filters */}
          <DeviceTabs
            devices={deviceList}
            value={selectedDeviceId}
            onChange={setSelectedDeviceId}
            counts={deviceCounts}
          />

          <Card>
            <CardContent className="p-3 space-y-3">
              <div className="flex flex-wrap items-end gap-3">
                <div className="flex-1 min-w-[280px] max-w-md">
                  <Label className="text-xs">Search</Label>
                  <div className="relative">
                    <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                    <Input
                      type="text"
                      placeholder="Tag name, device, or address…"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      className="pl-8 h-9"
                    />
                  </div>
                </div>

                <label className="flex items-center gap-2 text-sm cursor-pointer pb-2">
                  <input
                    type="checkbox"
                    checked={verify}
                    onChange={(e) => setVerify(e.target.checked)}
                    className="h-4 w-4"
                  />
                  <span>Verify (read-back after write)</span>
                </label>

                <div className="text-xs text-muted-foreground tabular-nums pb-2 ml-auto">
                  {filtered.length} of {allWritable.length}
                </div>
              </div>

              {!verify && (
                <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-800 flex items-center gap-2">
                  <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
                  Verify is off — writes will not be confirmed by a read-back.
                  The audit journal will still record the request, but the
                  "After" column will be empty.
                </div>
              )}
            </CardContent>
          </Card>

          {/* Empty-filtered state */}
          {filtered.length === 0 && (
            <Card>
              <CardContent className="p-6 text-sm text-muted-foreground text-center">
                No writable tags match this filter.
              </CardContent>
            </Card>
          )}

          {/* Write table */}
          {filtered.length > 0 && (
            <Card>
              <CardContent className="p-0">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Tag</TableHead>
                      <TableHead>Device</TableHead>
                      <TableHead className="w-[120px]">Type</TableHead>
                      <TableHead className="w-[180px]">Access</TableHead>
                      <TableHead className="text-right w-[90px]">Addr</TableHead>
                      <TableHead className="w-[140px]">Current</TableHead>
                      <TableHead className="w-[200px]">New value</TableHead>
                      <TableHead className="w-[100px]">Action</TableHead>
                      <TableHead className="w-[140px]">Last write</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {filtered.map((t) => {
                      const state = writeStates[t.tag_id];
                      const pending = state?.pending ?? false;
                      const isCoil = t.function_code === 1;
                      return (
                        <TableRow key={t.tag_id}>
                          <TableCell className="text-xs font-medium">
                            {t.tag_name}
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground">
                            {t.device_name}
                          </TableCell>
                          <TableCell className="text-xs font-mono">
                            {t.data_type}
                          </TableCell>
                          <TableCell>
                            <span
                              className={cn(
                                "inline-flex items-center gap-1.5 text-xs",
                              )}
                            >
                              <span
                                className={cn(
                                  "inline-block px-1.5 py-0.5 rounded text-[10px] font-medium",
                                  isCoil
                                    ? "bg-blue-100 text-blue-800"
                                    : "bg-purple-100 text-purple-800",
                                )}
                              >
                                {areaLabel(t.function_code)}
                              </span>
                              <span className="text-[10px] uppercase tracking-wide text-green-700 font-semibold">
                                R+W
                              </span>
                            </span>
                          </TableCell>
                          <TableCell className="text-right tabular-nums text-xs font-mono">
                            {t.address}
                            {t.register_count > 1 && (
                              <span className="text-muted-foreground">
                                +{t.register_count - 1}
                              </span>
                            )}
                          </TableCell>
                          <TableCell className="text-xs font-mono">
                            <span
                              className={cn(
                                (t.st ?? 0) >= 128
                                  ? "text-foreground"
                                  : "text-amber-700",
                              )}
                            >
                              {formatLiveValue(t)}
                            </span>
                          </TableCell>
                          <TableCell>
                            <Input
                              type="text"
                              placeholder={
                                t.data_type === "bool"
                                  ? "true / false / 1 / 0"
                                  : "value"
                              }
                              value={pendingValues[t.tag_id] ?? ""}
                              onChange={(e) =>
                                setPendingValues((p) => ({
                                  ...p,
                                  [t.tag_id]: e.target.value,
                                }))
                              }
                              onKeyDown={(e) => {
                                if (e.key === "Enter") handleWrite(t.tag_id);
                              }}
                              disabled={pending}
                              className="h-8 text-xs font-mono"
                            />
                          </TableCell>
                          <TableCell>
                            <Gate cap="operate" mode="disable">
                            <Button
                              size="sm"
                              variant="secondary"
                              disabled={pending || !pendingValues[t.tag_id]?.trim()}
                              onClick={() => handleWrite(t.tag_id)}
                              className="h-8 px-3"
                            >
                              {pending ? (
                                <Loader2 className="h-3 w-3 animate-spin" />
                              ) : (
                                <>
                                  <Zap className="h-3 w-3 mr-1" />
                                  Write
                                </>
                              )}
                            </Button>
                            </Gate>
                          </TableCell>
                          <TableCell>
                            <WriteResultBadge state={state} />
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}

          <Card>
            <CardContent className="p-3 text-xs text-muted-foreground flex items-start gap-2">
              <Info className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <div>
                Every write — successful or failed — is recorded in the audit
                journal at <Link to="/modbus/write-audit" className="underline">
                Modbus → Write Audit</Link>, including the value the system
                most recently believed was there (Before), the value the
                user requested, and the post-write verify read (After).
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function WriteResultBadge({ state }: { state: WriteState | undefined }) {
  if (!state) return <span className="text-xs text-muted-foreground">—</span>;
  if (state.pending) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" /> writing…
      </span>
    );
  }
  if (!state.result) return <span className="text-xs text-muted-foreground">—</span>;
  const r = state.result;
  if (r.success) {
    return (
      <span
        className="inline-flex items-center gap-1 text-xs text-green-700"
        title={
          r.verify_value != null
            ? `Verify: ${r.verify_value} · ${r.latency_ms?.toFixed(0)}ms · #${r.journal_id}`
            : `${r.latency_ms?.toFixed(0)}ms · #${r.journal_id}`
        }
      >
        <CheckCircle2 className="h-3.5 w-3.5" />
        OK{" "}
        <span className="text-muted-foreground tabular-nums">
          {r.latency_ms != null ? `${r.latency_ms.toFixed(0)}ms` : ""}
        </span>
      </span>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-red-700"
      title={r.error ?? "unknown error"}
    >
      <XCircle className="h-3.5 w-3.5" />
      Failed
    </span>
  );
}
