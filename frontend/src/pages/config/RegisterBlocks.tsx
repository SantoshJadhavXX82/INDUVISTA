/**
 * Register Blocks sub-page — list + drawer create/edit/delete.
 *
 * A register block defines a contiguous Modbus address range polled in a
 * single read. Tags within a device's blocks share the device's polling
 * cadence. function_code = 1/2/3/4 (coils, discrete inputs, holding
 * registers, input registers).
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2, AlertCircle, Upload, Download } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { cn } from "@/lib/utils";
import { type BulkResult } from "@/types/api";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Drawer } from "@/components/ui/drawer";
import { HelpTip } from "@/components/ui/help-tip";
import { help } from "@/lib/help-text";
import { DeviceTabs } from "@/components/ui/device-tabs";
import { CsvImportContent, type ImportRowResult, exportCsv } from "@/components/ui/csv-import";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

type RegisterBlock = {
  id: number;
  name: string;
  device_id: number;
  device_name: string;
  function_code: number;
  start_address: number;
  count: number;
  scan_interval_ms: number | null;
  phase_ms: number | null;
  enabled: boolean;
  // Phase 8.5.1 — engineering writability flag
  writable: boolean;
};

type Device = { id: number; name: string };

const FC_LABELS: Record<number, string> = {
  1: "FC1 — Coils (read bools)",
  2: "FC2 — Discrete inputs (read bools)",
  3: "FC3 — Holding registers (read/write words)",
  4: "FC4 — Input registers (read words)",
};

export default function RegisterBlocks() {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState<RegisterBlock | "new" | null>(null);
  const [activeDeviceId, setActiveDeviceId] = useState<number | null>(null);
  const [importing, setImporting] = useState(false);

  const blocks = useQuery({
    queryKey: ["register-blocks"],
    queryFn: () => api.get<RegisterBlock[]>("/register-blocks"),
  });

  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: () => api.get<Device[]>("/devices"),
    staleTime: 60_000,
  });

  const countsByDevice = useMemo(() => {
    const counts: Record<number | "all", number> = { all: blocks.data?.length ?? 0 };
    blocks.data?.forEach((b) => {
      counts[b.device_id] = (counts[b.device_id] ?? 0) + 1;
    });
    return counts;
  }, [blocks.data]);

  const filteredBlocks = useMemo(() => {
    if (!blocks.data) return [];
    if (activeDeviceId === null) return blocks.data;
    return blocks.data.filter((b) => b.device_id === activeDeviceId);
  }, [blocks.data, activeDeviceId]);

  return (
    <div className="space-y-3">
      <DeviceTabs
        devices={devices.data ?? []}
        value={activeDeviceId}
        onChange={setActiveDeviceId}
        counts={countsByDevice}
      />

      <div className="flex justify-between items-center">
        <span className="text-sm text-muted-foreground">
          {blocks.data ? `${filteredBlocks.length} of ${blocks.data.length} register blocks` : "Loading…"}
        </span>
        <div className="flex gap-2">
          <Button onClick={() => exportBlocks(filteredBlocks)} size="sm" variant="outline">
            <Download className="h-4 w-4 mr-1.5" />
            Export CSV
          </Button>
          <Button onClick={() => setImporting(true)} size="sm" variant="outline">
            <Upload className="h-4 w-4 mr-1.5" />
            Import CSV
          </Button>
          <Button onClick={() => setEditing("new")} size="sm">
            <Plus className="h-4 w-4 mr-1.5" />
            Add block
          </Button>
        </div>
      </div>

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
                <TableHead>Device</TableHead>
                <TableHead className="text-right">FC</TableHead>
                <TableHead className="text-right">Start</TableHead>
                <TableHead className="text-right">Count</TableHead>
                <TableHead>Status</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filteredBlocks.map((b) => (
                <TableRow
                  key={b.id}
                  onClick={() => setEditing(b)}
                  className="cursor-pointer"
                >
                  <TableCell className="font-medium">{b.name}</TableCell>
                  <TableCell className="text-xs">{b.device_name}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">{b.function_code}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">{b.start_address}</TableCell>
                  <TableCell className="text-right tabular-nums text-xs">{b.count}</TableCell>
                  <TableCell>
                    <Badge variant={b.enabled ? "success" : "secondary"} className="text-xs">
                      {b.enabled ? "enabled" : "disabled"}
                    </Badge>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Drawer
        open={editing !== null}
        onClose={() => setEditing(null)}
        title={editing === "new" ? "New register block" : `Block: ${editing && editing !== "new" ? editing.name : ""}`}
      >
        {editing !== null && (
          <BlockForm
            block={editing === "new" ? null : editing}
            devices={devices.data ?? []}
            onDone={() => {
              queryClient.invalidateQueries({ queryKey: ["register-blocks"] });
              setEditing(null);
            }}
          />
        )}
      </Drawer>

      <Drawer
        open={importing}
        onClose={() => setImporting(false)}
        title="Import register blocks from CSV"
        size="lg"
      >
        {importing && (
          <CsvImportContent
            expectedColumns={[
              "name", "device_name", "function_code",
              "start_address", "count",
              "scan_interval_ms", "phase_ms",
              "access",
            ]}
            requiredColumns={["name", "device_name", "function_code", "start_address", "count"]}
            templateCsv={
              "name,device_name,function_code,start_address,count,scan_interval_ms,phase_ms,access\n" +
              "MyBlock_HR_0_30,FLOWCOMP_001,3,0,30,1000,0,Read-only\n" +
              "MyBlock_HR_setpoints,FLOWCOMP_001,3,400,10,1000,0,Read+Write\n"
            }
            templateFilename="register-blocks-template.csv"
            onImport={async (rows) => {
              const deviceByName: Record<string, number> = {};
              devices.data?.forEach((d) => { deviceByName[d.name] = d.id; });

              const preFlight: ImportRowResult[] = [];
              const valid: any[] = [];
              const validIndexes: number[] = [];

              rows.forEach((row, i) => {
                const did = deviceByName[row.device_name];
                if (did === undefined) {
                  preFlight.push({
                    row: i,
                    success: false,
                    message: `device '${row.device_name}' not found`,
                  });
                  return;
                }
                const fc = parseInt(row.function_code, 10);
                // Phase 8.5.1 — parse access column.
                // Accept friendly labels: "Read-only" / "Read+Write" / "RW" / "RO"
                // Also accept booleans: "true" / "false" / "1" / "0"
                // DI/IR (FC 2/4) silently force RO regardless of column value.
                const accessRaw = (row.access ?? "").trim().toLowerCase();
                let writable = false;
                if (fc === 1 || fc === 3) {
                  if (["read+write", "readwrite", "rw", "true", "1", "yes"].includes(accessRaw)) {
                    writable = true;
                  }
                }
                valid.push({
                  device_id: did,
                  name: row.name,
                  function_code: fc,
                  start_address: parseInt(row.start_address, 10),
                  count: parseInt(row.count, 10),
                  scan_interval_ms: row.scan_interval_ms ? parseInt(row.scan_interval_ms, 10) : 1000,
                  phase_ms: row.phase_ms ? parseInt(row.phase_ms, 10) : 0,
                  writable,
                });
                validIndexes.push(i);
              });

              if (valid.length === 0) return preFlight;

              const serverResults = await api.post<BulkResult[]>(
                "/register-blocks/bulk",
                { blocks: valid },
              );
              const merged: ImportRowResult[] = [...preFlight];
              serverResults.forEach((sr, j) => {
                merged.push({
                  row: validIndexes[j],
                  success: !sr.error,
                  message: sr.error ?? undefined,
                });
              });
              merged.sort((a, b) => a.row - b.row);
              queryClient.invalidateQueries({ queryKey: ["register-blocks"] });
              return merged;
            }}
          />
        )}
      </Drawer>
    </div>
  );
}

// --------------------------------------------------------------------------

type FormState = {
  name: string;
  device_id: string;
  function_code: string;
  start_address: string;
  count: string;
  enabled: boolean;
  // Phase 8.5 — per-block poll interval. Empty string = inherit from device.
  scan_interval_ms: string;
  // Phase 8.5.1 — engineering writability flag. Only meaningful for FC 1/3.
  writable: boolean;
};

/** Convert FC numeric → friendly area label. */
const AREA_LABEL: Record<string, string> = {
  "1": "Coil",
  "2": "Discrete Input",
  "3": "Holding Register",
  "4": "Input Register",
};

/** Is this area RW-capable per Modbus spec? */
function fcAllowsWrite(fc: string): boolean {
  return fc === "1" || fc === "3";
}

function BlockForm({
  block,
  devices,
  onDone,
}: {
  block: RegisterBlock | null;
  devices: Device[];
  onDone: () => void;
}) {
  const isNew = block === null;
  const [form, setForm] = useState<FormState>({
    name: block?.name ?? "",
    device_id: block ? String(block.device_id) : (devices[0] ? String(devices[0].id) : ""),
    function_code: block ? String(block.function_code) : "3",
    start_address: block ? String(block.start_address) : "0",
    count: block ? String(block.count) : "10",
    enabled: block?.enabled ?? true,
    // Phase 8.5: null in DB = inherit from device; empty string in form = same
    scan_interval_ms: block?.scan_interval_ms != null ? String(block.scan_interval_ms) : "",
    writable: block?.writable ?? false,
  });
  const [error, setError] = useState<string | null>(null);
  const [deleteConfirm, setDeleteConfirm] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      const blockInterval = form.scan_interval_ms.trim() === ""
        ? null
        : parseInt(form.scan_interval_ms, 10);
      // Phase 8.5.1 — guard: writable=true on DI/IR is rejected by DB CHECK,
      // so flip it off client-side if the user changed area to DI/IR.
      const writable = fcAllowsWrite(form.function_code) ? form.writable : false;
      if (isNew) {
        return api.post("/register-blocks", {
          name: form.name,
          device_id: parseInt(form.device_id, 10),
          function_code: parseInt(form.function_code, 10),
          start_address: parseInt(form.start_address, 10),
          count: parseInt(form.count, 10),
          scan_interval_ms: blockInterval,
          writable,
        });
      }
      return api.patch(`/register-blocks/${block.id}`, {
        name: form.name,
        count: parseInt(form.count, 10),
        enabled: form.enabled,
        scan_interval_ms: blockInterval,
        writable,
      });
    },
    onSuccess: onDone,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  const remove = useMutation({
    mutationFn: () => api.delete(`/register-blocks/${block!.id}`),
    onSuccess: onDone,
    onError: (e: Error) => setError(e instanceof ApiError ? e.detail : e.message),
  });

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        save.mutate();
      }}
      className="space-y-4"
    >
      <div className="space-y-1.5">
        <Label htmlFor="name">
          Name <HelpTip entry={help.block.name} />
        </Label>
        <Input
          id="name"
          required
          value={form.name}
          onChange={(e) => setForm({ ...form, name: e.target.value })}
        />
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="device">
          Device <HelpTip entry={help.block.device} />
          {!isNew && <span className="normal-case text-muted-foreground"> (immutable)</span>}
        </Label>
        <select
          id="device"
          disabled={!isNew}
          value={form.device_id}
          onChange={(e) => setForm({ ...form, device_id: e.target.value })}
          className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50"
        >
          {devices.map((d) => (
            <option key={d.id} value={d.id}>{d.name}</option>
          ))}
        </select>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="function_code">
            Register area <HelpTip entry={help.block.function_code} />
            {!isNew && <span className="normal-case text-muted-foreground"> (immutable)</span>}
          </Label>
          <select
            id="function_code"
            disabled={!isNew}
            value={form.function_code}
            onChange={(e) => {
              const fc = e.target.value;
              // If switching to a read-only area, automatically clear writable
              setForm({
                ...form,
                function_code: fc,
                writable: fcAllowsWrite(fc) ? form.writable : false,
              });
            }}
            className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm disabled:opacity-50"
          >
            <option value="1">Coil</option>
            <option value="2">Discrete Input</option>
            <option value="3">Holding Register</option>
            <option value="4">Input Register</option>
          </select>
          <p className="text-xs text-muted-foreground">
            {form.function_code === "2" || form.function_code === "4"
              ? "Read-only per Modbus spec."
              : "Supports both read and write."}
          </p>
        </div>

        <div className="space-y-1.5">
          <Label>Access</Label>
          <div className="flex flex-col gap-2 pt-1">
            <label className={cn(
              "flex items-center gap-2 text-sm cursor-pointer",
              !fcAllowsWrite(form.function_code) && "text-muted-foreground",
            )}>
              <input
                type="radio"
                name="access"
                checked={!form.writable}
                onChange={() => setForm({ ...form, writable: false })}
                className="h-4 w-4"
              />
              <span>Read-only</span>
            </label>
            <label className={cn(
              "flex items-center gap-2 text-sm cursor-pointer",
              !fcAllowsWrite(form.function_code) && "opacity-40 cursor-not-allowed",
            )}>
              <input
                type="radio"
                name="access"
                checked={form.writable}
                disabled={!fcAllowsWrite(form.function_code)}
                onChange={() => setForm({ ...form, writable: true })}
                className="h-4 w-4"
              />
              <span>Read + Write</span>
              {form.writable && (
                <span className="text-[10px] text-green-700 font-semibold uppercase ml-1">
                  enabled
                </span>
              )}
            </label>
          </div>
          {!fcAllowsWrite(form.function_code) && (
            <p className="text-xs text-muted-foreground">
              Discrete Input and Input Register are always read-only.
            </p>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="start_address">
            Start address <HelpTip entry={help.block.start_address} />
            {!isNew && <span className="normal-case text-muted-foreground"> (immutable)</span>}
          </Label>
          <Input
            id="start_address"
            type="number"
            required
            min="0"
            disabled={!isNew}
            value={form.start_address}
            onChange={(e) => setForm({ ...form, start_address: e.target.value })}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="count">
            Count <HelpTip entry={help.block.register_count} />
          </Label>
          <Input
            id="count"
            type="number"
            required
            min="1"
            max="125"
            value={form.count}
            onChange={(e) => setForm({ ...form, count: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="block_scan_interval_ms">
          Poll interval (ms) — optional
        </Label>
        <Input
          id="block_scan_interval_ms"
          type="number"
          min="50"
          placeholder="(inherit from device)"
          value={form.scan_interval_ms}
          onChange={(e) => setForm({ ...form, scan_interval_ms: e.target.value })}
        />
        <p className="text-xs text-muted-foreground">
          Per-block polling cadence. Leave blank to use the device's
          scan_interval_ms. Useful for mixing fast totalizers with slow
          chromatograph blocks on the same device.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="enabled">
          Status <HelpTip entry={help.block.enabled} />
        </Label>
        <label className="flex items-center gap-2 h-9 text-sm">
          <input
            id="enabled"
            type="checkbox"
            checked={form.enabled}
            onChange={(e) => setForm({ ...form, enabled: e.target.checked })}
            className="h-4 w-4"
          />
          <span className="text-muted-foreground">
            {form.enabled ? "polled" : "skipped"}
          </span>
        </label>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-800 flex gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : isNew ? "Create block" : "Save changes"}
        </Button>
      </div>

      {!isNew && (
        <section className="pt-4 border-t border-red-100">
          <h3 className="text-sm font-semibold text-red-700">Delete block</h3>
          <p className="text-xs text-muted-foreground mt-1 mb-2">
            Tags will keep existing but their register_block_id becomes null
            (they'll need to be reassigned or disabled). Type{" "}
            <code className="font-mono bg-secondary px-1 rounded">{block.name}</code> to confirm.
          </p>
          <div className="flex gap-2">
            <Input
              value={deleteConfirm}
              onChange={(e) => setDeleteConfirm(e.target.value)}
              placeholder={block.name}
              className="flex-1"
            />
            <Button
              type="button"
              variant="outline"
              disabled={deleteConfirm !== block.name || remove.isPending}
              onClick={() => remove.mutate()}
              className="border-red-200 text-red-700 hover:bg-red-50"
            >
              <Trash2 className="h-4 w-4 mr-1.5" />
              {remove.isPending ? "Deleting…" : "Delete"}
            </Button>
          </div>
        </section>
      )}
    </form>
  );
}

// --------------------------------------------------------------------------
// CSV export — same column order as the import template so it round-trips
// --------------------------------------------------------------------------

function exportBlocks(blocks: RegisterBlock[]): void {
  const stamp = filenameStamp();
  exportCsv<RegisterBlock>(blocks, [
    { header: "name", value: (b) => b.name },
    { header: "device_name", value: (b) => b.device_name },
    { header: "function_code", value: (b) => b.function_code },
    { header: "start_address", value: (b) => b.start_address },
    { header: "count", value: (b) => b.count },
    { header: "scan_interval_ms", value: (b) => b.scan_interval_ms },
    { header: "phase_ms", value: (b) => b.phase_ms },
    // Phase 8.5.1 — engineering access. Friendly label exported; importer
    // accepts either friendly label or boolean.
    { header: "access", value: (b) => b.writable ? "Read+Write" : "Read-only" },
  ], `induvista-register-blocks-${stamp}.csv`);
}

function filenameStamp(): string {
  const d = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
}
